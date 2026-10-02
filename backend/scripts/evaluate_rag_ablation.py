# -*- coding: utf-8 -*-
"""
Runs the RAG ablation study: for every question in rag_ablation_testset.csv,
generates an answer WITH retrieval turned on (RAG) and WITH IT TURNED OFF
(the same LLM, same question, but no retrieved context), then has the LLM
judge each answer against the REAL source passage it came from.

This is the evidence RQ2 asks for: does retrieval actually make answers more
accurate/grounded, or would the same LLM do just as well on its own?

For every question the script saves:
  - the two answers and the two verdicts of the judge,
  - which answer the judge saw first (the order is random but reproducible),
  - the identifiers of the passages that retrieval gave to the model,
  - whether the source passage of the question was among them, and its
    position in the ranking of the retriever (looking at the first 10).

The last two items make it possible to measure how often retrieval finds the
right passage, separately from how well the model uses it.

All 50 questions are answered with one and the same configuration. A result
that does not contain the retrieved passages (produced by an earlier version
of this script) is generated again, so that the whole test set is consistent.

Standalone script (does not import app.py) - same reasoning as
evaluate_escalation_guard.py: app.py runs Streamlit-only setup code on
import, which crashes outside `streamlit run`.

Safe to close/interrupt and re-run: saves progress after every question and
skips the ones already done.

Run from anywhere (same pattern as the other backend/scripts/*.py files):
    python backend\\scripts\\evaluate_rag_ablation.py

Output ("rag_ablation_results" folder in the project root):
    rag_ablation_raw.json      - every question with both answers + verdicts
    rag_ablation_summary.json  - the final comparison as plain numbers
    rag_ablation_table.md      - the same comparison as a markdown table
"""

import csv
import json
import random
import time
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Anchored to the project root instead of a hardcoded absolute path. This file
# lives in backend/scripts/, so the project root is THREE levels up from here
# (scripts -> backend -> project root).
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CHROMA_DB_PATH = PROJECT_ROOT / "chroma_db"
TESTSET_PATH = PROJECT_ROOT / "backend" / "scripts" / "rag_ablation_testset.csv"
RESULTS_DIR = PROJECT_ROOT / "rag_ablation_results"
RAW_PATH = RESULTS_DIR / "rag_ablation_raw.json"

K = 2             # passages given to the model in the retrieval condition
RANK_DEPTH = 10   # how far down the ranking we look for the source passage
JUDGE_SEED = 23   # together with the test_id, decides which answer the judge sees first
ANSWER_INSTRUCTION = "(Answer in 2-3 concise sentences.)"

MAX_ATTEMPTS = 3          # attempts for an ordinary error of the provider
RETRY_PAUSE = 5           # seconds between those attempts
RATE_LIMIT_WAIT = 65      # seconds to wait when the provider says "too many requests"
MAX_RATE_LIMIT_WAITS = 4  # after this many waits the run stops (progress is saved)


class ProviderLimitReached(Exception):
    """The provider keeps refusing requests: stop now and continue later."""


def setup():
    """Loads the models and the vector index. Kept in a function so the rest of
    the file can be imported and tested without the providers installed."""
    import chromadb
    from llama_index.core import VectorStoreIndex, Settings
    from llama_index.vector_stores.chroma import ChromaVectorStore
    from llama_index.embeddings.ollama import OllamaEmbedding
    from llama_index.llms.groq import Groq

    Settings.embed_model = OllamaEmbedding(model_name="nomic-embed-text")
    Settings.llm = Groq(model="openai/gpt-oss-120b")
    client = chromadb.PersistentClient(path=str(CHROMA_DB_PATH))
    collection = client.get_or_create_collection("rag_collection")
    vector_store = ChromaVectorStore(chroma_collection=collection)
    index = VectorStoreIndex.from_vector_store(vector_store)
    return index, Settings.llm


# ----------------------------------------------------------------------
# Calls to the provider, with waiting and retrying
# ----------------------------------------------------------------------
def is_rate_limit_error(exc):
    text = f"{type(exc).__name__} {exc}".lower()
    return any(s in text for s in ("429", "rate limit", "rate_limit", "ratelimit",
                                   "too many requests", "quota"))


def call_with_retries(function, sleep=time.sleep):
    """Runs function(). Waits and tries again when the provider is rate-limiting,
    and repeats a few times on any other error."""
    attempts = 0
    rate_waits = 0
    while True:
        try:
            return function()
        except Exception as e:  # noqa: BLE001
            if is_rate_limit_error(e):
                rate_waits += 1
                if rate_waits > MAX_RATE_LIMIT_WAITS:
                    raise ProviderLimitReached(str(e)) from e
                print(f"    provider is rate-limiting, waiting {RATE_LIMIT_WAIT}s "
                      f"({rate_waits}/{MAX_RATE_LIMIT_WAITS}) ...")
                sleep(RATE_LIMIT_WAIT)
                continue
            attempts += 1
            if attempts >= MAX_ATTEMPTS:
                raise
            sleep(RETRY_PAUSE)


# ----------------------------------------------------------------------
# The two conditions
# ----------------------------------------------------------------------
def retrieval_query(question):
    return f"{question}\n\n{ANSWER_INSTRUCTION}"


def normalize(text):
    return " ".join((text or "").split())


def is_source(node_with_score, chunk_id, source_chunk):
    """True if a retrieved passage is the passage the question was written from."""
    node = node_with_score.node
    return node.node_id == chunk_id or normalize(node.get_content()) == normalize(source_chunk)


def answer_with_rag(index, question):
    """One generation call on the K retrieved passages.
    Returns the answer and the passages that were given to the model."""
    query_engine = index.as_query_engine(similarity_top_k=K, response_mode="simple_summarize")
    response = query_engine.query(retrieval_query(question))
    return str(response).strip(), list(response.source_nodes)


def answer_without_rag(llm, question):
    prompt = (
        "You are a helpful medical assistant chatbot. Answer the patient's question as "
        "best you can, in 2-3 concise sentences.\n\n"
        f"Question: {question}"
    )
    return str(llm.complete(prompt)).strip()


def rank_of_source(index, question, chunk_id, source_chunk):
    """Position of the source passage among the first RANK_DEPTH passages returned
    by the retriever for this question (1 = first), or None if it is not there.
    This uses only the embedding model, no call to the language model."""
    retriever = index.as_retriever(similarity_top_k=RANK_DEPTH)
    nodes = retriever.retrieve(retrieval_query(question))
    for rank, node_with_score in enumerate(nodes, start=1):
        if is_source(node_with_score, chunk_id, source_chunk):
            return rank
    return None


# ----------------------------------------------------------------------
# The judge
# ----------------------------------------------------------------------
def rag_is_shown_first(test_id):
    """Random but reproducible: depends only on the seed and on the question's id."""
    return random.Random(f"{JUDGE_SEED}-{test_id}").random() < 0.5


def parse_judge(response):
    """Returns the verdicts for Answer 1 and Answer 2, in that order.
    Anything that cannot be read as CONSISTENT counts as INCONSISTENT."""
    v1, v2 = "INCONSISTENT", "INCONSISTENT"
    for line in response.splitlines():
        line_up = line.strip().upper()
        verdict = "CONSISTENT" if ("CONSISTENT" in line_up and "INCONSISTENT" not in line_up) else "INCONSISTENT"
        if line_up.startswith("ANSWER 1"):
            v1 = verdict
        elif line_up.startswith("ANSWER 2"):
            v2 = verdict
    return v1, v2


def judge(llm, source_chunk, question, rag_answer, no_rag_answer, rag_first):
    """Asks the LLM to grade both answers against the real source passage. The judge
    only sees "Answer 1" and "Answer 2", so it cannot know which one used retrieval.
    Returns (rag_verdict, no_rag_verdict, raw_response)."""
    first_answer, second_answer = (rag_answer, no_rag_answer) if rag_first else (no_rag_answer, rag_answer)
    prompt = (
        "You are grading two candidate answers to a patient's medical question, using the "
        "REFERENCE PASSAGE below as the ONLY source of truth. Do not use outside knowledge "
        "to override it.\n\n"
        f"REFERENCE PASSAGE:\n{source_chunk}\n\n"
        f"QUESTION: {question}\n\n"
        f"ANSWER 1:\n{first_answer}\n\n"
        f"ANSWER 2:\n{second_answer}\n\n"
        "For EACH answer, judge whether it is CONSISTENT with the reference passage "
        "(correct and specific, matching the passage's actual facts) or INCONSISTENT "
        "(wrong, contradicts the passage, or too vague/generic to reflect its specific facts).\n\n"
        "Respond in exactly this format and nothing else:\n"
        "ANSWER 1: CONSISTENT or INCONSISTENT\n"
        "ANSWER 2: CONSISTENT or INCONSISTENT"
    )
    response = str(llm.complete(prompt)).strip()
    v1, v2 = parse_judge(response)
    # v1 belongs to whichever answer was shown first, v2 to the other one.
    rag_verdict, no_rag_verdict = (v1, v2) if rag_first else (v2, v1)
    return rag_verdict, no_rag_verdict, response


# ----------------------------------------------------------------------
# Results on disk
# ----------------------------------------------------------------------
def load_existing_results():
    if RAW_PATH.exists():
        with open(RAW_PATH, encoding="utf-8") as f:
            return json.load(f)
    return []


def save_results(results):
    RESULTS_DIR.mkdir(exist_ok=True)
    tmp = RAW_PATH.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    tmp.replace(RAW_PATH)   # atomic: a crash can never leave a half-written results file


def is_complete(result):
    """A result is complete when it contains the passages that were retrieved."""
    return "retrieved_chunk_ids" in result


def evaluate_question(index, llm, row, sleep):
    question = row["question"]
    rag_first = rag_is_shown_first(row["test_id"])

    rag_answer, source_nodes = call_with_retries(lambda: answer_with_rag(index, question), sleep)
    no_rag_answer = call_with_retries(lambda: answer_without_rag(llm, question), sleep)
    v_rag, v_no_rag, raw = call_with_retries(
        lambda: judge(llm, row["source_chunk"], question, rag_answer, no_rag_answer, rag_first), sleep)
    rank = call_with_retries(
        lambda: rank_of_source(index, question, row["chunk_id"], row["source_chunk"]), sleep)

    return {
        "test_id": row["test_id"],
        "chunk_id": row["chunk_id"],
        "source_chunk": row["source_chunk"],
        "question": question,
        "rag_answer": rag_answer,
        "no_rag_answer": no_rag_answer,
        "rag_verdict": v_rag,
        "no_rag_verdict": v_no_rag,
        "raw_judge_response": raw,
        "rag_shown_first": rag_first,
        "retrieved_chunk_ids": [n.node.node_id for n in source_nodes],
        "source_retrieved": any(is_source(n, row["chunk_id"], row["source_chunk"]) for n in source_nodes),
        "source_rank": rank,
    }


# ----------------------------------------------------------------------
# Summary
# ----------------------------------------------------------------------
def write_summary(results):
    n = len(results)
    consistent = lambda r, key: r[key] == "CONSISTENT"  # noqa: E731
    rag_correct = sum(consistent(r, "rag_verdict") for r in results)
    no_rag_correct = sum(consistent(r, "no_rag_verdict") for r in results)
    both = sum(consistent(r, "rag_verdict") and consistent(r, "no_rag_verdict") for r in results)
    rag_only = rag_correct - both
    no_rag_only = no_rag_correct - both

    hits = [r for r in results if r["source_retrieved"]]
    misses = [r for r in results if not r["source_retrieved"]]
    ranks = [r["source_rank"] for r in results]

    summary = {
        "n": n,
        "k": K,
        "rag_consistent": rag_correct,
        "rag_consistency_rate": round(rag_correct / n, 4) if n else 0,
        "no_rag_consistent": no_rag_correct,
        "no_rag_consistency_rate": round(no_rag_correct / n, 4) if n else 0,
        "both_consistent": both,
        "only_rag_consistent": rag_only,
        "only_no_rag_consistent": no_rag_only,
        "neither_consistent": n - both - rag_only - no_rag_only,
        "rag_shown_first": sum(1 for r in results if r["rag_shown_first"]),
        "source_passage_retrieved": len(hits),
        "rag_consistent_when_retrieved": sum(consistent(r, "rag_verdict") for r in hits),
        "rag_consistent_when_not_retrieved": sum(consistent(r, "rag_verdict") for r in misses),
        "no_rag_consistent_when_retrieved": sum(consistent(r, "no_rag_verdict") for r in hits),
        "no_rag_consistent_when_not_retrieved": sum(consistent(r, "no_rag_verdict") for r in misses),
        "source_in_top": {str(depth): sum(1 for rank in ranks if rank is not None and rank <= depth)
                          for depth in (1, 2, 3, 5, 10)},
    }
    with open(RESULTS_DIR / "rag_ablation_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    table = (
        "| Metric | RAG ON | RAG OFF |\n|---|---|---|\n"
        f"| Test questions (n) | {summary['n']} | {summary['n']} |\n"
        f"| Answers consistent with source | {summary['rag_consistent']} | {summary['no_rag_consistent']} |\n"
        f"| Consistency rate | {summary['rag_consistency_rate']*100:.1f}% | "
        f"{summary['no_rag_consistency_rate']*100:.1f}% |\n"
        f"| Consistent in this condition only | {summary['only_rag_consistent']} | "
        f"{summary['only_no_rag_consistent']} |\n"
        f"| Consistent when the source passage was retrieved ({len(hits)} questions) | "
        f"{summary['rag_consistent_when_retrieved']} | {summary['no_rag_consistent_when_retrieved']} |\n"
        f"| Consistent when it was not retrieved ({len(misses)} questions) | "
        f"{summary['rag_consistent_when_not_retrieved']} | {summary['no_rag_consistent_when_not_retrieved']} |\n"
    )
    with open(RESULTS_DIR / "rag_ablation_table.md", "w", encoding="utf-8") as f:
        f.write(table)

    extra = (
        f"\nSource passage among the {K} passages given to the model: {len(hits)}/{n}\n"
        "Source passage within the first 1 / 2 / 3 / 5 / 10 of the ranking: "
        + " / ".join(str(summary["source_in_top"][d]) for d in ("1", "2", "3", "5", "10")) + "\n"
        f"Both consistent: {both}   only RAG: {rag_only}   only no-RAG: {no_rag_only}   "
        f"neither: {summary['neither_consistent']}\n"
        f"RAG answer shown first to the judge: {summary['rag_shown_first']}/{n}\n"
    )
    return table + extra


def main(index=None, llm=None, sleep=time.sleep):
    with open(TESTSET_PATH, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    print(f"Loaded {len(rows)} test questions from {TESTSET_PATH}")

    if index is None or llm is None:
        index, llm = setup()

    by_id = {r["test_id"]: r for r in load_existing_results()}
    todo = [row for row in rows if not is_complete(by_id.get(row["test_id"], {}))]
    if todo:
        print(f"{len(todo)} question(s) to evaluate ({len(rows) - len(todo)} already complete).")

    for n_done, row in enumerate(todo, start=1):
        try:
            result = evaluate_question(index, llm, row, sleep)
        except ProviderLimitReached:
            print("\n[!] The provider is not accepting more requests right now.\n"
                  "    Everything done so far is saved. Run the same command again later to continue.")
            break
        except Exception as e:  # noqa: BLE001
            print(f"[{n_done}/{len(todo)}] ERROR on test_id {row['test_id']}: {e}  (will retry next run)")
            continue

        by_id[row["test_id"]] = result
        # Always saved in the order of the test set.
        save_results([by_id[r["test_id"]] for r in rows if r["test_id"] in by_id])
        rank = result["source_rank"] if result["source_rank"] is not None else f">{RANK_DEPTH}"
        print(f"[{n_done}/{len(todo)}] test_id {row['test_id']}: RAG={result['rag_verdict']:12s} "
              f"No-RAG={result['no_rag_verdict']:12s} source passage rank: {rank}")
        sleep(0.3)

    print("=" * 70)
    results = [by_id[r["test_id"]] for r in rows if r["test_id"] in by_id]
    remaining = sum(1 for row in rows if not is_complete(by_id.get(row["test_id"], {})))
    if remaining:
        print(f"[!] {remaining} question(s) are not finished yet. The summary files were NOT updated.\n"
              "    Run the same command again to finish.")
        return
    print(write_summary(results))
    print(f"Saved detailed results to: {RESULTS_DIR}")


if __name__ == "__main__":
    main()
# -*- coding: utf-8 -*-
"""
Runs the RAG ablation study: for every question in rag_ablation_testset.csv,
generates an answer WITH retrieval turned on (RAG) and WITH IT TURNED OFF
(the same LLM, same question, but no retrieved context), then has the LLM
judge each answer against the REAL source passage it came from.

This is the evidence RQ2 asks for: does retrieval actually make answers more
accurate/grounded, or would the same LLM do just as well on its own?

VERSION 2 - what changed and why
--------------------------------
The first version had a bug in the step that hands the judge's two verdicts
back to the two conditions. The judge sees the two answers in a random order
("Answer 1" / "Answer 2"). When the no-RAG answer was shown first, the two
verdicts were handed back the wrong way round, so for those questions the RAG
verdict was stored as the no-RAG verdict and vice versa.

This version:
  1. assigns the verdicts correctly,
  2. stores, for every question, which answer was shown first
     ("rag_shown_first"), so the result can always be checked afterwards,
  3. decides the order from the question's own test_id (not from how many
     questions were processed before it), so the order is the same no matter
     how many times the script is stopped and restarted,
  4. automatically RE-JUDGES the questions that were evaluated by the first
     version. It reuses the answers that are already saved (it does not
     generate them again), so this only costs one judge call per question.

Standalone script (does not import app.py) - same reasoning as
evaluate_escalation_guard.py: app.py runs Streamlit-only setup code on
import, which crashes outside `streamlit run`.

Safe to close/interrupt and re-run: saves progress after every question and
skips ones already done.

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

JUDGE_SEED = 23   # together with the test_id, decides which answer the judge sees first


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


def answer_with_rag(index, question, k=2):
    # k=2 and response_mode="simple_summarize" (a single LLM call) - unchanged from version 1.
    query_engine = index.as_query_engine(similarity_top_k=k, response_mode="simple_summarize")
    response = query_engine.query(f"{question}\n\n(Answer in 2-3 concise sentences.)")
    return str(response).strip()


def answer_without_rag(llm, question):
    prompt = (
        "You are a helpful medical assistant chatbot. Answer the patient's question as "
        "best you can, in 2-3 concise sentences.\n\n"
        f"Question: {question}"
    )
    return str(llm.complete(prompt)).strip()


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


def needs_rejudging(result):
    return "rag_shown_first" not in result


def write_summary(results):
    n = len(results)
    rag_correct = sum(1 for r in results if r["rag_verdict"] == "CONSISTENT")
    no_rag_correct = sum(1 for r in results if r["no_rag_verdict"] == "CONSISTENT")
    both = sum(1 for r in results if r["rag_verdict"] == "CONSISTENT" and r["no_rag_verdict"] == "CONSISTENT")
    rag_only = rag_correct - both
    no_rag_only = no_rag_correct - both

    summary = {
        "n": n,
        "rag_consistent": rag_correct,
        "rag_consistency_rate": round(rag_correct / n, 4) if n else 0,
        "no_rag_consistent": no_rag_correct,
        "no_rag_consistency_rate": round(no_rag_correct / n, 4) if n else 0,
        "both_consistent": both,
        "only_rag_consistent": rag_only,
        "only_no_rag_consistent": no_rag_only,
        "neither_consistent": n - both - rag_only - no_rag_only,
        "rag_shown_first": sum(1 for r in results if r["rag_shown_first"]),
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
    )
    with open(RESULTS_DIR / "rag_ablation_table.md", "w", encoding="utf-8") as f:
        f.write(table)
    return table


def main(index=None, llm=None, sleep=time.sleep):
    with open(TESTSET_PATH, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    print(f"Loaded {len(rows)} test questions from {TESTSET_PATH}")

    if index is None or llm is None:
        index, llm = setup()

    results = load_existing_results()

    # ---- Step 1: re-judge the questions evaluated by version 1 (answers are reused) ----
    old = [r for r in results if needs_rejudging(r)]
    if old:
        print(f"{len(old)} question(s) were judged by the first version of this script.")
        print("Re-judging them now, using the answers that are already saved ...")
    for n_done, r in enumerate(old, start=1):
        rag_first = rag_is_shown_first(r["test_id"])
        try:
            v_rag, v_no_rag, raw = judge(llm, r["source_chunk"], r["question"],
                                         r["rag_answer"], r["no_rag_answer"], rag_first)
        except Exception as e:  # noqa: BLE001
            print(f"[re-judge {n_done}/{len(old)}] ERROR on test_id {r['test_id']}: {e}  (will retry next run)")
            continue
        r["rag_verdict_v1"] = r["rag_verdict"]          # kept only as a record of the old, unreliable value
        r["no_rag_verdict_v1"] = r["no_rag_verdict"]
        r["rag_verdict"], r["no_rag_verdict"] = v_rag, v_no_rag
        r["raw_judge_response"] = raw
        r["rag_shown_first"] = rag_first
        save_results(results)
        print(f"[re-judge {n_done}/{len(old)}] test_id {r['test_id']}: RAG={v_rag:12s} No-RAG={v_no_rag:12s}")
        sleep(0.3)

    # ---- Step 2: evaluate the questions that have no result yet ----
    done_ids = {r["test_id"] for r in results}
    todo = [row for row in rows if row["test_id"] not in done_ids]
    if todo:
        print(f"{len(todo)} new question(s) to evaluate.")
    for n_done, row in enumerate(todo, start=1):
        rag_first = rag_is_shown_first(row["test_id"])
        try:
            rag_answer = answer_with_rag(index, row["question"])
            no_rag_answer = answer_without_rag(llm, row["question"])
            v_rag, v_no_rag, raw = judge(llm, row["source_chunk"], row["question"],
                                         rag_answer, no_rag_answer, rag_first)
        except Exception as e:  # noqa: BLE001
            print(f"[{n_done}/{len(todo)}] ERROR on test_id {row['test_id']}: {e}  (will retry next run)")
            continue

        results.append({
            **row,
            "rag_answer": rag_answer,
            "no_rag_answer": no_rag_answer,
            "rag_verdict": v_rag,
            "no_rag_verdict": v_no_rag,
            "raw_judge_response": raw,
            "rag_shown_first": rag_first,
        })
        save_results(results)
        print(f"[{n_done}/{len(todo)}] test_id {row['test_id']}: RAG={v_rag:12s} No-RAG={v_no_rag:12s}")
        sleep(0.3)

    # ---- Step 3: summary (only when every stored result is a version-2 result) ----
    print("=" * 70)
    still_old = sum(1 for r in results if needs_rejudging(r))
    missing = len(rows) - len(results)
    if still_old:
        print(f"[!] {still_old} question(s) still have to be re-judged. The summary files were NOT "
              "updated.\n    Run the same command again to finish.")
        return
    table = write_summary(results)
    print(table)
    if missing:
        print(f"[!] {missing} question(s) of the test set have no result yet (errors above). "
              "Run the same command again to finish them.")
    print(f"Saved detailed results to: {RESULTS_DIR}")


if __name__ == "__main__":
    main()
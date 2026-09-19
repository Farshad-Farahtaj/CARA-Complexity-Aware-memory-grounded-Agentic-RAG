# -*- coding: utf-8 -*-
"""
Runs the RAG ablation study: for every question in rag_ablation_testset.csv,
generates an answer WITH retrieval turned on (RAG) and WITH IT TURNED OFF
(the same LLM, same question, but no retrieved context), then has the LLM
judge each answer against the REAL source passage it came from.

This is the evidence RQ2 asks for: does retrieval actually make answers more
accurate/grounded, or would the same LLM do just as well on its own?

Standalone script (does not import app.py) - same reasoning as
evaluate_escalation_guard.py: app.py runs Streamlit-only setup code on
import, which crashes outside `streamlit run`.

Safe to close/interrupt and re-run: saves progress after every question and
skips ones already done.

Run from anywhere (same pattern as the other backend/scripts/*.py files):
    python backend\\scripts\\evaluate_rag_ablation.py

Output (new "rag_ablation_results" folder in the project root):
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
import chromadb
from llama_index.core import VectorStoreIndex, Settings
from llama_index.vector_stores.chroma import ChromaVectorStore
from llama_index.embeddings.ollama import OllamaEmbedding
from llama_index.llms.groq import Groq

load_dotenv()
Settings.embed_model = OllamaEmbedding(model_name="nomic-embed-text")
Settings.llm = Groq(model="openai/gpt-oss-120b")

# Anchored to the project root instead of a hardcoded absolute path - same fix
# already applied to ingest.py. This file lives in backend/scripts/, so the
# project root is THREE levels up from here (scripts -> backend -> project
# root). Makes the script work on any machine/path, not just this one.
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CHROMA_DB_PATH = PROJECT_ROOT / "chroma_db"
TESTSET_PATH = PROJECT_ROOT / "backend" / "scripts" / "rag_ablation_testset.csv"
RESULTS_DIR = PROJECT_ROOT / "rag_ablation_results"
RESULTS_DIR.mkdir(exist_ok=True)
RAW_PATH = RESULTS_DIR / "rag_ablation_raw.json"

JUDGE_SEED = 23  # controls which answer is shown first to the judge, per question


def load_index():
    client = chromadb.PersistentClient(path=str(CHROMA_DB_PATH))
    collection = client.get_or_create_collection("rag_collection")
    vector_store = ChromaVectorStore(chroma_collection=collection)
    return VectorStoreIndex.from_vector_store(vector_store)


def answer_with_rag(index, question, k=2):
    # k=2 (was 3) and response_mode="simple_summarize" (a single LLM call, instead of
    # llama_index's default multi-call "refine" chaining) - this cuts token usage per
    # question substantially, which matters because this model is billed/rate-limited
    # per token per day on the free Groq tier.
    query_engine = index.as_query_engine(similarity_top_k=k, response_mode="simple_summarize")
    response = query_engine.query(f"{question}\n\n(Answer in 2-3 concise sentences.)")
    return str(response).strip()


def answer_without_rag(question):
    prompt = (
        "You are a helpful medical assistant chatbot. Answer the patient's question as "
        "best you can, in 2-3 concise sentences.\n\n"
        f"Question: {question}"
    )
    return str(Settings.llm.complete(prompt)).strip()


def judge_pair(source_chunk, question, first_label, first_answer, second_label, second_answer):
    """Asks the LLM to grade both answers against the real source passage, in a
    randomized order (so the judge has no way to know which one is the RAG answer)."""
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
    response = str(Settings.llm.complete(prompt)).strip()
    v1, v2 = "INCONSISTENT", "INCONSISTENT"
    for line in response.splitlines():
        line_up = line.strip().upper()
        if line_up.startswith("ANSWER 1"):
            v1 = "CONSISTENT" if ("CONSISTENT" in line_up and "INCONSISTENT" not in line_up) else "INCONSISTENT"
        elif line_up.startswith("ANSWER 2"):
            v2 = "CONSISTENT" if ("CONSISTENT" in line_up and "INCONSISTENT" not in line_up) else "INCONSISTENT"
    verdicts = {first_label: v1, second_label: v2}
    return verdicts["rag"], verdicts["no_rag"], response


def load_existing_results():
    if RAW_PATH.exists():
        with open(RAW_PATH, encoding="utf-8") as f:
            return json.load(f)
    return []


def save_results(results):
    with open(RAW_PATH, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)


def main():
    with open(TESTSET_PATH, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    print(f"Loaded {len(rows)} test questions from {TESTSET_PATH}")

    index = load_index()
    rng = random.Random(JUDGE_SEED)

    results = load_existing_results()
    done_ids = {r["test_id"] for r in results}
    if done_ids:
        print(f"Resuming - {len(done_ids)} question(s) already completed in a previous run.")

    for i, row in enumerate(rows, start=1):
        if row["test_id"] in done_ids:
            continue
        try:
            rag_answer = answer_with_rag(index, row["question"])
            no_rag_answer = answer_without_rag(row["question"])

            if rng.random() < 0.5:
                v_rag, v_no_rag, raw_judge = judge_pair(
                    row["source_chunk"], row["question"],
                    "rag", rag_answer, "no_rag", no_rag_answer)
            else:
                v_no_rag, v_rag, raw_judge = judge_pair(
                    row["source_chunk"], row["question"],
                    "no_rag", no_rag_answer, "rag", rag_answer)
        except Exception as e:
            print(f"[{i}/{len(rows)}] ERROR on test_id {row['test_id']}: {e}  (will retry next run)")
            continue

        results.append({
            **row,
            "rag_answer": rag_answer,
            "no_rag_answer": no_rag_answer,
            "rag_verdict": v_rag,
            "no_rag_verdict": v_no_rag,
            "raw_judge_response": raw_judge,
        })
        save_results(results)
        print(f"[{i}/{len(rows)}] RAG={v_rag:12s} No-RAG={v_no_rag:12s}")
        time.sleep(0.3)

    n = len(results)
    rag_correct = sum(1 for r in results if r["rag_verdict"] == "CONSISTENT")
    no_rag_correct = sum(1 for r in results if r["no_rag_verdict"] == "CONSISTENT")

    summary = {
        "n": n,
        "rag_consistent": rag_correct,
        "rag_consistency_rate": round(rag_correct / n, 4) if n else 0,
        "no_rag_consistent": no_rag_correct,
        "no_rag_consistency_rate": round(no_rag_correct / n, 4) if n else 0,
    }
    with open(RESULTS_DIR / "rag_ablation_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    table = (
        "| Metric | RAG ON | RAG OFF |\n|---|---|---|\n"
        f"| Test questions (n) | {summary['n']} | {summary['n']} |\n"
        f"| Answers consistent with source | {summary['rag_consistent']} | {summary['no_rag_consistent']} |\n"
        f"| Consistency rate | {summary['rag_consistency_rate']*100:.1f}% | "
        f"{summary['no_rag_consistency_rate']*100:.1f}% |\n"
    )
    with open(RESULTS_DIR / "rag_ablation_table.md", "w", encoding="utf-8") as f:
        f.write(table)

    print("=" * 70)
    print(table)
    print(f"Saved detailed results to: {RESULTS_DIR}")


if __name__ == "__main__":
    main()
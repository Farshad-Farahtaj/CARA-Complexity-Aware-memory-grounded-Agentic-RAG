# -*- coding: utf-8 -*-
"""
Builds a test set for the RAG ablation study (does retrieval actually help?),
which is the evidence RQ2 asks for.

METHODOLOGY (goes in the thesis methodology chapter):
Random chunks are sampled directly from your real chroma_db (the same
documents CARA retrieves from in production), with a fixed random seed for
reproducibility. For each sampled chunk, an LLM is asked to write ONE
specific factual question that ONLY that passage answers (a question a
patient might realistically ask). This ties every test question to a real,
verifiable piece of your knowledge base - not hand-invented questions.

The next script (evaluate_rag_ablation.py) uses this test set to compare
answers WITH retrieval turned on vs WITH IT TURNED OFF, judged against the
original source passage.

Run from anywhere (same pattern as the other backend/scripts/*.py files):
    python backend\\scripts\\build_rag_ablation_testset.py

Output: backend\\scripts\\rag_ablation_testset.csv
"""

import csv
import random
from pathlib import Path
from dotenv import load_dotenv
import chromadb
from llama_index.core import Settings
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
OUT_PATH = PROJECT_ROOT / "backend" / "scripts" / "rag_ablation_testset.csv"

N_QUESTIONS = 50
RANDOM_SEED = 11
MIN_CHUNK_LEN = 200  # skip very short/junk chunks (headers, page numbers, etc.)


def sample_chunks():
    client = chromadb.PersistentClient(path=str(CHROMA_DB_PATH))
    collection = client.get_or_create_collection("rag_collection")
    data = collection.get(include=["documents"])
    pairs = [(cid, doc) for cid, doc in zip(data["ids"], data["documents"])
             if doc and len(doc.strip()) >= MIN_CHUNK_LEN]
    print(f"Found {len(pairs)} usable chunks in chroma_db (out of {len(data['ids'])} total).")
    rng = random.Random(RANDOM_SEED)
    rng.shuffle(pairs)
    n = min(N_QUESTIONS, len(pairs))
    if n < N_QUESTIONS:
        print(f"[!] Only {n} usable chunks available - using all of them instead of {N_QUESTIONS}.")
    return pairs[:n]


def generate_question(chunk_text):
    prompt = (
        "Below is a passage from a medical reference document used by a patient-facing "
        "medical chatbot.\n\n"
        f"PASSAGE:\n{chunk_text}\n\n"
        "Write ONE specific factual question that a patient might ask, which this exact "
        "passage answers. The question must require this specific passage to answer "
        "correctly - not something answerable from general knowledge alone. "
        "Reply with ONLY the question, nothing else."
    )
    return str(Settings.llm.complete(prompt)).strip()


def main():
    pairs = sample_chunks()
    rows = []
    for i, (chunk_id, chunk_text) in enumerate(pairs, start=1):
        question = generate_question(chunk_text)
        rows.append({
            "test_id": i,
            "chunk_id": chunk_id,
            "source_chunk": chunk_text,
            "question": question,
        })
        print(f"[{i}/{len(pairs)}] {question}")

    with open(OUT_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    print("=" * 70)
    print(f"Saved {len(rows)} test questions to: {OUT_PATH}")


if __name__ == "__main__":
    main()
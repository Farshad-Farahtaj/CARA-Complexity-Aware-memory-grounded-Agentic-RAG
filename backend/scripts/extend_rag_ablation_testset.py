# -*- coding: utf-8 -*-
"""
Extends the RAG ablation test set (RQ2) from 50 questions to 300 WITHOUT
changing the 50 questions that already exist.

How it works
------------
build_rag_ablation_testset.py shuffles all usable chunks of chroma_db with a
fixed seed (11) and takes the first 50. With the same seed, the first 50 chunks
of a longer list are exactly the same 50 chunks. So this script:

  1. repeats the same shuffle,
  2. checks that the 50 chunks already in rag_ablation_testset.csv really are
     the first 50 of the shuffled list (it compares the chunk ids),
  3. writes ONE new question for each of the next chunks, exactly like the
     original script did, and appends it to the same CSV file.

The existing 50 rows are never modified. The script saves after every question
and can be stopped and restarted at any time.

After it finishes, run the evaluation exactly as before. It already skips the
questions that were answered, so only the new ones are evaluated:

    python backend\\scripts\\extend_rag_ablation_testset.py
    python backend\\scripts\\evaluate_rag_ablation.py

The optional number is the total size of the test set (default 300):

    python backend\\scripts\\extend_rag_ablation_testset.py 200
"""

import csv
import random
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CHROMA_DB_PATH = PROJECT_ROOT / "chroma_db"
CSV_PATH = PROJECT_ROOT / "backend" / "scripts" / "rag_ablation_testset.csv"

DEFAULT_TOTAL = 300
RANDOM_SEED = 11        # must stay 11: same seed as build_rag_ablation_testset.py
MIN_CHUNK_LEN = 200     # same filter as build_rag_ablation_testset.py
FIELDNAMES = ["test_id", "chunk_id", "source_chunk", "question"]

RATE_LIMIT_WAIT = 65
MAX_RATE_LIMIT_WAITS = 6


def shuffled_chunks():
    """Identical to sample_chunks() in build_rag_ablation_testset.py, but returns the
    whole shuffled list instead of only the first 50."""
    import chromadb
    client = chromadb.PersistentClient(path=str(CHROMA_DB_PATH))
    collection = client.get_or_create_collection("rag_collection")
    data = collection.get(include=["documents"])
    pairs = [(cid, doc) for cid, doc in zip(data["ids"], data["documents"])
             if doc and len(doc.strip()) >= MIN_CHUNK_LEN]
    print(f"Found {len(pairs)} usable chunks in chroma_db (out of {len(data['ids'])} total).")
    rng = random.Random(RANDOM_SEED)
    rng.shuffle(pairs)
    return pairs


def make_llm():
    from llama_index.llms.groq import Groq
    return Groq(model="openai/gpt-oss-120b")


def generate_question(llm, chunk_text):
    """Same prompt as build_rag_ablation_testset.py."""
    prompt = (
        "Below is a passage from a medical reference document used by a patient-facing "
        "medical chatbot.\n\n"
        f"PASSAGE:\n{chunk_text}\n\n"
        "Write ONE specific factual question that a patient might ask, which this exact "
        "passage answers. The question must require this specific passage to answer "
        "correctly - not something answerable from general knowledge alone. "
        "Reply with ONLY the question, nothing else."
    )
    return str(llm.complete(prompt)).strip()


def is_rate_limit_error(exc):
    text = f"{type(exc).__name__} {exc}".lower()
    return any(s in text for s in ("429", "rate limit", "rate_limit", "ratelimit",
                                   "quota", "resource_exhausted", "too many requests"))


def read_existing():
    if not CSV_PATH.exists():
        return []
    with open(CSV_PATH, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def file_ends_with_newline(path):
    with open(path, "rb") as f:
        f.seek(0, 2)
        if f.tell() == 0:
            return True
        f.seek(-1, 2)
        return f.read(1) in (b"\n", b"\r")


def extend(total, pairs=None, llm=None, sleep=time.sleep):
    existing = read_existing()
    print(f"The test set currently has {len(existing)} question(s).")
    if len(existing) >= total:
        print(f"Nothing to do - it already has {total} or more.")
        return

    if pairs is None:
        pairs = shuffled_chunks()
    if len(pairs) < total:
        print(f"[!] Only {len(pairs)} usable chunks exist - the test set will stop at that number.")
        total = len(pairs)

    # Safety check: the rows already in the CSV must be the first rows of the same shuffle.
    for i, row in enumerate(existing):
        if row["chunk_id"] != pairs[i][0]:
            print("=" * 70)
            print("[X] STOPPED - nothing was changed.")
            print(f"    Row {i + 1} of the CSV is chunk {row['chunk_id']},")
            print(f"    but position {i + 1} of the shuffled list is chunk {pairs[i][0]}.")
            print("    This means chroma_db is not the same as when the 50 questions were made")
            print("    (for example it was rebuilt). Send this message to get it fixed.")
            print("=" * 70)
            sys.exit(1)
    print("Verified: the existing questions are the first chunks of the same shuffle.")

    if llm is None:
        llm = make_llm()

    if existing and not file_ends_with_newline(CSV_PATH):
        with open(CSV_PATH, "a", encoding="utf-8", newline="") as f:
            f.write("\r\n")

    write_header = not existing
    for position in range(len(existing), total):
        chunk_id, chunk_text = pairs[position]
        rate_waits = 0
        while True:
            try:
                question = generate_question(llm, chunk_text)
                break
            except Exception as e:  # noqa: BLE001
                if is_rate_limit_error(e) and rate_waits < MAX_RATE_LIMIT_WAITS:
                    rate_waits += 1
                    print(f"    provider is rate-limiting, waiting {RATE_LIMIT_WAIT}s "
                          f"({rate_waits}/{MAX_RATE_LIMIT_WAITS}) ...")
                    sleep(RATE_LIMIT_WAIT)
                    continue
                print("=" * 70)
                print(f"Stopped at question {position + 1}: {type(e).__name__}: {e}")
                print(f"Progress is saved ({position} question(s) in the file).")
                print("Run exactly the same command again later (or tomorrow) to continue.")
                print("=" * 70)
                return

        with open(CSV_PATH, "a", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
            if write_header:
                writer.writeheader()
                write_header = False
            writer.writerow({
                "test_id": position + 1,
                "chunk_id": chunk_id,
                "source_chunk": chunk_text,
                "question": question,
            })
        print(f"[{position + 1}/{total}] {question}")

    print("=" * 70)
    print(f"Done. The test set now has {total} questions: {CSV_PATH}")
    print("Next step:  python backend\\scripts\\evaluate_rag_ablation.py")


if __name__ == "__main__":
    extend(int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_TOTAL)
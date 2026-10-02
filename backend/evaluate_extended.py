# -*- coding: utf-8 -*-
"""
Extends the MedQA benchmark (RQ1) from 100 to 300 questions WITHOUT throwing
away the 100 questions that were already answered.

How it works
------------
evaluate.py draws its 100 questions with random.seed(42) + random.sample(...).
With the same seed, the first 100 questions of a 300-question sample are exactly
the same 100 questions, in the same order. So this script:

  1. draws the 300-question sample with the same seed,
  2. loads the existing results from eval_results/<model>.json and checks,
     question by question, that they really are the first 100 of the new sample
     (it compares the correct answer letter of every question),
  3. keeps every question that was already answered,
  4. runs ONLY what is missing: the 200 new questions, plus any old question
     that failed with an API error (so those get a real answer this time),
  5. saves after every single question into eval_results_extended/<model>.json.

The prompt, the retrieval settings (top-3) and the answer parsing are identical
to evaluate.py, so old and new answers are directly comparable.

Safe to stop and restart at any time: it always continues where it stopped.
If the provider's daily limit is reached, it saves and exits with a clear
message. Just run the same command again later (or the next day).

Run from the project root (same as evaluate.py):

    python backend/evaluate_extended.py gptoss
    python backend/evaluate_extended.py qwen38
    python backend/evaluate_extended.py gemma4 100     <- only re-runs the questions that failed
    python backend/evaluate_extended.py summary

The optional number after the model key is the total sample size (default 300).
"""

import json
import random
import re
import sys
import time
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEST_FILE = PROJECT_ROOT / "MedQA" / "Questions" / "4_options" / "phrases_no_exclude_test.jsonl"
CHROMA_DB_PATH = PROJECT_ROOT / "chroma_db"
OLD_RESULTS_DIR = PROJECT_ROOT / "eval_results"
NEW_RESULTS_DIR = PROJECT_ROOT / "eval_results_extended"

DEFAULT_SAMPLE_SIZE = 300
SEED = 42                      # must stay 42: this is what makes the old 100 a prefix of the new sample

MAX_ATTEMPTS = 3               # same as evaluate.py: attempts for ordinary (non rate-limit) errors
RETRY_DELAY = 5                # seconds between those attempts (same as evaluate.py)
RATE_LIMIT_WAIT = 65           # seconds to wait when the provider says "too many requests"
MAX_RATE_LIMIT_WAITS = 6       # after this many waits in a row, assume the DAILY limit is reached and stop

MODEL_NAMES = {
    "gemma4": "Gemma 4 31B",
    "medgemma": "MedGemma 4B",
    "gptoss": "GPT-OSS-120B",
    "qwen38": "Qwen3.8 27B",
}
NEEDS_CLEANING = {"medgemma"}


def make_llm(key):
    """Same model definitions as evaluate.py. Imports are inside the function so that
    'summary' works even if a provider package is missing."""
    if key == "gemma4":
        from llama_index.llms.google_genai import GoogleGenAI
        return GoogleGenAI(model="gemma-4-31b-it")
    if key == "medgemma":
        from llama_index.llms.ollama import Ollama
        return Ollama(model="medgemma1.5:4b", request_timeout=600.0)
    if key == "gptoss":
        from llama_index.llms.groq import Groq
        return Groq(model="openai/gpt-oss-120b")
    if key == "qwen38":
        from llama_index.llms.groq import Groq
        return Groq(model="qwen/qwen3.8-27b")
    raise ValueError(key)


def setup_query_engine(key):
    import chromadb
    from llama_index.core import VectorStoreIndex, StorageContext, Settings
    from llama_index.vector_stores.chroma import ChromaVectorStore
    from llama_index.embeddings.ollama import OllamaEmbedding

    Settings.llm = make_llm(key)
    Settings.embed_model = OllamaEmbedding(model_name="nomic-embed-text")
    chroma_client = chromadb.PersistentClient(path=str(CHROMA_DB_PATH))
    chroma_collection = chroma_client.get_or_create_collection("rag_collection")
    vector_store = ChromaVectorStore(chroma_collection=chroma_collection)
    storage_context = StorageContext.from_defaults(vector_store=vector_store)
    index = VectorStoreIndex.from_vector_store(vector_store, storage_context=storage_context)
    return index.as_query_engine(similarity_top_k=3)


# ----------------------------------------------------------------------------
# Identical to evaluate.py
# ----------------------------------------------------------------------------
def load_sample(sample_size):
    with open(TEST_FILE, "r", encoding="utf-8") as f:
        lines = [json.loads(line) for line in f]
    random.seed(SEED)
    return random.sample(lines, sample_size)


def build_prompt(item):
    options_text = "\n".join(f"{k}: {v}" for k, v in item["options"].items())
    return (
        f"{item['question']}\n\n{options_text}\n\n"
        "Answer with only the single letter of the correct option, nothing else."
    )


def extract_letter(response_text):
    match = re.search(r"\b([A-E])\b", response_text.strip())
    return match.group(1) if match else None


def clean_medgemma(text):
    return re.sub(r"<unused94>.*?<unused95>", "", text, flags=re.DOTALL).strip()


# ----------------------------------------------------------------------------
# New logic: reuse old answers, resume, handle rate limits
# ----------------------------------------------------------------------------
class DailyLimitReached(Exception):
    pass


def is_rate_limit_error(exc):
    text = f"{type(exc).__name__} {exc}".lower()
    return any(s in text for s in ("429", "rate limit", "rate_limit", "ratelimit",
                                   "quota", "resource_exhausted", "too many requests"))


def is_failed(entry):
    return str(entry.get("status", "")).startswith("ERROR")


def load_state(key, sample):
    """Returns {question_idx: entry} with every question that is already answered."""
    new_path = NEW_RESULTS_DIR / f"{key}.json"
    old_path = OLD_RESULTS_DIR / f"{key}.json"

    if new_path.exists():
        source, origin = new_path, "this script's own previous progress"
    elif old_path.exists():
        source, origin = old_path, "the original 100-question run"
    else:
        print("No previous results found - starting from question 1.")
        return {}

    with open(source, "r", encoding="utf-8") as f:
        previous = json.load(f)

    state = {}
    for entry in previous.get("per_question", []):
        idx = int(entry["question_idx"])
        if idx > len(sample):
            continue
        expected = sample[idx - 1]["answer_idx"]
        if entry["actual"] != expected:
            print("=" * 70)
            print(f"[X] STOPPED - nothing was changed.")
            print(f"    Question {idx} in {source} has correct answer '{entry['actual']}',")
            print(f"    but question {idx} of the new sample has correct answer '{expected}'.")
            print("    This means the old results are NOT the first questions of the new sample")
            print("    (for example the MedQA file is different). Send this message to get it fixed.")
            print("=" * 70)
            sys.exit(1)
        state[idx] = entry

    n_failed = sum(1 for e in state.values() if is_failed(e))
    print(f"Loaded {len(state)} already-answered question(s) from {origin}")
    print(f"  -> verified: they match the first questions of the new sample.")
    if n_failed:
        print(f"  -> {n_failed} of them had failed with an API error and will be run again.")
    return state


def summarize(key, state, sample_size):
    entries = [state[i] for i in sorted(state)]
    answered = [e for e in entries if not is_failed(e)]
    correct = sum(1 for e in answered if e["status"] == "correct")
    return {
        "model_key": key,
        "model_name": MODEL_NAMES[key],
        "sample_size": sample_size,
        "completed": len(answered),
        "failed": len(entries) - len(answered),
        "correct": correct,
        "accuracy": (correct / len(answered) * 100) if answered else 0.0,
        "avg_seconds": (sum(e["seconds"] for e in answered) / len(answered)) if answered else 0.0,
        "per_question": entries,
    }


def save(key, state, sample_size):
    NEW_RESULTS_DIR.mkdir(exist_ok=True)
    path = NEW_RESULTS_DIR / f"{key}.json"
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(summarize(key, state, sample_size), f, indent=2, ensure_ascii=False)
    tmp.replace(path)   # atomic: a crash can never leave a half-written results file


def ask(query_engine, prompt, sleep=time.sleep):
    """Returns (response_text_or_None, attempts, error_or_None, seconds_of_last_call)."""
    attempts = 0
    rate_waits = 0
    last_error = None
    while attempts < MAX_ATTEMPTS:
        start = time.time()
        try:
            response = query_engine.query(prompt)
            return str(response), attempts + 1, None, time.time() - start
        except Exception as e:  # noqa: BLE001 - any provider error is handled the same way
            elapsed = time.time() - start
            last_error = e
            if is_rate_limit_error(e):
                rate_waits += 1
                if rate_waits > MAX_RATE_LIMIT_WAITS:
                    raise DailyLimitReached(str(e))
                print(f"    provider is rate-limiting, waiting {RATE_LIMIT_WAIT}s "
                      f"({rate_waits}/{MAX_RATE_LIMIT_WAITS}) ...")
                sleep(RATE_LIMIT_WAIT)
                continue                      # a rate-limit wait does not count as an attempt
            attempts += 1
            if attempts < MAX_ATTEMPTS:
                sleep(RETRY_DELAY)
    return None, attempts, last_error, elapsed


def run_model(key, sample_size, query_engine=None, sleep=time.sleep):
    sample = load_sample(sample_size)
    state = load_state(key, sample)
    todo = [i for i in range(1, sample_size + 1) if i not in state or is_failed(state[i])]

    print(f"\n=== {MODEL_NAMES[key]}: {sample_size} questions in total, {len(todo)} left to run ===")
    if not todo:
        save(key, state, sample_size)
        print("Nothing left to do - all questions are answered.")
        print_one_line_summary(key, state, sample_size)
        return

    if query_engine is None:
        query_engine = setup_query_engine(key)

    for n_done, idx in enumerate(todo, start=1):
        item = sample[idx - 1]
        try:
            text, attempts, error, seconds = ask(query_engine, build_prompt(item), sleep=sleep)
        except DailyLimitReached:
            save(key, state, sample_size)
            print("=" * 70)
            print("The provider keeps refusing requests - the DAILY limit is probably reached.")
            print(f"Progress is saved ({len(todo) - n_done + 1} question(s) still to run).")
            print("Run exactly the same command again later (or tomorrow) to continue.")
            print("=" * 70)
            return

        if error is not None:
            predicted, status = None, f"ERROR after {attempts} attempts: {type(error).__name__}"
        else:
            if key in NEEDS_CLEANING:
                text = clean_medgemma(text)
            predicted = extract_letter(text)
            status = "correct" if predicted == item["answer_idx"] else "wrong"

        state[idx] = {
            "question_idx": idx,
            "predicted": predicted,
            "actual": item["answer_idx"],
            "status": status,
            "attempts": attempts,
            "seconds": round(seconds, 1),
            "run_date": date.today().isoformat(),
        }
        save(key, state, sample_size)
        print(f"[{n_done}/{len(todo)}] question {idx}: predicted={predicted} "
              f"actual={item['answer_idx']} ({status}) - {seconds:.1f}s")

    print_one_line_summary(key, state, sample_size)
    still_failed = sum(1 for e in state.values() if is_failed(e))
    if still_failed:
        print(f"{still_failed} question(s) still failed with an API error. "
              "Run the same command again to retry only those.")
    print(f"Saved to {NEW_RESULTS_DIR / (key + '.json')}")


def print_one_line_summary(key, state, sample_size):
    s = summarize(key, state, sample_size)
    print(f"\n{s['model_name']}: {s['correct']}/{s['completed']} correct = {s['accuracy']:.1f}% "
          f"({s['completed']}/{sample_size} answered, {s['failed']} failed), "
          f"avg {s['avg_seconds']:.1f}s/question")


def show_summary():
    if not NEW_RESULTS_DIR.exists():
        print("No extended results yet. Run a model first, e.g.: python backend/evaluate_extended.py gptoss")
        return
    print("\n=== Extended results so far ===")
    for key, name in MODEL_NAMES.items():
        path = NEW_RESULTS_DIR / f"{key}.json"
        if not path.exists():
            print(f"{name:<14} (not run yet)")
            continue
        with open(path, "r", encoding="utf-8") as f:
            r = json.load(f)
        print(f"{name:<14} {r['correct']:3d}/{r['completed']:3d} correct = {r['accuracy']:5.1f}%   "
              f"answered {r['completed']}/{r['sample_size']}, failed {r.get('failed', 0)}, "
              f"avg {r['avg_seconds']:6.1f}s")


if __name__ == "__main__":
    valid = list(MODEL_NAMES) + ["summary"]
    if len(sys.argv) not in (2, 3) or sys.argv[1] not in valid:
        print("Usage:")
        print("  python backend/evaluate_extended.py <model_key> [sample_size]")
        print("  python backend/evaluate_extended.py summary")
        print(f"Model keys: {', '.join(MODEL_NAMES)}   (sample_size defaults to {DEFAULT_SAMPLE_SIZE})")
        sys.exit(1)

    if sys.argv[1] == "summary":
        show_summary()
    else:
        size = int(sys.argv[2]) if len(sys.argv) == 3 else DEFAULT_SAMPLE_SIZE
        run_model(sys.argv[1], size)
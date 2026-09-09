import json
import os
import random
import re
import sys
import time
from pathlib import Path

from llama_index.core import VectorStoreIndex, StorageContext, Settings
from llama_index.vector_stores.chroma import ChromaVectorStore
from llama_index.llms.google_genai import GoogleGenAI
from llama_index.llms.groq import Groq
from llama_index.llms.openai_like import OpenAILike
from llama_index.llms.ollama import Ollama
from llama_index.embeddings.ollama import OllamaEmbedding
from dotenv import load_dotenv
import chromadb

load_dotenv()

TEST_FILE = Path("MedQA/Questions/4_options/phrases_no_exclude_test.jsonl")
RESULTS_DIR = Path("eval_results")
SAMPLE_SIZE = 100
SEED = 42

MODELS = {
    "gemma4":   ("Gemma 4 31B",  lambda: GoogleGenAI(model="gemma-4-31b-it"),                     False),
    "medgemma": ("MedGemma 4B",  lambda: Ollama(model="medgemma1.5:4b", request_timeout=600.0),   True),
    "gptoss":   ("GPT-OSS-120B", lambda: Groq(model="openai/gpt-oss-120b"),                        False),
    "qwen38":   ("Qwen3.8 27B",  lambda: Groq(model="qwen/qwen3.8-27b"),                           False),
}


def load_sample():
    with open(TEST_FILE, "r", encoding="utf-8") as f:
        lines = [json.loads(line) for line in f]
    random.seed(SEED)
    return random.sample(lines, SAMPLE_SIZE)


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


def setup_query_engine():
    Settings.embed_model = OllamaEmbedding(model_name="nomic-embed-text")
    chroma_client = chromadb.PersistentClient(path="./chroma_db")
    chroma_collection = chroma_client.get_or_create_collection("rag_collection")
    vector_store = ChromaVectorStore(chroma_collection=chroma_collection)
    storage_context = StorageContext.from_defaults(vector_store=vector_store)
    index = VectorStoreIndex.from_vector_store(vector_store, storage_context=storage_context)
    return index.as_query_engine(similarity_top_k=3)


def query_with_retries(query_engine, prompt, max_attempts=3, retry_delay=5):
    last_exception = None
    for attempt in range(1, max_attempts + 1):
        try:
            response = query_engine.query(prompt)
            return response, attempt, None
        except Exception as e:
            last_exception = e
            if attempt < max_attempts:
                time.sleep(retry_delay)
    return None, max_attempts, last_exception


def run_model(key):
    model_name, llm_factory, needs_cleaning = MODELS[key]
    Settings.llm = llm_factory()
    query_engine = setup_query_engine()
    sample = load_sample()

    RESULTS_DIR.mkdir(exist_ok=True)
    result_path = RESULTS_DIR / f"{key}.json"

    per_question = []
    correct = 0
    total_time = 0.0
    start_index = 0

    if result_path.exists():
        with open(result_path, "r", encoding="utf-8") as f:
            existing = json.load(f)
        if existing.get("sample_size") == len(sample):
            per_question = existing["per_question"]
            correct = existing["correct"]
            total_time = existing["avg_seconds"] * existing["completed"]
            start_index = existing["completed"]
            print(f"Resuming {model_name} from question {start_index + 1}/{len(sample)} "
                  f"(found existing progress in {result_path})")

    print(f"\n=== Evaluating {model_name} on {len(sample)} questions ===")
    for i, item in enumerate(sample[start_index:], start_index + 1):
        prompt = build_prompt(item)
        start = time.time()
        response, attempts, error = query_with_retries(query_engine, prompt, max_attempts=3, retry_delay=5)
        elapsed = time.time() - start
        total_time += elapsed

        if error is not None:
            predicted = None
            is_correct = False
            status = f"ERROR after {attempts} attempts: {type(error).__name__}"
        else:
            text = str(response)
            if needs_cleaning:
                text = clean_medgemma(text)
            predicted = extract_letter(text)
            is_correct = predicted == item["answer_idx"]
            status = "correct" if is_correct else "wrong"

        correct += int(is_correct)
        per_question.append({
            "question_idx": i,
            "predicted": predicted,
            "actual": item["answer_idx"],
            "status": status,
            "attempts": attempts,
            "seconds": round(elapsed, 1),
        })
        retry_note = f" (took {attempts} attempts)" if attempts > 1 else ""
        print(f"[{i}/{len(sample)}] predicted={predicted} actual={item['answer_idx']} ({status}){retry_note} - {elapsed:.1f}s")

        partial_result = {
            "model_key": key,
            "model_name": model_name,
            "sample_size": len(sample),
            "completed": i,
            "correct": correct,
            "accuracy": correct / i * 100,
            "avg_seconds": total_time / i,
            "per_question": per_question,
        }
        with open(RESULTS_DIR / f"{key}.json", "w", encoding="utf-8") as f:
            json.dump(partial_result, f, indent=2, ensure_ascii=False)

    accuracy = correct / len(sample) * 100
    avg_time = total_time / len(sample)
    print(f"\n{model_name}: {correct}/{len(sample)} correct = {accuracy:.1f}% accuracy, avg {avg_time:.1f}s/question")
    print(f"Saved to {RESULTS_DIR / f'{key}.json'}")


def show_summary():
    if not RESULTS_DIR.exists():
        print("No results yet. Run a model first, e.g.: python backend/evaluate.py gemma4")
        return

    print("\n=== Summary (all models run so far) ===")
    for key in MODELS:
        path = RESULTS_DIR / f"{key}.json"
        if not path.exists():
            print(f"{MODELS[key][0]:<18} (not run yet)")
            continue
        with open(path, "r", encoding="utf-8") as f:
            result = json.load(f)
        print(f"{result['model_name']:<18} {result['accuracy']:5.1f}% accuracy, {result['avg_seconds']:6.1f}s/question avg")


if __name__ == "__main__":
    valid_args = list(MODELS.keys()) + ["summary"]
    if len(sys.argv) != 2 or sys.argv[1] not in valid_args:
        print("Usage:")
        print("  python backend/evaluate.py <model_key>   -- run one model")
        print("  python backend/evaluate.py summary       -- show combined results so far")
        print(f"Available model keys: {', '.join(MODELS.keys())}")
        sys.exit(1)

    arg = sys.argv[1]
    if arg == "summary":
        show_summary()
    else:
        run_model(arg)
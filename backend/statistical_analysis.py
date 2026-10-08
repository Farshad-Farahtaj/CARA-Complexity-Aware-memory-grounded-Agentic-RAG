"""
Statistical analysis of the three experiments.

Reads the per-item results saved by the evaluation scripts and prints every
number reported in the thesis: confidence intervals, tests, and descriptive
statistics. Nothing is written to disk and no model is called.

Run from anywhere:
    python backend/statistical_analysis.py

Input files (all inside the project root):
    eval_results/<model>.json                 first pass of the model benchmark
    eval_results_extended/<model>.json        used instead, when it exists
    rag_ablation_results/rag_ablation_raw.json
    escalation_eval_results/escalation_eval_raw.json

Requires: numpy, scipy
"""

import json
import math
from collections import Counter
from itertools import combinations
from pathlib import Path

import numpy as np
from scipy import stats

# This file lives in backend/, so the project root is one level up.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
Z = 1.96  # 0.975 quantile of the standard normal distribution

MODELS = [
    ("gptoss", "GPT-OSS-120B"),
    ("gemma4", "Gemma 4 31B"),
    ("qwen38", "Qwen3.8 27B"),
    ("medgemma", "MedGemma 4B"),
]

# Questions of the RAG ablation whose source passage is a list of
# bibliographic references (identified by reading the 200 passages).
REFERENCE_LIST_IDS = {
    1, 2, 19, 21, 32, 37, 42, 45, 50, 72, 73, 74, 80, 93, 95, 101,
    102, 107, 135, 136, 149, 156, 157, 170,
}


# ----------------------------------------------------------------------
# Statistical tools
# ----------------------------------------------------------------------
def wilson_interval(successes, n, z=Z):
    """95% Wilson score interval for a proportion."""
    p = successes / n
    denominator = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denominator
    half_width = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return center - half_width, center + half_width


def mcnemar_exact(b, c):
    """Exact two-sided McNemar test on the two numbers of discordant items."""
    if b + c == 0:
        return 1.0
    return min(1.0, 2 * stats.binom.cdf(min(b, c), b + c, 0.5))


def paired_difference_interval(b, c, n, z=Z):
    """Difference between two paired proportions with its 95% interval."""
    difference = (b - c) / n
    standard_error = math.sqrt((b + c) - (b - c) ** 2 / n) / n
    low = max(-1.0, difference - z * standard_error)
    high = min(1.0, difference + z * standard_error)
    return difference, low, high


def format_p(p, digits=3):
    """'=0.503' with a fixed number of decimals, or '<0.001' when the value rounds to zero."""
    if round(p, digits) == 0:
        return f"<{10 ** -digits:.{digits}f}"
    return f"={p:.{digits}f}"


def holm_correction(p_values):
    """Holm adjusted p-values, returned in the original order."""
    order = sorted(range(len(p_values)), key=lambda i: p_values[i])
    adjusted = [0.0] * len(p_values)
    running_max = 0.0
    for rank, i in enumerate(order):
        value = min(1.0, (len(p_values) - rank) * p_values[i])
        running_max = max(running_max, value)
        adjusted[i] = running_max
    return adjusted


def cochran_q(matrix):
    """Cochran's Q test. matrix has one row per item and one column per condition."""
    k = matrix.shape[1]
    column_totals = matrix.sum(axis=0)
    row_totals = matrix.sum(axis=1)
    total = matrix.sum()
    q = (k - 1) * (k * (column_totals ** 2).sum() - total ** 2) / (k * total - (row_totals ** 2).sum())
    return q, stats.chi2.sf(q, k - 1)


def percent(x):
    return f"{100 * x:.1f}"


def title(text):
    print("\n" + "=" * 72)
    print(text)
    print("=" * 72)


# ----------------------------------------------------------------------
# Experiment 1: model comparison
# ----------------------------------------------------------------------
def load_benchmark(key):
    extended = PROJECT_ROOT / "eval_results_extended" / f"{key}.json"
    first_pass = PROJECT_ROOT / "eval_results" / f"{key}.json"
    path = extended if extended.exists() else first_pass
    with open(path, encoding="utf-8") as f:
        return json.load(f)["per_question"]


def experiment_1():
    title("EXPERIMENT 1: model comparison on MedQA")
    correct = {}
    seconds = {}
    for key, name in MODELS:
        items = load_benchmark(key)
        correct[key] = np.array([1 if item["status"] == "correct" else 0 for item in items])
        seconds[key] = np.array([item["seconds"] for item in items], dtype=float)
        n = len(items)
        k = int(correct[key].sum())
        low, high = wilson_interval(k, n)
        q1, median, q3 = np.percentile(seconds[key], [25, 50, 75])
        print(f"{name:<14} {k}/{n} = {percent(k / n)}%  CI {percent(low)}-{percent(high)}   "
              f"time: median {median:.1f} s, IQR {q1:.1f}-{q3:.1f}, mean {seconds[key].mean():.1f}")

    n = len(next(iter(correct.values())))
    matrix = np.stack([correct[key] for key, _ in MODELS], axis=1)
    q, p = cochran_q(matrix)
    print(f"\nCochran's Q = {q:.2f}, 3 degrees of freedom, p = {p:.2e}")

    print("\nPairwise comparisons (McNemar exact test, Holm correction):")
    pairs = list(combinations(MODELS, 2))
    rows = []
    for (key_a, name_a), (key_b, name_b) in pairs:
        b = int(((correct[key_a] == 1) & (correct[key_b] == 0)).sum())
        c = int(((correct[key_a] == 0) & (correct[key_b] == 1)).sum())
        rows.append((name_a, name_b, b, c, mcnemar_exact(b, c)))
    adjusted = holm_correction([row[4] for row in rows])
    for (name_a, name_b, b, c, p), p_holm in zip(rows, adjusted):
        difference, low, high = paired_difference_interval(b, c, n)
        print(f"  {name_a:<13} vs {name_b:<13} b={b:>2} c={c:>2}  "
              f"difference {100 * difference:+.0f} ({100 * low:.1f} to {100 * high:.1f})  "
              f"p{format_p(p)}  Holm{format_p(p_holm)}")

    per_question = Counter(matrix.sum(axis=1).tolist())
    print("\nNumber of questions answered correctly by 4, 3, 2, 1, 0 models:",
          [per_question.get(k, 0) for k in (4, 3, 2, 1, 0)])

    print("\nResponse time, Wilcoxon signed-rank test on the paired times:")
    for (key_a, name_a), (key_b, name_b) in pairs:
        p = stats.wilcoxon(seconds[key_a], seconds[key_b]).pvalue
        print(f"  {name_a:<13} vs {name_b:<13} p = {p:.2e}")


# ----------------------------------------------------------------------
# Experiment 2: retrieval ablation
# ----------------------------------------------------------------------
def summarize_ablation(label, items):
    n = len(items)
    rag = sum(item["rag_verdict"] == "CONSISTENT" for item in items)
    no_rag = sum(item["no_rag_verdict"] == "CONSISTENT" for item in items)
    b = sum(item["rag_verdict"] == "CONSISTENT" and item["no_rag_verdict"] != "CONSISTENT" for item in items)
    c = sum(item["rag_verdict"] != "CONSISTENT" and item["no_rag_verdict"] == "CONSISTENT" for item in items)
    rag_low, rag_high = wilson_interval(rag, n)
    no_low, no_high = wilson_interval(no_rag, n)
    difference, low, high = paired_difference_interval(b, c, n)
    print(f"{label:<28} n={n:>2}  with retrieval {rag}/{n} = {percent(rag / n)}% "
          f"(CI {percent(rag_low)}-{percent(rag_high)})  without {no_rag}/{n} = {percent(no_rag / n)}% "
          f"(CI {percent(no_low)}-{percent(no_high)})")
    print(f"{'':<28} b={b} c={c}  McNemar p{format_p(mcnemar_exact(b, c), 4)}  "
          f"difference {100 * difference:.1f} ({100 * low:.1f} to {100 * high:.1f})")


def experiment_2():
    title("EXPERIMENT 2: retrieval ablation")
    with open(PROJECT_ROOT / "rag_ablation_results" / "rag_ablation_raw.json", encoding="utf-8") as f:
        items = json.load(f)
    for item in items:
        item["id"] = int(item["test_id"])

    summarize_ablation("All questions", items)
    both = sum(i["rag_verdict"] == "CONSISTENT" and i["no_rag_verdict"] == "CONSISTENT" for i in items)
    neither = sum(i["rag_verdict"] != "CONSISTENT" and i["no_rag_verdict"] != "CONSISTENT" for i in items)
    print(f"{'':<28} both consistent: {both}, neither: {neither}")

    print()
    summarize_ablation("Without reference lists", [i for i in items if i["id"] not in REFERENCE_LIST_IDS])
    print()
    summarize_ablation('Without "the passage"', [i for i in items if "passage" not in i["question"].lower()])

    shown_first = sum(bool(i["rag_shown_first"]) for i in items)
    first = sum((i["rag_verdict"] if i["rag_shown_first"] else i["no_rag_verdict"]) == "CONSISTENT" for i in items)
    second = sum((i["no_rag_verdict"] if i["rag_shown_first"] else i["rag_verdict"]) == "CONSISTENT" for i in items)
    print(f"\nAnswer with retrieval shown first in {shown_first} of {len(items)} questions")
    print(f"Consistent verdicts: first position {first}, second position {second}")
    first_only = sum(
        (i["rag_verdict"] if i["rag_shown_first"] else i["no_rag_verdict"]) == "CONSISTENT"
        and (i["no_rag_verdict"] if i["rag_shown_first"] else i["rag_verdict"]) != "CONSISTENT"
        for i in items)
    second_only = sum(
        (i["rag_verdict"] if i["rag_shown_first"] else i["no_rag_verdict"]) != "CONSISTENT"
        and (i["no_rag_verdict"] if i["rag_shown_first"] else i["rag_verdict"]) == "CONSISTENT"
        for i in items)
    print(f"Only the first consistent: {first_only}, only the second: {second_only}, "
          f"McNemar p{format_p(mcnemar_exact(first_only, second_only))}")

    with_retrieval = np.median([len(i["rag_answer"].split()) for i in items])
    without = np.median([len(i["no_rag_answer"].split()) for i in items])
    print(f"\nMedian length of the answers (words): with retrieval {with_retrieval:.1f}, without {without:.1f}")

    # Retrieval: was the source passage of the question given to the model?
    if all("source_retrieved" in i for i in items):
        n = len(items)
        hits = [i for i in items if i["source_retrieved"]]
        misses = [i for i in items if not i["source_retrieved"]]
        low, high = wilson_interval(len(hits), n)
        print(f"\nSource passage retrieved: {len(hits)}/{n} = {percent(len(hits) / n)}% "
              f"(CI {percent(low)}-{percent(high)})")
        for depth in (1, 2, 3, 5, 10):
            found = sum(1 for i in items if i["source_rank"] is not None and i["source_rank"] <= depth)
            print(f"  source passage within the first {depth:>2} of the ranking: {found}/{n}")
        print()
        if hits:
            summarize_ablation("Source retrieved", hits)
        if misses:
            summarize_ablation("Source not retrieved", misses)


# ----------------------------------------------------------------------
# Experiment 3: escalation guard
# ----------------------------------------------------------------------
def experiment_3():
    title("EXPERIMENT 3: escalation guard")
    with open(PROJECT_ROOT / "escalation_eval_results" / "escalation_eval_raw.json", encoding="utf-8") as f:
        cases = json.load(f)

    tp = sum(c["ground_truth_label"] == "ESCALATE" and c["predicted_label"] == "ESCALATE" for c in cases)
    fn = sum(c["ground_truth_label"] == "ESCALATE" and c["predicted_label"] == "SAFE" for c in cases)
    fp = sum(c["ground_truth_label"] == "SAFE" and c["predicted_label"] == "ESCALATE" for c in cases)
    tn = sum(c["ground_truth_label"] == "SAFE" and c["predicted_label"] == "SAFE" for c in cases)
    n = len(cases)
    print(f"Confusion matrix: TP={tp} FN={fn} FP={fp} TN={tn}  (n={n})")

    for name, successes, total in [("accuracy", tp + tn, n), ("precision", tp, tp + fp),
                                   ("recall", tp, tp + fn), ("specificity", tn, tn + fp)]:
        low, high = wilson_interval(successes, total)
        print(f"  {name:<12} {successes}/{total} = {successes / total:.2f}  CI {low:.3f}-{high:.3f}")
    precision, recall = tp / (tp + fp), tp / (tp + fn)
    print(f"  {'F1':<12} {2 * precision * recall / (precision + recall):.2f}")
    mcc = (tp * tn - fp * fn) / math.sqrt((tp + fp) * (tp + fn) * (tn + fp) * (tn + fn))
    print(f"  {'MCC':<12} {mcc:.2f}")

    print("\nAccuracy by category:")
    for category in sorted({c["category"] for c in cases}):
        group = [c for c in cases if c["category"] == category]
        right = sum(c["ground_truth_label"] == c["predicted_label"] for c in group)
        low, high = wilson_interval(right, len(group))
        print(f"  {category}: {right}/{len(group)}  CI {percent(low)}-{percent(high)}")

    print("\nCases by clinic:", dict(Counter(c["clinic_name"] for c in cases)))
    print("Distinct patients:", len({c["patient_id"] for c in cases}),
          " distinct questions:", len({c["question"] for c in cases}))

    print("\nMisclassified cases:")
    for c in cases:
        if c["ground_truth_label"] != c["predicted_label"]:
            print(f"  id {c['test_id']} ({c['category']}): label {c['ground_truth_label']}, "
                  f"guard {c['predicted_label']} - {c['question']}")


if __name__ == "__main__":
    experiment_1()
    experiment_2()
    experiment_3()
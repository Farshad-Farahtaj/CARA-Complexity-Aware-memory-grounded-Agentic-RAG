import json
from pathlib import Path
import matplotlib.pyplot as plt

RESULTS_DIR = Path("eval_results")
MODEL_ORDER = ["gemma4", "medgemma", "gptoss", "qwen38"]


def load_results():
    results = []
    for key in MODEL_ORDER:
        path = RESULTS_DIR / f"{key}.json"
        if not path.exists():
            continue
        with open(path, "r", encoding="utf-8") as f:
            results.append(json.load(f))
    return results


def print_table(results):
    print(f"{'Model':<18}{'Accuracy':>12}{'Avg Time':>14}{'Completed':>14}")
    print("-" * 58)
    for r in results:
        completed = r.get("completed", r["sample_size"])
        print(f"{r['model_name']:<18}{r['accuracy']:>11.1f}%{r['avg_seconds']:>13.1f}s"
              f"{completed:>10}/{r['sample_size']}")


def make_chart(results):
    names = [r["model_name"] for r in results]
    accuracies = [r["accuracy"] for r in results]
    times = [r["avg_seconds"] for r in results]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))

    bars1 = ax1.bar(names, accuracies, color="#4C72B0")
    ax1.set_ylabel("Accuracy (%)")
    ax1.set_title("Model Accuracy on MedQA Sample")
    ax1.set_ylim(0, 100)
    for bar, acc in zip(bars1, accuracies):
        ax1.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 1, f"{acc:.1f}%", ha="center")
    ax1.tick_params(axis="x", rotation=20)

    bars2 = ax2.bar(names, times, color="#DD8452")
    ax2.set_ylabel("Avg. Seconds per Question")
    ax2.set_title("Model Response Time")
    for bar, t in zip(bars2, times):
        ax2.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + max(times) * 0.02, f"{t:.1f}s", ha="center")
    ax2.tick_params(axis="x", rotation=20)

    plt.tight_layout()
    out_path = RESULTS_DIR / "comparison_chart.png"
    plt.savefig(out_path, dpi=150)
    print(f"\nChart saved to {out_path}")


def export_markdown(results):
    lines = ["| Model | Accuracy | Avg Time/Question | Completed |", "|---|---|---|---|"]
    for r in results:
        completed = r.get("completed", r["sample_size"])
        lines.append(f"| {r['model_name']} | {r['accuracy']:.1f}% | {r['avg_seconds']:.1f}s | "
                      f"{completed}/{r['sample_size']} |")
    out_path = RESULTS_DIR / "comparison_table.md"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"Markdown table saved to {out_path}")


if __name__ == "__main__":
    results = load_results()
    if not results:
        print("No results found in eval_results/. Run evaluate.py first.")
    else:
        print_table(results)
        make_chart(results)
        export_markdown(results)
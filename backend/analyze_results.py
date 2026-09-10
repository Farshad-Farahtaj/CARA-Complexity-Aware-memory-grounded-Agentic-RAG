import json
from pathlib import Path
import matplotlib.pyplot as plt

RESULTS_DIR = Path("eval_results")
MODEL_ORDER = ["gemma4", "medgemma", "gptoss", "qwen38"]

MODEL_COLORS = {
    "Gemma 4 31B": "#4C72B0",
    "MedGemma 4B": "#DD8452",
    "GPT-OSS-120B": "#55A868",
    "Qwen3.8 27B": "#C44E52",
}


def load_results():
    results = []
    for key in MODEL_ORDER:
        path = RESULTS_DIR / f"{key}.json"
        if not path.exists():
            continue
        with open(path, "r", encoding="utf-8") as f:
            results.append(json.load(f))
    results.sort(key=lambda r: r["accuracy"], reverse=True)
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
    colors = [MODEL_COLORS.get(n, "#888888") for n in names]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5.5))
    fig.patch.set_facecolor("white")

    for ax in (ax1, ax2):
        ax.set_facecolor("white")
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.spines["left"].set_visible(False)
        ax.grid(axis="y", linestyle="-", linewidth=0.6, color="#e0e0e0", zorder=0)
        ax.set_axisbelow(True)
        ax.tick_params(left=False)

    bars1 = ax1.bar(names, accuracies, color=colors, width=0.6, zorder=3)
    ax1.set_ylabel("Accuracy (%)", fontsize=11)
    ax1.set_title("Accuracy on MedQA (n=100)", fontsize=13, fontweight="bold", pad=14)
    ax1.set_ylim(0, 100)
    for bar, acc in zip(bars1, accuracies):
        ax1.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 2,
                  f"{acc:.1f}%", ha="center", fontsize=10, fontweight="bold")
    ax1.tick_params(axis="x", rotation=15, labelsize=9.5)

    bars2 = ax2.bar(names, times, color=colors, width=0.6, zorder=3)
    ax2.set_ylabel("Avg. Seconds / Question", fontsize=11)
    ax2.set_title("Response Time (lower is faster)", fontsize=13, fontweight="bold", pad=14)
    for bar, t in zip(bars2, times):
        ax2.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + max(times) * 0.02,
                  f"{t:.1f}s", ha="center", fontsize=10, fontweight="bold")
    ax2.tick_params(axis="x", rotation=15, labelsize=9.5)

    fig.suptitle("CARA — Model Comparison", fontsize=15, fontweight="bold", y=1.02)
    plt.tight_layout()
    out_path = RESULTS_DIR / "comparison_chart.png"
    plt.savefig(out_path, dpi=180, bbox_inches="tight")
    print(f"\nChart saved to {out_path}")


def export_markdown(results):
    medals = ["🥇", "🥈", "🥉", "4️⃣"]
    best_acc = max(r["accuracy"] for r in results)
    best_time = min(r["avg_seconds"] for r in results)

    lines = [
        "| Rank | Model | Accuracy | Avg Time/Question | Completed |",
        "|:---:|---|:---:|:---:|:---:|",
    ]
    for i, r in enumerate(results):
        completed = r.get("completed", r["sample_size"])
        medal = medals[i] if i < len(medals) else ""
        acc_str = f"**{r['accuracy']:.1f}%**" if r["accuracy"] == best_acc else f"{r['accuracy']:.1f}%"
        time_str = f"**{r['avg_seconds']:.1f}s**" if r["avg_seconds"] == best_time else f"{r['avg_seconds']:.1f}s"
        lines.append(f"| {medal} | {r['model_name']} | {acc_str} | {time_str} | {completed}/{r['sample_size']} |")

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
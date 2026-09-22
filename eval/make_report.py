"""Build eval/RESULTS.md and eval/results/ablation.png from the run summaries.

Every number in the README comes from here - nothing is typed in by hand.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "eval" / "results"

LADDER = [
    ("baseline", "Baseline: single prompt, raw CREATE TABLE schema"),
    ("rich_schema", "+ rich schema (descriptions, sample values)"),
    ("value_hints", "+ value hints (DB values matching the question)"),
    ("few_shot", "+ 3 retrieved few-shot examples"),
    ("self_correct", "+ execution-guided self-correction (2 rounds)"),
    ("full", "+ self-consistency voting (5 samples)"),
    ("full_linking", "+ embedding schema linking"),
]


def load() -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(RESULTS.glob("*.summary.json"))]


def pct(x: float | None) -> str:
    return "-" if x is None else f"{100 * x:.1f}"


def main() -> None:
    runs = load()
    if not runs:
        print("no summaries yet")
        return
    main_model = "qwen2.5-coder:7b"
    lines = ["# Evaluation results", "",
             "Execution accuracy (EX) on the Spider dev set: a prediction is correct when its result "
             "set equals the gold query's result set (order-insensitive unless the gold query has ORDER BY, "
             "column order ignored). All models run locally through Ollama on an Apple M3 (16 GB).", ""]

    by_key = {(r["config_name"], r["model"], r["limit"]): r for r in runs}
    limits = sorted({r["limit"] for r in runs if r["limit"]}, reverse=True)

    for limit in limits + ([None] if any(r["limit"] is None for r in runs) else []):
        rows = [(label, by_key.get((cfg, main_model, limit))) for cfg, label in LADDER]
        rows = [(label, r) for label, r in rows if r]
        if not rows:
            continue
        scope = f"random sample of {limit} dev questions (seed {rows[0][1]['seed']})" if limit else "full dev set (1034 questions)"
        lines += [f"## Ablation - {main_model}, {scope}", "",
                  "| Pipeline | EX % | Δ | easy | medium | hard | extra | LLM calls / q | latency s / q |",
                  "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
        prev = None
        for label, r in rows:
            ex = r["overall"]["ex"]
            delta = "" if prev is None else f"{100 * (ex - prev):+.1f}"
            h = r["by_hardness"]
            lines.append(f"| {label} | **{pct(ex)}** | {delta} | " + " | ".join(
                pct(h.get(k, {}).get("ex")) for k in ("easy", "medium", "hard", "extra"))
                + f" | {r['mean_llm_calls']} | {r['mean_latency_s']} |")
            prev = ex
        lines.append("")

        models = sorted({r["model"] for r in runs if r["limit"] == limit})
        if len(models) > 1:
            lines += ["### Model comparison (same sample)", "", "| Model | baseline EX % | + rich schema, hints, few-shot, self-correction EX % |",
                      "|---|---:|---:|"]
            for m in models:
                b, s = by_key.get(("baseline", m, limit)), by_key.get(("self_correct", m, limit))
                lines.append(f"| {m} | {pct(b['overall']['ex']) if b else '-'} | {pct(s['overall']['ex']) if s else '-'} |")
            lines.append("")

        _chart(rows, limit)
        lines += [f"![ablation chart](results/ablation_{limit or 'all'}.png)", ""]

    lines += ["## Reproduce", "", "```bash", "eval/run_ablation.sh 200      # ladder + model comparison", "python eval/make_report.py", "```", "",
              "Hardness buckets use a sqlglot port of the official Spider rules; bucket sizes are within ~1% of the official script."]
    (ROOT / "eval" / "RESULTS.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


def _chart(rows: list[tuple[str, dict]], limit: int | None) -> None:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return
    labels = [label.split(":")[0].split("(")[0].strip() for label, _ in rows]
    values = [100 * r["overall"]["ex"] for _, r in rows]
    fig, ax = plt.subplots(figsize=(8, 0.55 * len(rows) + 1.2))
    bars = ax.barh(labels[::-1], values[::-1], color="#4c6ef5")
    ax.bar_label(bars, fmt="%.1f%%", padding=4, fontsize=9)
    ax.set_xlim(0, 100)
    ax.set_xlabel("Execution accuracy (%)")
    ax.set_title(f"Spider dev - cumulative ablation ({limit or 1034} questions)")
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(RESULTS / f"ablation_{limit or 'all'}.png", dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    main()

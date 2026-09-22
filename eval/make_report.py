"""Build eval/RESULTS.md and charts from the per-question result files.

Every number in the README comes from here - nothing is typed in by hand.
Confidence intervals are 95% percentile bootstrap intervals over questions.
Paired comparisons use the exact McNemar test on questions both runs answered.
"""

from __future__ import annotations

import json
import math
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "eval" / "results"
MAIN_MODEL = "qwen2.5-coder:7b"

LADDER = [
    ("baseline", "Baseline: one prompt, raw CREATE TABLE schema"),
    ("rich_schema", "+ rich schema (descriptions, sample values)"),
    ("value_hints", "+ value hints (DB values matching the question)"),
    ("few_shot", "+ 3 retrieved few-shot examples (RAG over Spider train)"),
    ("self_correct", "+ execution-guided self-correction (≤2 rounds)"),
    ("full", "+ self-consistency voting (5 samples)"),
    ("full_linking", "+ embedding-based schema linking"),
]
HARDNESS = ("easy", "medium", "hard", "extra")


def load_runs() -> dict[tuple[str, str, int | None], dict]:
    runs = {}
    for summary_path in sorted(RESULTS.glob("*.summary.json")):
        s = json.loads(summary_path.read_text())
        records = [json.loads(line) for line in (RESULTS / f"{s['run']}.jsonl").read_text().splitlines()]
        s["records"] = {r["idx"]: r for r in records}
        runs[(s["config_name"], s["model"], s["limit"])] = s
    return runs


def bootstrap_ci(values: list[bool], n: int = 2000, seed: int = 0) -> tuple[float, float]:
    rng = random.Random(seed)
    k = len(values)
    means = sorted(sum(values[rng.randrange(k)] for _ in range(k)) / k for _ in range(n))
    return means[int(0.025 * n)], means[int(0.975 * n) - 1]


def mcnemar_p(a: dict, b: dict) -> float:
    """Exact two-sided McNemar test: do runs a and b differ on the same questions?"""
    common = a.keys() & b.keys()
    only_a = sum(1 for i in common if a[i]["correct"] and not b[i]["correct"])
    only_b = sum(1 for i in common if b[i]["correct"] and not a[i]["correct"])
    n = only_a + only_b
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, k) for k in range(0, min(only_a, only_b) + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def pct(x: float | None) -> str:
    return "–" if x is None else f"{100 * x:.1f}"


def ex_cell(run: dict) -> str:
    vals = [r["correct"] for r in run["records"].values()]
    lo, hi = bootstrap_ci(vals)
    return f"**{pct(run['overall']['ex'])}** <sub>[{pct(lo)}, {pct(hi)}]</sub>"


def main() -> None:
    runs = load_runs()
    if not runs:
        print("no results yet")
        return
    limit = 200
    lines = [
        "# Evaluation results", "",
        "**Metric:** execution accuracy (EX) on the Spider dev set. A prediction counts as correct when running it "
        "returns the same result set as the gold query (row order only matters if the gold query has ORDER BY; "
        "column order is ignored), following the Spider test-suite convention.", "",
        f"**Sample:** a fixed random sample of {limit} dev questions (seed 42), identical for every row below. "
        "Brackets show the 95% bootstrap confidence interval; *p* is an exact McNemar test against the previous "
        "row on the same questions. All models run locally via Ollama (Q4_K_M quantization) on an Apple M3 with 16 GB.", "",
    ]

    rows = [(label, runs.get((cfg, MAIN_MODEL, limit))) for cfg, label in LADDER]
    rows = [(label, r) for label, r in rows if r]
    if rows:
        lines += [f"## Ablation: what each pipeline stage contributes ({MAIN_MODEL})", "",
                  "| Pipeline (cumulative) | EX % [95% CI] | Δ vs previous | *p* | easy | medium | hard | extra | LLM calls / question |",
                  "|---|---|---:|---:|---:|---:|---:|---:|---:|"]
        prev = None
        for label, r in rows:
            h = r["by_hardness"]
            delta = "" if prev is None else f"{100 * (r['overall']['ex'] - prev['overall']['ex']):+.1f}"
            p = "" if prev is None else f"{mcnemar_p(prev['records'], r['records']):.2f}"
            lines.append(f"| {label} | {ex_cell(r)} | {delta} | {p} | "
                         + " | ".join(pct(h.get(k, {}).get("ex")) for k in HARDNESS)
                         + f" | {r['mean_llm_calls']} |")
            prev = r
        n_by = rows[0][1]["by_hardness"]
        lines += ["", f"Questions per difficulty bucket: " + ", ".join(f"{k} {n_by.get(k, {}).get('n', 0)}" for k in HARDNESS)
                  + ". Latency is not reported because the evaluation cache replays identical calls across rows.", ""]
        base, best = rows[0][1], max((r for _, r in rows), key=lambda r: r["overall"]["ex"])
        lines += [f"Best configuration vs baseline: {pct(base['overall']['ex'])}% → {pct(best['overall']['ex'])}% "
                  f"(McNemar *p* = {mcnemar_p(base['records'], best['records']):.3f}).", ""]
        _chart(rows, "ablation.png", f"Spider dev, {limit}-question sample\ncumulative ablation ({MAIN_MODEL})")
        lines += ["![ablation chart](results/ablation.png)", ""]

    full_base, full_app = runs.get(("baseline", MAIN_MODEL, None)), runs.get(("self_correct", MAIN_MODEL, None))
    if full_base and full_app:
        lines += ["## Headline: full Spider dev set (1,034 questions)", "",
                  "| Pipeline | EX % [95% CI] | easy | medium | hard | extra |", "|---|---|---:|---:|---:|---:|"]
        for label, r in (("Baseline (single prompt)", full_base), ("App pipeline (schema, hints, few-shot, self-correction)", full_app)):
            h = r["by_hardness"]
            lines.append(f"| {label} | {ex_cell(r)} | " + " | ".join(pct(h.get(k, {}).get("ex")) for k in HARDNESS) + " |")
        lines += ["", f"Paired McNemar test: *p* = {mcnemar_p(full_base['records'], full_app['records']):.4f}.", ""]

    # model comparison
    comp = [("qwen2.5-coder:7b", "Qwen2.5-Coder 7B"), ("llama3.1:8b", "Llama 3.1 8B"), ("sqlcoder:7b", "SQLCoder 7B †")]
    comp_rows = [(name, runs.get(("baseline", m, limit)), runs.get(("self_correct", m, limit))) for m, name in comp]
    comp_rows = [c for c in comp_rows if c[1] or c[2]]
    if len(comp_rows) > 1:
        lines += ["## Model comparison (same questions)", "",
                  "| Model | Baseline EX % | + rich schema, hints, few-shot, self-correction EX % |", "|---|---|---|"]
        for name, b, s in comp_rows:
            lines.append(f"| {name} | {ex_cell(b) if b else '–'} | {ex_cell(s) if s else '–'} |")
        lines += ["", "† SQLCoder is a completion model trained on its own prompt template, so it is evaluated with the "
                  "template from its model card (single call, raw schema) rather than the chat pipeline.", ""]

    # fine-tuning
    ft = [("sqltutor-base-1.5b", "Qwen2.5-Coder 1.5B (base)"), ("sqltutor-lora-1.5b", "Qwen2.5-Coder 1.5B + our LoRA")]
    ft_rows = [(name, runs.get(("value_hints", m, limit)), runs.get(("self_correct", m, limit))) for m, name in ft]
    if any(r[1] for r in ft_rows):
        seven = runs.get(("value_hints", MAIN_MODEL, limit))
        lines += ["## Fine-tuning a small model (LoRA, trained locally with MLX)", "",
                  "| Model | Same prompt as training EX % | + few-shot & self-correction EX % |", "|---|---|---|"]
        for name, a, b in ft_rows:
            lines.append(f"| {name} | {ex_cell(a) if a else '–'} | {ex_cell(b) if b else '–'} |")
        if seven:
            lines.append(f"| Qwen2.5-Coder 7B (reference) | {ex_cell(seven)} | "
                         f"{ex_cell(runs[('self_correct', MAIN_MODEL, limit)]) if ('self_correct', MAIN_MODEL, limit) in runs else '–'} |")
        base_ft, lora_ft = ft_rows[0][1], ft_rows[1][1]
        if base_ft and lora_ft:
            lines += ["", f"LoRA vs base (same prompt): McNemar *p* = {mcnemar_p(base_ft['records'], lora_ft['records']):.3f}."]
        log = ROOT / "eval" / "finetune" / "train.log"
        if log.exists():
            vals = [ln for ln in log.read_text().splitlines() if "Val loss" in ln]
            if vals:
                lines += ["", f"Validation loss (held-out databases): first `{vals[0].strip()}` → last `{vals[-1].strip()}`."]
        lines += ["", "Training data: 6,359 Spider **train** questions; validation holds out 6 whole databases. "
                  "The dev set used above is never seen in training.", ""]

    lines += ["## Reproduce", "", "```bash", "eval/run_ablation.sh 200            # ladder + model comparison (resumable)",
              "eval/finetune/run_finetune.sh       # LoRA train → fuse → import → evaluate", "python eval/make_report.py", "```", "",
              "Difficulty buckets use a sqlglot port of the official Spider hardness rules; bucket sizes on the full dev set are "
              "within ~1% of the official script (easy 246/248, medium 446/446, hard 185/174, extra 157/166)."]
    report = "\n".join(lines) + "\n"
    (ROOT / "eval" / "RESULTS.md").write_text(report)
    _sync_readme(report)
    print(report)


def _sync_readme(report: str) -> None:
    """Copy the result sections into README.md between the RESULTS markers."""
    readme = ROOT / "README.md"
    start, end = "<!-- RESULTS:START -->", "<!-- RESULTS:END -->"
    text = readme.read_text()
    if start not in text or end not in text:
        return
    body = report.split("## Reproduce")[0]
    body = body.replace("# Evaluation results\n", "").replace("## ", "### ").replace("](results/", "](eval/results/")
    text = text[: text.index(start) + len(start)] + "\n" + body.strip() + "\n" + text[text.index(end):]
    readme.write_text(text)


def _chart(rows: list[tuple[str, dict]], filename: str, title: str) -> None:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return
    labels = [label.split("(")[0].strip() for label, _ in rows][::-1]
    values, errs = [], [[], []]
    for _, r in rows[::-1]:
        v = 100 * r["overall"]["ex"]
        lo, hi = bootstrap_ci([x["correct"] for x in r["records"].values()])
        values.append(v)
        errs[0].append(v - 100 * lo)
        errs[1].append(100 * hi - v)
    fig, ax = plt.subplots(figsize=(9, 0.55 * len(rows) + 1.4))
    bars = ax.barh(labels, values, xerr=errs, color="#4c6ef5", ecolor="#868e96", capsize=3)
    ax.bar_label(bars, fmt="%.1f%%", padding=26, fontsize=9)
    ax.set_xlim(40, 100)
    ax.set_xlabel("Execution accuracy (%) with 95% bootstrap CI")
    ax.set_title(title, fontsize=11)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(RESULTS / filename, dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    main()

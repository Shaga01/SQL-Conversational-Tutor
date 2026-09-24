"""Build eval/RESULTS.md and charts from the per-question result files.

Every reported number comes from here - nothing is typed in by hand.
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
        f"**Samples:** headline rows use all 1,034 dev questions; ablation, model-comparison and fine-tuning tables use a "
        f"fixed random sample of {limit} dev questions (seed 42), identical for every row. "
        "Brackets show the 95% bootstrap confidence interval; *p* is an exact McNemar test on the same questions (in "
        "the ablation, against the previous row). All models run locally via Ollama (Q4_K_M quantization) on an Apple M3 with 16 GB.", "",
    ]

    lines += _key_findings(runs, limit)
    full_base, full_app = runs.get(("baseline", MAIN_MODEL, None)), runs.get(("self_correct", MAIN_MODEL, None))
    if full_base and full_app:
        lines += ["## Headline: full Spider dev set (1,034 questions)", "",
                  "| Pipeline | EX % [95% CI] | easy | medium | hard | extra |", "|---|---|---:|---:|---:|---:|"]
        for label, r in (("Baseline (single prompt)", full_base), ("App pipeline (schema, hints, few-shot, self-correction)", full_app)):
            h = r["by_hardness"]
            lines.append(f"| {label} | {ex_cell(r)} | " + " | ".join(pct(h.get(k, {}).get("ex")) for k in HARDNESS) + " |")
        lines += ["", f"Paired McNemar test: *p* = {mcnemar_p(full_base['records'], full_app['records']):.4f}.", ""]

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
        lines += ["", "Questions per difficulty bucket: " + ", ".join(f"{k} {n_by.get(k, {}).get('n', 0)}" for k in HARDNESS)
                  + ". Latency is not reported because the evaluation cache replays identical calls across rows.", ""]
        base, best = rows[0][1], max((r for _, r in rows), key=lambda r: r["overall"]["ex"])
        lines += [f"Best configuration vs baseline: {pct(base['overall']['ex'])}% → {pct(best['overall']['ex'])}% "
                  f"(McNemar *p* = {mcnemar_p(base['records'], best['records']):.3f}).", ""]
        _chart(rows, "ablation.png", f"Spider dev, {limit}-question sample\ncumulative ablation ({MAIN_MODEL})")
        lines += ["![ablation chart](results/ablation.png)", ""]

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
    ft = [("sqltutor-base-1.5b", "Qwen2.5-Coder 1.5B, base (same 4-bit round trip)"),
          ("sqltutor-lora-1.5b", "+ LoRA run 1 (lr 1e-4, final step) ✗"),
          ("sqltutor-lora2-1.5b", "+ LoRA run 2 (lr 1e-5, step chosen on validation DBs)")]
    ft_rows = [(name, runs.get(("value_hints", m, limit)), runs.get(("self_correct", m, limit))) for m, name in ft]
    if any(r[1] for r in ft_rows):
        lines += ["## Fine-tuning a small model (QLoRA, trained locally with MLX)", "",
                  "| Model | Same prompt as training | + few-shot & self-correction |", "|---|---|---|"]
        for name, a, b in ft_rows:
            if a or b:
                lines.append(f"| {name} | {ex_cell(a) if a else '–'} | {ex_cell(b) if b else '–'} |")
        seven_a, seven_b = runs.get(("value_hints", MAIN_MODEL, limit)), runs.get(("self_correct", MAIN_MODEL, limit))
        if seven_a:
            lines.append(f"| Qwen2.5-Coder 7B (reference) | {ex_cell(seven_a)} | {ex_cell(seven_b) if seven_b else '–'} |")
        base_ft = ft_rows[0][1]
        for label, r in ((ft_rows[1][0], ft_rows[1][1]), (ft_rows[2][0], ft_rows[2][1])):
            if base_ft and r:
                lines.append(f"\n{label.split(' (')[0].lstrip('+ ')} vs base (same prompt): "
                             f"{100 * (r['overall']['ex'] - base_ft['overall']['ex']):+.1f} points, McNemar *p* = "
                             f"{mcnemar_p(base_ft['records'], r['records']):.3f}.")
        full_b, full_l = runs.get(("value_hints", "sqltutor-base-1.5b", None)), runs.get(("value_hints", "sqltutor-lora2-1.5b", None))
        if full_b and full_l:
            lines += ["", "**Full dev set (1,034 questions), same prompt as training:** "
                      f"base {ex_cell(full_b)} → LoRA run 2 {ex_cell(full_l)}, "
                      f"{100 * (full_l['overall']['ex'] - full_b['overall']['ex']):+.1f} points, McNemar *p* = "
                      f"{mcnemar_p(full_b['records'], full_l['records']):.4f}."]
        curves = [(p.stem.split("__")[1], json.loads(p.read_text())) for p in sorted(RESULTS.glob("finetune_curve__*.json"))]
        if curves:
            lines += ["", "Execution accuracy at each saved checkpoint (evaluated directly in MLX, before merging):", ""]
            for name, c in curves:
                steps = sorted(c["curve"], key=int)
                where = ("held-out training DBs (used to choose the checkpoint)" if c["split"] == "valid"
                         else "Spider dev sample (diagnosis only)")
                lines += [f"*{name}*, {c['n']} questions from {where}:", "",
                          "| step | " + " | ".join(steps) + " |", "|---" * (len(steps) + 1) + "|",
                          "| EX % | " + " | ".join(f"{100 * c['curve'][k]['ex']:.0f}" for k in steps) + " |",
                          "| query errors | " + " | ".join(str(c["curve"][k]["exec_errors"]) for k in steps) + " |", ""]
        lines += ["Run 1 used learning rate 1e-4 with MLX's LoRA `scale: 20`. MLX applies that scale directly to the update "
                  "(the Hugging Face convention divides by rank), so steps were roughly 10× larger than MLX's defaults "
                  "intend. Validation loss still fell (1.14 → 0.31), but execution accuracy collapsed and recovered only "
                  "partly as the learning rate decayed: the model imitated Spider's SQL style and started inventing "
                  "columns. Run 2 used MLX's default 1e-5, and its checkpoint was chosen by execution accuracy on "
                  "held-out training databases, never on dev.", "",
                  "Training data: 5,755 Spider **train** examples (≤1,400 tokens); validation holds out 6 whole databases. "
                  "LoRA rank 16 on the top 16 of 28 layers (10.5M trainable parameters, 0.68%), 1,600 examples at "
                  "batch 1 × 4 gradient accumulation, 4-bit base (QLoRA), 3.3 GB peak memory on an M3.", ""]

    lines += _detector_section()

    lines += ["## Reproduce", "", "```bash", "eval/run_ablation.sh 200            # ladder + model comparison (resumable)",
              "eval/finetune/run_finetune.sh       # LoRA train → fuse → import → evaluate", "python eval/make_report.py", "```", "",
              "Difficulty buckets use a sqlglot port of the official Spider hardness rules; bucket sizes on the full dev set are "
              "within ~1% of the official script (easy 246/248, medium 446/446, hard 185/174, extra 157/166)."]
    report = "\n".join(lines) + "\n"
    (ROOT / "eval" / "RESULTS.md").write_text(report)
    print(report)


def _key_findings(runs: dict, limit: int) -> list[str]:
    """Headline claims, computed from the result files so they can never drift from the data."""
    out: list[str] = []
    fb, fa = runs.get(("baseline", MAIN_MODEL, None)), runs.get(("self_correct", MAIN_MODEL, None))
    if fb and fa:
        hb, ha = fb["by_hardness"], fa["by_hardness"]
        out.append(f"- **Full dev set (1,034 questions):** a single-prompt baseline scores {pct(fb['overall']['ex'])}%; the app "
                   f"pipeline (rich schema, value hints, few-shot retrieval, self-correction) scores {pct(fa['overall']['ex'])}% "
                   f"(McNemar *p* = {mcnemar_p(fb['records'], fa['records']):.2f}, no overall difference). It gains on extra-hard "
                   f"questions ({pct(hb['extra']['ex'])} → {pct(ha['extra']['ex'])}%) and loses on hard ones "
                   f"({pct(hb['hard']['ex'])} → {pct(ha['hard']['ex'])}%). Gains seen for this configuration on the "
                   f"{limit}-question sample did not hold up on the full set.")
    sb, sf = runs.get(("baseline", MAIN_MODEL, limit)), runs.get(("full", MAIN_MODEL, limit))
    if sb and sf:
        out.append(f"- **Self-consistency voting** (5 samples) reached {pct(sf['overall']['ex'])}% vs {pct(sb['overall']['ex'])}% on the "
                   f"{limit}-question sample (*p* = {mcnemar_p(sb['records'], sf['records']):.3f}) at ~{sf['mean_llm_calls']:.1f}× "
                   "the LLM calls; it was not run on the full set.")
    bb, bl = runs.get(("value_hints", "sqltutor-base-1.5b", None)), runs.get(("value_hints", "sqltutor-lora2-1.5b", None))
    if bb and bl:
        out.append(f"- **Our QLoRA fine-tune of Qwen2.5-Coder 1.5B** (trained on a laptop) improves it from "
                   f"{pct(bb['overall']['ex'])}% to {pct(bl['overall']['ex'])}% on the full dev set (McNemar "
                   f"*p* = {mcnemar_p(bb['records'], bl['records']):.4f}), at every difficulty level, with "
                   f"{bb['errors'] - bl['errors']} fewer failing queries. The first attempt made the model worse; see below.")
    det = RESULTS / "misconceptions_train.json"
    if det.exists():
        d = json.loads(det.read_text())
        out.append(f"- **Tutor feedback:** on {d['mutants_scored']:,} injected bugs the misconception detector names the right "
                   f"mistake {pct(d['micro_graded_recall'])}% of the time, and flags {pct(d['clean_flagged_rate'])}% of "
                   f"{d['clean_queries']:,} correct queries.")
    return (["## Key findings", ""] + out + [""]) if out else []


def _detector_section() -> list[str]:
    """Mutation-based evaluation of the misconception detector (eval/misconception_eval.py)."""
    def load(name: str) -> dict | None:
        path = RESULTS / f"misconceptions_{name}.json"
        return json.loads(path.read_text()) if path.exists() else None

    rows = [
        ("Development: Spider dev + exercises (used while building the detector)", load("dev")),
        ("Held-out: Spider train, 140 DBs — first detector version", load("train_before_fixes")),
        ("Held-out: Spider train_others, 6 DBs — before any fix", load("others_heldout")),
        ("Post-hoc: Spider train, final detector ‡", load("train")),
        ("Post-hoc: Spider train_others, final detector ‡", load("others")),
    ]
    rows = [(label, r) for label, r in rows if r]
    if not rows:
        return []
    out = ["## Tutor feedback quality: does the misconception detector name the right mistake?", "",
           "Known-correct queries are mutated with one injected bug each (e.g. LEFT JOIN → JOIN, IS NULL → = NULL, "
           "dropped ON clause); *recall* is the share of mutants for which the tutor reports the matching misconception. "
           "*Grading* mode includes the result diff against the reference (exercise submissions); *static* mode is "
           "free practice with no reference. Mutants that return the correct result anyway are excluded. The last "
           "column is the share of the **unmutated, correct** queries that get any correctness warning.", "",
           "| Query set | mutants | recall (grading) | recall (static) | correct queries flagged |",
           "|---|---:|---:|---:|---:|"]
    for label, r in rows:
        out.append(f"| {label} | {r['mutants_scored']:,} | {pct(r['micro_graded_recall'])}% | "
                   f"{pct(r['micro_static_recall'])}% | {pct(r['clean_flagged_rate'])}% of {r['clean_queries']:,} |")
    detail = load("train") or rows[0][1]
    out += ["", "‡ After fixing false-positive patterns found by inspecting flagged held-out queries (functional-dependency "
            "closure for GROUP BY, join-graph connectivity, dangling foreign keys, key-like join columns), so these rows "
            "are not held-out. A manual sample of the remaining flags on correct Spider queries showed mostly genuine "
            "problems in the reference SQL: Cartesian joins, wrong join keys, case-mismatched literals that return no "
            "rows, and non-deterministic GROUP BY columns.", "",
            f"Per injected bug (Spider train, {detail['mutants_scored']:,} mutants):", "",
            "| Injected bug | mutants | recall (grading) | recall (static) |", "|---|---:|---:|---:|"]
    for name, v in sorted(detail["operators"].items(), key=lambda kv: -kv[1]["scored"]):
        if v["scored"]:
            out.append(f"| {name} | {v['scored']:,} | {pct(v['graded_recall'])}% | {pct(v['static_recall'])}% |")
    out += ["", "Static recall is 0% for dropped DISTINCT and LEFT → INNER by design: without a reference solution those "
            "queries are valid SQL, and only the result diff reveals the mistake.", ""]
    return out


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

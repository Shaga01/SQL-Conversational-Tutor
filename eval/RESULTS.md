# Evaluation results

**Metric:** execution accuracy (EX) on the Spider dev set. A prediction counts as correct when running it returns the same result set as the gold query (row order only matters if the gold query has ORDER BY; column order is ignored), following the Spider test-suite convention.

**Sample:** a fixed random sample of 200 dev questions (seed 42), identical for every row below. Brackets show the 95% bootstrap confidence interval; *p* is an exact McNemar test against the previous row on the same questions. All models run locally via Ollama (Q4_K_M quantization) on an Apple M3 with 16 GB.

## Ablation: what each pipeline stage contributes (qwen2.5-coder:7b)

| Pipeline (cumulative) | EX % [95% CI] | Δ vs previous | *p* | easy | medium | hard | extra | LLM calls / question |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| Baseline: one prompt, raw CREATE TABLE schema | **73.5** <sub>[67.5, 79.5]</sub> |  |  | 84.8 | 84.8 | 56.7 | 40.6 | 1.0 |
| + rich schema (descriptions, sample values) | **72.0** <sub>[66.0, 78.0]</sub> | -1.5 | 0.66 | 84.8 | 83.7 | 46.7 | 43.8 | 1.0 |
| + value hints (DB values matching the question) | **72.0** <sub>[65.5, 78.0]</sub> | +0.0 | 1.00 | 84.8 | 81.5 | 46.7 | 50.0 | 1.0 |
| + 3 retrieved few-shot examples (RAG over Spider train) | **74.5** <sub>[68.5, 80.0]</sub> | +2.5 | 0.44 | 80.4 | 85.9 | 53.3 | 53.1 | 1.0 |
| + execution-guided self-correction (≤2 rounds) | **76.5** <sub>[70.5, 82.0]</sub> | +2.0 | 0.12 | 80.4 | 89.1 | 53.3 | 56.2 | 1.1 |
| + self-consistency voting (5 samples) | **78.5** <sub>[72.5, 84.0]</sub> | +2.0 | 0.39 | 80.4 | 92.4 | 53.3 | 59.4 | 5.72 |
| + embedding-based schema linking | **78.5** <sub>[72.5, 84.0]</sub> | +0.0 | 1.00 | 80.4 | 91.3 | 56.7 | 59.4 | 5.7 |

Questions per difficulty bucket: easy 46, medium 92, hard 30, extra 32. Latency is not reported because the evaluation cache replays identical calls across rows.

Best configuration vs baseline: 73.5% → 78.5% (McNemar *p* = 0.087).

![ablation chart](results/ablation.png)

## Model comparison (same questions)

| Model | Baseline EX % | + rich schema, hints, few-shot, self-correction EX % |
|---|---|---|
| Qwen2.5-Coder 7B | **73.5** <sub>[67.5, 79.5]</sub> | **76.5** <sub>[70.5, 82.0]</sub> |
| Llama 3.1 8B | **71.0** <sub>[64.5, 77.0]</sub> | – |
| SQLCoder 7B † | **40.5** <sub>[33.5, 47.5]</sub> | – |

† SQLCoder is a completion model trained on its own prompt template, so it is evaluated with the template from its model card (single call, raw schema) rather than the chat pipeline.

## Tutor feedback quality: does the misconception detector name the right mistake?

Known-correct queries are mutated with one injected bug each (e.g. LEFT JOIN → JOIN, IS NULL → = NULL, dropped ON clause); *recall* is the share of mutants for which the tutor reports the matching misconception. *Grading* mode includes the result diff against the reference (exercise submissions); *static* mode is free practice with no reference. Mutants that return the correct result anyway are excluded. The last column is the share of the **unmutated, correct** queries that get any correctness warning.

| Query set | mutants | recall (grading) | recall (static) | correct queries flagged |
|---|---:|---:|---:|---:|
| Development: Spider dev + exercises (used while building the detector) | 3,000 | 98.9% | 97.3% | 1.6% of 1,062 |
| Held-out: Spider train, 140 DBs — first detector version | 18,408 | 99.3% | 97.8% | 2.1% of 6,997 |
| Held-out: Spider train_others, 6 DBs — before any fix | 3,188 | 100.0% | 100.0% | 8.6% of 1,659 |
| Post-hoc: Spider train, final detector ‡ | 19,613 | 99.1% | 97.7% | 1.6% of 6,997 |
| Post-hoc: Spider train_others, final detector ‡ | 3,178 | 100.0% | 100.0% | 0.4% of 1,659 |

‡ After fixing false-positive patterns found by inspecting flagged held-out queries (functional-dependency closure for GROUP BY, join-graph connectivity, dangling foreign keys, key-like join columns), so these rows are not held-out. A manual sample of the remaining flags on correct Spider queries showed mostly genuine problems in the reference SQL: Cartesian joins, wrong join keys, case-mismatched literals that return no rows, and non-deterministic GROUP BY columns.

Per injected bug (Spider train, 19,613 mutants):

| Injected bug | mutants | recall (grading) | recall (static) |
|---|---:|---:|---:|
| misspell column | 6,648 | 98.5% | 98.5% |
| misspell table | 6,588 | 99.3% | 99.3% |
| drop ON condition | 2,407 | 99.9% | 99.9% |
| join on unrelated ids | 1,191 | 97.9% | 97.9% |
| drop ORDER BY before LIMIT | 801 | 100.0% | 100.0% |
| change literal case | 696 | 100.0% | 100.0% |
| drop GROUP BY column | 618 | 100.0% | 100.0% |
| HAVING → WHERE | 390 | 100.0% | 100.0% |
| drop DISTINCT | 274 | 99.3% | 0.0% |

Static recall is 0% for dropped DISTINCT and LEFT → INNER by design: without a reference solution those queries are valid SQL, and only the result diff reveals the mistake.

## Reproduce

```bash
eval/run_ablation.sh 200            # ladder + model comparison (resumable)
eval/finetune/run_finetune.sh       # LoRA train → fuse → import → evaluate
python eval/make_report.py
```

Difficulty buckets use a sqlglot port of the official Spider hardness rules; bucket sizes on the full dev set are within ~1% of the official script (easy 246/248, medium 446/446, hard 185/174, extra 157/166).

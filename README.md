# SQL Tutor — explanation-first SQL learning with a local, measurable AI

An intelligent tutoring system for SQL. Learners write queries against a real database. When a query is wrong, the tutor finds *why* (wrong join type, `= NULL`, a LEFT JOIN undone by WHERE, …), shows how the database executes it step by step, and coaches with Socratic hints instead of handing over answers. It tracks per-skill mastery with Bayesian Knowledge Tracing and picks the next exercise to match.

Everything runs locally and costs nothing: open models served by [Ollama](https://ollama.com), SQLite, and a LoRA fine-tune trained on a laptop with Apple MLX.

[![CI](https://github.com/Shaga01/SQL-Conversational-Tutor/actions/workflows/ci.yml/badge.svg)](https://github.com/Shaga01/SQL-Conversational-Tutor/actions/workflows/ci.yml)

## Highlights

- **Grounded feedback, not free-form guessing.** Deterministic analyzers decide *what* is wrong: 20+ misconception rules over the SQL syntax tree, SQLite error translation, database-aware checks, and result-set diffs against a reference. The LLM only decides *how to say it*, from a FACTS block it is told not to go beyond.
- **Feedback quality is measured, too.** Mutation testing injects known bugs into ~7,000 correct Spider queries; the tutor names the right mistake for ~99% of them and flags under 2% of the correct queries. Held-out and post-hoc numbers are reported separately.
- **An agentic text-to-SQL pipeline where each stage is measured.** Schema representation, value retrieval, few-shot RAG, execution-guided self-correction and self-consistency voting can each be toggled, and each is benchmarked on Spider with confidence intervals and significance tests.
- **Our own fine-tuned model.** A QLoRA adapter for Qwen2.5-Coder-1.5B, trained on Spider's train split on an M3 MacBook (3.3 GB peak memory). It beats the base model by a statistically significant margin on the full dev set, measured through an identical serving path, with the checkpoint chosen on held-out training databases.
- **Adaptive learning.** Bayesian Knowledge Tracing over a 16-skill prerequisite graph, with exercises chosen from the learner's zone of proximal development and explanation depth adapted to the inferred level.
- **A secure sandbox.** Four independent layers (AST validation, read-only connection, SQLite authorizer, time and memory limits), backed by adversarial tests (infinite recursive CTEs, Cartesian explosions, `ATTACH`, `zeroblob` memory bombs, …).

## Results

<!-- RESULTS:START -->
**Metric:** execution accuracy (EX) on the Spider dev set. A prediction counts as correct when running it returns the same result set as the gold query (row order only matters if the gold query has ORDER BY; column order is ignored), following the Spider test-suite convention.

**Samples:** headline rows use all 1,034 dev questions; ablation, model-comparison and fine-tuning tables use a fixed random sample of 200 dev questions (seed 42), identical for every row. Brackets show the 95% bootstrap confidence interval; *p* is an exact McNemar test on the same questions (in the ablation, against the previous row). All models run locally via Ollama (Q4_K_M quantization) on an Apple M3 with 16 GB.

### Key findings

- **Full dev set (1,034 questions):** a single-prompt baseline scores 78.4%; the app pipeline (rich schema, value hints, few-shot retrieval, self-correction) scores 77.8% (McNemar *p* = 0.66, no overall difference). It gains on extra-hard questions (47.1 → 54.1%) and loses on hard ones (74.1 → 68.7%). Gains seen for this configuration on the 200-question sample did not hold up on the full set.
- **Self-consistency voting** (5 samples) reached 78.5% vs 73.5% on the 200-question sample (*p* = 0.087) at ~5.7× the LLM calls; it was not run on the full set.
- **Our QLoRA fine-tune of Qwen2.5-Coder 1.5B** (trained on a laptop) improves it from 57.0% to 60.5% on the full dev set (McNemar *p* = 0.0033), at every difficulty level, with 58 fewer failing queries. The first attempt made the model worse; see below.
- **Tutor feedback:** on 19,613 injected bugs the misconception detector names the right mistake 99.1% of the time, and flags 1.6% of 6,997 correct queries.

### Headline: full Spider dev set (1,034 questions)

| Pipeline | EX % [95% CI] | easy | medium | hard | extra |
|---|---|---:|---:|---:|---:|
| Baseline (single prompt) | **78.4** <sub>[75.9, 80.9]</sub> | 90.6 | 84.5 | 74.1 | 47.1 |
| App pipeline (schema, hints, few-shot, self-correction) | **77.8** <sub>[75.3, 80.3]</sub> | 89.8 | 83.4 | 68.7 | 54.1 |

Paired McNemar test: *p* = 0.6587.

### Ablation: what each pipeline stage contributes (qwen2.5-coder:7b)

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

![ablation chart](eval/results/ablation.png)

### Model comparison (same questions)

| Model | Baseline EX % | + rich schema, hints, few-shot, self-correction EX % |
|---|---|---|
| Qwen2.5-Coder 7B | **73.5** <sub>[67.5, 79.5]</sub> | **76.5** <sub>[70.5, 82.0]</sub> |
| Llama 3.1 8B | **71.0** <sub>[64.5, 77.0]</sub> | **73.0** <sub>[66.5, 79.0]</sub> |
| SQLCoder 7B † | **40.5** <sub>[33.5, 47.5]</sub> | – |

† SQLCoder is a completion model trained on its own prompt template, so it is evaluated with the template from its model card (single call, raw schema) rather than the chat pipeline.

### Fine-tuning a small model (QLoRA, trained locally with MLX)

| Model | Same prompt as training | + few-shot & self-correction |
|---|---|---|
| Qwen2.5-Coder 1.5B, base (same 4-bit round trip) | **54.5** <sub>[47.5, 61.5]</sub> | **50.0** <sub>[43.0, 57.0]</sub> |
| + LoRA run 1 (lr 1e-4, final step) ✗ | **42.5** <sub>[35.5, 49.5]</sub> | **29.5** <sub>[23.5, 35.5]</sub> |
| + LoRA run 2 (lr 1e-5, step chosen on validation DBs) | **59.0** <sub>[52.0, 65.5]</sub> | **53.5** <sub>[46.5, 60.5]</sub> |
| Qwen2.5-Coder 7B (reference) | **72.0** <sub>[65.5, 78.0]</sub> | **76.5** <sub>[70.5, 82.0]</sub> |

LoRA run 1 vs base (same prompt): -12.0 points, McNemar *p* = 0.002.

LoRA run 2 vs base (same prompt): +4.5 points, McNemar *p* = 0.150.

**Full dev set (1,034 questions), same prompt as training:** base **57.0** <sub>[54.0, 60.0]</sub> → LoRA run 2 **60.5** <sub>[57.5, 63.6]</sub>, +3.6 points, McNemar *p* = 0.0033.

Execution accuracy at each saved checkpoint (evaluated directly in MLX, before merging):

*adapters*, 60 questions from Spider dev sample (diagnosis only):

| step | 0 | 400 | 800 | 1200 | 1600 |
|---|---|---|---|---|---|
| EX % | 60 | 38 | 25 | 48 | 47 |
| query errors | 12 | 27 | 32 | 24 | 20 |

*adapters_run2*, 100 questions from held-out training DBs (used to choose the checkpoint):

| step | 0 | 200 | 400 | 600 | 800 | 1000 | 1200 | 1400 | 1600 |
|---|---|---|---|---|---|---|---|---|---|
| EX % | 80 | 84 | 78 | 77 | 81 | 85 | 85 | 84 | 83 |
| query errors | 6 | 10 | 18 | 16 | 12 | 12 | 8 | 9 | 9 |

Run 1 used learning rate 1e-4 with MLX's LoRA `scale: 20`. MLX applies that scale directly to the update (the Hugging Face convention divides by rank), so steps were roughly 10× larger than MLX's defaults intend. Validation loss still fell (1.14 → 0.31), but execution accuracy collapsed and recovered only partly as the learning rate decayed: the model imitated Spider's SQL style and started inventing columns. Run 2 used MLX's default 1e-5, and its checkpoint was chosen by execution accuracy on held-out training databases, never on dev.

Training data: 5,755 Spider **train** examples (≤1,400 tokens); validation holds out 6 whole databases. LoRA rank 16 on the top 16 of 28 layers (10.5M trainable parameters, 0.68%), 1,600 examples at batch 1 × 4 gradient accumulation, 4-bit base (QLoRA), 3.3 GB peak memory on an M3.

### Tutor feedback quality: does the misconception detector name the right mistake?

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
<!-- RESULTS:END -->

Full tables, per-difficulty breakdowns and methodology: [eval/RESULTS.md](eval/RESULTS.md). Per-question predictions for every run are committed in [eval/results/](eval/results/).

## Architecture

```mermaid
flowchart LR
    UI["React + CodeMirror<br/>editor · step visualizer · progress · chat"] -- REST + SSE --> API[FastAPI]

    subgraph Tutor
        IR["Intent router<br/>(JSON-schema output)"]
        MC["Misconception detector<br/>AST rules · error translation · result diff"]
        ST["Execution stepper<br/>FROM→JOIN→WHERE→GROUP→…"]
        BKT["Learner model<br/>Bayesian Knowledge Tracing"]
        LLM["Grounded explainer<br/>streams from FACTS only"]
    end

    subgraph T2S["Text-to-SQL agent"]
        P1[rich schema + value hints] --> P2[few-shot retrieval] --> P3[generate] --> P4{execute}
        P4 -- error --> P5[self-correct] --> P4
        P4 -- ok --> P6[self-consistency vote]
    end

    API --> IR --> MC & ST & T2S
    MC & ST --> LLM
    API --> BKT
    MC & ST & T2S --> SB[("Sandboxed SQLite<br/>read-only · authorizer · limits")]
    T2S & LLM & IR --> OL["Ollama (local)<br/>Qwen2.5-Coder · nomic-embed"]
```

| Path | What it does |
|---|---|
| [backend/app/sandbox.py](backend/app/sandbox.py) | Four-layer secure execution |
| [backend/app/text2sql/](backend/app/text2sql/) | Agent pipeline, schema linking, value hints, example retrieval |
| [backend/app/tutor/misconceptions.py](backend/app/tutor/misconceptions.py) | Mistake detection with evidence |
| [backend/app/tutor/stepper.py](backend/app/tutor/stepper.py) | Clause-by-clause execution with row counts |
| [backend/app/tutor/learner.py](backend/app/tutor/learner.py) | BKT mastery and adaptive exercise selection |
| [backend/app/tutor/conversation.py](backend/app/tutor/conversation.py) | Intent routing, grounded prompts, answer-leak guard, offline fallback |
| [backend/app/datasets/shop.py](backend/app/datasets/shop.py) | Deterministic teaching database designed to expose misconceptions |
| [eval/](eval/) | Spider harness, hardness port, ablation and report scripts |
| [eval/finetune/](eval/finetune/) | Data builder, MLX LoRA config, train → fuse → import → evaluate |

## Design decisions

- **Why deterministic analysis plus an LLM, and not the LLM alone?** A tutor that invents mistakes is worse than none. Rules are exact and testable; the test suite includes correct queries that must produce *zero* findings. The LLM adds tone, adapts to the learner's level, and handles open questions.
- **Why a synthetic dataset for teaching?** On a 4-row table most wrong queries return the right answer by accident. The shop database is generated so that customers without orders, NULL emails, cancelled orders and duplicate names make each classic mistake change the result.
- **Why grade by result equivalence?** Many different queries are correct. Grading runs both queries and compares result sets (multiset semantics, column order ignored, row order only when the reference sorts), the same rule used by the benchmark.
- **Why report failures honestly?** The rich schema representation did *not* help on Spider, whose schemas are small and self-descriptive, and the self-correction pipeline's gain on the 200-question sample disappeared on the full dev set. Both results stay in the tables. The first fine-tuning run made the model worse; the report keeps it together with the diagnosis (a learning rate about 10× too high for MLX's LoRA scaling convention) and the corrected run.
- **Why mutation testing for the tutor?** Hand-written test cases only show that rules work on examples I thought of. Injecting bugs into thousands of real queries on databases I never looked at exposed four real detector flaws (for example, it suggested HAVING when the actual problem was a missing GROUP BY), and it also found errors in Spider's own reference SQL.
- **Infrastructure failures are never scored.** If the model server errors, the harness retries and otherwise leaves the question unscored for a resumed run, so an outage cannot pass as a wrong answer.

## Run it

Prerequisites: Python 3.11+, Node 20+, [Ollama](https://ollama.com).

```bash
ollama pull qwen2.5-coder:7b && ollama pull nomic-embed-text

python3 -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt pytest
uvicorn app.main:app --app-dir backend --port 8000        # API on :8000

cd frontend && npm install && npm run dev                 # UI on :5173 (proxies /api)
```

Without Ollama the app still works. The analyzer, stepper, grading and learner model are deterministic, and the tutor falls back to template feedback.

Docker: `docker compose up --build` (Ollama stays on the host so it can use the GPU), then open http://localhost:8080.

Tests: `cd backend && python -m pytest` (the LLM is faked, so the tests are fast and offline).

## Reproduce the evaluation

```bash
# Spider (official release, ~200 MB) -> eval/data/spider_data/
eval/run_ablation.sh 200          # ablation ladder + model comparison on a fixed 200-question sample
eval/run_full_dev.sh              # baseline vs app pipeline on all 1,034 dev questions
eval/finetune/run_finetune.sh     # LoRA fine-tune with MLX, import to Ollama, evaluate
python eval/make_report.py        # regenerate eval/RESULTS.md and this README's results section
```

Runs are resumable and LLM responses are cached, so re-running a report is instant.

## Project history

v1 was a course capstone (Boise State CS-695); its proposal and poster are in [docs/capstone/](docs/capstone/). v2 is a rebuild: a secure sandbox, the evaluation harness, the tutor features and fine-tuning. All results above come from v2's measured runs.

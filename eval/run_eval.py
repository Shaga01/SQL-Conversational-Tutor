"""Execution-accuracy benchmark for the text-to-SQL pipeline on Spider (dev).

Examples
--------
    # ablation ladder on a fixed 200-question sample (seed 42)
    python eval/run_eval.py --config baseline --limit 200
    python eval/run_eval.py --config full --limit 200

    # final number on the whole dev set
    python eval/run_eval.py --config full

Each run writes ``eval/results/<run>.jsonl`` (one line per question, appended as it
goes so runs can be resumed) and ``eval/results/<run>.summary.json``.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from collections import defaultdict
from dataclasses import asdict, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "eval"))

import httpx  # noqa: E402
from app.compare import has_top_level_order_by, results_match  # noqa: E402
from app.llm import LLMUnavailable, OllamaClient  # noqa: E402
from app.sandbox import SandboxError, run_query  # noqa: E402
from app.text2sql.examples import ExampleStore  # noqa: E402
from app.text2sql.pipeline import PipelineConfig, Text2SQL  # noqa: E402
from hardness import hardness, sql_features  # noqa: E402

SPIDER = ROOT / "eval" / "data" / "spider_data"
RESULTS = ROOT / "eval" / "results"


def configs(model: str) -> dict[str, PipelineConfig]:
    """Cumulative ablation ladder: each rung adds one technique to the previous one."""
    base = PipelineConfig.baseline(model)
    rich = replace(base, rich_schema=True)
    hints = replace(rich, value_hints=True)
    shots = replace(hints, few_shot=3)
    fix = replace(shots, self_correct_rounds=2)
    vote = replace(fix, n_samples=5)
    return {
        "baseline": base,
        "rich_schema": rich,
        "value_hints": hints,
        "few_shot": shots,
        "self_correct": fix,
        "full": vote,
        "full_linking": replace(vote, schema_linking=True),
    }


class InfraError(Exception):
    pass


def run_with_retry(pipeline: Text2SQL, question: str, path: Path, attempts: int = 3):
    for attempt in range(attempts):
        try:
            return pipeline.run(question, path)
        except (httpx.HTTPError, LLMUnavailable) as exc:
            if attempt == attempts - 1:
                raise InfraError(f"{type(exc).__name__}: {exc}") from exc
            time.sleep(5 * (attempt + 1))


def load_questions(limit: int | None, seed: int) -> list[tuple[int, dict]]:
    data = json.loads((SPIDER / "dev.json").read_text())
    indexed = list(enumerate(data))
    if limit and limit < len(indexed):
        indexed = sorted(random.Random(seed).sample(indexed, limit), key=lambda p: p[0])
    return indexed


def db_path(db_id: str) -> Path:
    return SPIDER / "database" / db_id / f"{db_id}.sqlite"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="full")
    ap.add_argument("--model", default="qwen2.5-coder:7b")
    ap.add_argument("--limit", type=int, default=None, help="random sample size (default: all 1034)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--name", default=None, help="run name (default: derived from config/model/limit)")
    args = ap.parse_args()

    cfg = configs(args.model)[args.config]
    name = args.name or f"{args.config}__{args.model.replace(':', '-').replace('/', '_')}__{args.limit or 'all'}"
    RESULTS.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS / f"{name}.jsonl"

    client = OllamaClient(cache_path=ROOT / "eval" / "data" / "llm_cache.sqlite")
    examples = ExampleStore.from_spider(SPIDER / "train_spider.json", client) if cfg.few_shot else None
    pipeline = Text2SQL(cfg, client=client, examples=examples)

    done = {}
    if out_path.exists():
        for line in out_path.read_text().splitlines():
            rec = json.loads(line)
            done[rec["idx"]] = rec
    questions = load_questions(args.limit, args.seed)
    print(f"[{name}] {len(questions)} questions, {len(done)} already done. config={asdict(cfg)}")

    with out_path.open("a") as out:
        for n, (idx, item) in enumerate(questions, 1):
            if idx in done:
                continue
            path = db_path(item["db_id"])
            gold_sql = item["query"]
            try:
                gold = run_query(path, gold_sql, max_rows=100_000, timeout_s=30, validate=False)
            except SandboxError as exc:
                print(f"  ! gold query {idx} failed: {exc}")
                continue

            t0 = time.perf_counter()
            try:
                res = run_with_retry(pipeline, item["question"], path)
                pred_sql, pred_error = res.sql, res.error
                correct = res.ok and res.result is not None and not res.result.truncated and results_match(
                    gold.rows, res.result.rows, has_top_level_order_by(gold_sql))
                llm_calls = sum(1 for s in res.trace if s.stage in ("generate", "self_correct"))
            except InfraError as exc:
                # the model server failed, not the model: don't score it; a resumed run retries it
                print(f"  ! skipped {idx} (infrastructure): {exc}", flush=True)
                continue
            except Exception as exc:  # the harness must never die on one bad example
                pred_sql, pred_error, correct, llm_calls = "", f"{type(exc).__name__}: {exc}", False, 0
            record = {
                "idx": idx, "db_id": item["db_id"], "question": item["question"], "gold": gold_sql,
                "pred": pred_sql, "correct": bool(correct), "error": pred_error,
                "hardness": hardness(gold_sql), "features": sql_features(gold_sql),
                "latency_s": round(time.perf_counter() - t0, 2), "llm_calls": llm_calls,
            }
            done[idx] = record
            out.write(json.dumps(record) + "\n")
            out.flush()
            if n % 10 == 0:
                acc = sum(r["correct"] for r in done.values()) / len(done)
                print(f"  {n}/{len(questions)}  running EX={acc:.3f}", flush=True)

    summarize(name, cfg, list(done.values()), args)


def summarize(name: str, cfg: PipelineConfig, records: list[dict], args: argparse.Namespace) -> None:
    def ex(rs: list[dict]) -> dict:
        return {"n": len(rs), "ex": round(sum(r["correct"] for r in rs) / len(rs), 4) if rs else None}

    by_hard: dict[str, list[dict]] = defaultdict(list)
    by_feat: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        by_hard[r["hardness"]].append(r)
        for feat, present in r["features"].items():
            if present:
                by_feat[feat].append(r)

    summary = {
        "run": name, "config_name": args.config, "model": args.model, "limit": args.limit, "seed": args.seed,
        "config": asdict(cfg), "overall": ex(records),
        "by_hardness": {h: ex(by_hard[h]) for h in ("easy", "medium", "hard", "extra", "unknown") if by_hard[h]},
        "by_feature": {f: ex(rs) for f, rs in sorted(by_feat.items())},
        "errors": sum(1 for r in records if r["error"]),
        "mean_latency_s": round(sum(r["latency_s"] for r in records) / max(1, len(records)), 2),
        "mean_llm_calls": round(sum(r["llm_calls"] for r in records) / max(1, len(records)), 2),
    }
    (RESULTS / f"{name}.summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps({k: summary[k] for k in ("run", "overall", "by_hardness", "errors", "mean_latency_s")}, indent=2))


if __name__ == "__main__":
    main()

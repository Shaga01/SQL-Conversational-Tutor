"""Execution accuracy vs. training step, evaluated directly in MLX (no merging/Ollama import).

Answers: does task accuracy follow validation loss as training proceeds?

    python eval/finetune/checkpoint_curve.py --adapters eval/finetune/adapters --split dev --n 60
    python eval/finetune/checkpoint_curve.py --adapters eval/finetune/adapters_run2 --split valid

--split valid uses the held-out *training* databases (never the dev set), which is the
legitimate way to choose a checkpoint; dev is reserved for the final measurement.
"""

import argparse
import json
import random
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from mlx_lm import generate, load

from app.compare import has_top_level_order_by, results_match
from app.sandbox import SandboxError, run_query
from app.text2sql.pipeline import PipelineConfig, Text2SQL, extract_sql

FT = ROOT / "eval" / "finetune"
SPIDER = ROOT / "eval" / "data" / "spider_data"


def validation_questions() -> list[dict]:
    """Spider train questions from the databases held out for validation (same split as prepare_data.py)."""
    train = json.loads((SPIDER / "train_spider.json").read_text())
    held_out = {json.loads(line)["db_id"] for line in (FT / "data" / "valid_meta.jsonl").read_text().splitlines()}
    return [q for q in train if q["db_id"] in held_out]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--adapters", default=str(FT / "adapters"))
    ap.add_argument("--split", choices=["dev", "valid"], default="dev")
    ap.add_argument("--n", type=int, default=60)
    args = ap.parse_args()
    adapters = Path(args.adapters)
    pool = json.loads((SPIDER / "dev.json").read_text()) if args.split == "dev" else validation_questions()
    N = min(args.n, len(pool))
    questions = random.Random(7).sample(pool, N)
    builder = Text2SQL(PipelineConfig(rich_schema=True, value_hints=True, few_shot=0, self_correct_rounds=0), client=None)
    steps = [0] + sorted(int(p.name[:7]) for p in adapters.glob("0*_adapters.safetensors"))
    curve = {}
    for step in steps:
        if step == 0:
            model, tok = load(str(FT / "base-4bit"))
        else:
            tmp = Path(tempfile.mkdtemp())
            shutil.copy(adapters / "adapter_config.json", tmp)
            shutil.copy(adapters / f"{step:07d}_adapters.safetensors", tmp / "adapters.safetensors")
            model, tok = load(str(FT / "base-4bit"), adapter_path=str(tmp))
        correct = errors = 0
        for q in questions:
            db = SPIDER / "database" / q["db_id"] / f"{q['db_id']}.sqlite"
            prompt = tok.apply_chat_template(builder.build_prompt(q["question"], db, trace=[]),
                                             add_generation_prompt=True, tokenize=False)
            sql = extract_sql(generate(model, tok, prompt=prompt, max_tokens=250))
            gold = run_query(db, q["query"], max_rows=100_000, timeout_s=30, validate=False)
            try:
                pred = run_query(db, sql, max_rows=100_000, timeout_s=5)
                correct += results_match(gold.rows, pred.rows, has_top_level_order_by(q["query"]))
            except SandboxError:
                errors += 1
        curve[step] = {"ex": round(correct / N, 3), "exec_errors": errors}
        print(step, curve[step], flush=True)
    out = ROOT / "eval" / "results" / f"finetune_curve__{adapters.name}__{args.split}.json"
    out.write_text(json.dumps({"adapters": adapters.name, "split": args.split, "n": N, "curve": curve}, indent=2))


if __name__ == "__main__":
    main()

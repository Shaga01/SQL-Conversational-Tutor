"""Build LoRA training data from the Spider *train* split.

Prompts are produced by the very same ``Text2SQL.build_prompt`` the app and the
benchmark use (rich schema + value hints, no few-shot), so the model is trained on
exactly the format it will see at inference. The Spider *dev* split - our
benchmark - is never touched here.

Output: eval/finetune/data/{train,valid}.jsonl in the chat format mlx-lm expects.
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.sandbox import SandboxError, run_query  # noqa: E402
from app.text2sql.pipeline import PipelineConfig, Text2SQL  # noqa: E402

SPIDER = ROOT / "eval" / "data" / "spider_data"
OUT = ROOT / "eval" / "finetune" / "data"
MAX_CHARS = 6500  # ~1800 tokens; longer prompts are dropped to keep --max-seq-length 2048

# must match the "value_hints" rung of the ablation ladder (see eval/run_eval.py)
PROMPT_CONFIG = PipelineConfig(rich_schema=True, value_hints=True, few_shot=0, self_correct_rounds=0)


def main() -> None:
    items = json.loads((SPIDER / "train_spider.json").read_text())
    builder = Text2SQL(PROMPT_CONFIG, client=None)  # build_prompt needs no LLM without linking/few-shot
    rows, skipped = [], {"gold_fails": 0, "too_long": 0}
    for n, item in enumerate(items):
        db = SPIDER / "database" / item["db_id"] / f"{item['db_id']}.sqlite"
        try:
            run_query(db, item["query"], max_rows=1, timeout_s=5, validate=False)
        except SandboxError:
            skipped["gold_fails"] += 1
            continue
        messages = builder.build_prompt(item["question"], db, trace=[])
        if sum(len(m["content"]) for m in messages) > MAX_CHARS:
            skipped["too_long"] += 1
            continue
        sql = " ".join(item["query"].split())  # normalise whitespace
        rows.append({"messages": messages + [{"role": "assistant", "content": f"```sql\n{sql}\n```"}],
                     "db_id": item["db_id"]})
        if n % 1000 == 0:
            print(f"  {n}/{len(items)}")

    # hold out whole databases for validation so val loss measures generalisation to unseen schemas
    dbs = sorted({r["db_id"] for r in rows})
    random.Random(0).shuffle(dbs)
    val_dbs = set(dbs[: max(1, len(dbs) // 20)])
    OUT.mkdir(parents=True, exist_ok=True)
    splits = {"train": [r for r in rows if r["db_id"] not in val_dbs], "valid": [r for r in rows if r["db_id"] in val_dbs]}
    for name, split in splits.items():
        random.Random(1).shuffle(split)
        with (OUT / f"{name}.jsonl").open("w") as f:
            for r in split:
                f.write(json.dumps({"messages": r["messages"]}) + "\n")
    print(f"train={len(splits['train'])} valid={len(splits['valid'])} (valid DBs: {len(val_dbs)}) skipped={skipped}")


if __name__ == "__main__":
    main()

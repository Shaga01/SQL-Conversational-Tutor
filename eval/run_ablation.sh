#!/usr/bin/env bash
# Runs the cumulative ablation ladder + a model comparison on the same fixed sample.
# Usage: eval/run_ablation.sh [LIMIT]    (default 200; runs are resumable)
set -euo pipefail
cd "$(dirname "$0")/.."
LIMIT="${1:-200}"
export PYTHONUNBUFFERED=1
PY=.venv/bin/python
for cfg in baseline rich_schema value_hints few_shot self_correct full full_linking; do
  $PY eval/run_eval.py --config "$cfg" --limit "$LIMIT"
done
for model in sqlcoder:7b llama3.1:8b; do
  $PY eval/run_eval.py --config baseline --model "$model" --limit "$LIMIT"
  $PY eval/run_eval.py --config self_correct --model "$model" --limit "$LIMIT"
done
$PY eval/make_report.py

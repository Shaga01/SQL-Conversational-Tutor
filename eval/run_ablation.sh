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
# SQLCoder is a completion model: evaluated with its own documented prompt (single call).
$PY eval/run_eval.py --config baseline --model sqlcoder:7b --limit "$LIMIT"
$PY eval/run_eval.py --config baseline --model llama3.1:8b --limit "$LIMIT"
$PY eval/run_eval.py --config self_correct --model llama3.1:8b --limit "$LIMIT"
$PY eval/make_report.py

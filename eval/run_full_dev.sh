#!/usr/bin/env bash
# Headline numbers on the complete Spider dev set (1034 questions): baseline vs. the
# pipeline used by the app (rich schema, hints, few-shot, self-correction).
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONUNBUFFERED=1
for cfg in baseline self_correct; do
  .venv/bin/python eval/run_eval.py --config "$cfg"
done
.venv/bin/python eval/make_report.py

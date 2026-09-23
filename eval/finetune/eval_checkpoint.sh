#!/usr/bin/env bash
# Fuse one chosen checkpoint, import it exactly like the base model, and benchmark it on dev.
# Usage: eval/finetune/eval_checkpoint.sh <adapter_dir> <step> <ollama_name>
set -euo pipefail
cd "$(dirname "$0")/../.."
export HF_HUB_OFFLINE=1 PYTHONUNBUFFERED=1
PY=.venv/bin/python
FT=eval/finetune
ADAPTERS=$1; STEP=$2; NAME=$3
SEL=$(mktemp -d)
cp "$ADAPTERS/adapter_config.json" "$SEL/"
cp "$ADAPTERS/$(printf %07d "$STEP")_adapters.safetensors" "$SEL/adapters.safetensors"
rm -rf "$FT/fused_$NAME"
$PY -m mlx_lm fuse --model $FT/base-4bit --adapter-path "$SEL" --save-path "$FT/fused_$NAME" --dequantize
$PY $FT/make_modelfile.py "$FT/fused_$NAME" "$FT/Modelfile.$NAME"
ollama create "$NAME" --quantize q4_K_M -f "$FT/Modelfile.$NAME"
for cfg in value_hints self_correct; do
  $PY eval/run_eval.py --config "$cfg" --model "$NAME" --limit 200
done

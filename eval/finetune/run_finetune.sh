#!/usr/bin/env bash
# Train, fuse, import and benchmark the LoRA model. Needs the GPU to itself (~13 GB peak).
set -euo pipefail
cd "$(dirname "$0")/../.."
export PYTHONUNBUFFERED=1
export HF_HUB_OFFLINE=1   # base model is already cached; no internet needed
PY=.venv/bin/python
FT=eval/finetune
BASE_ID=Qwen/Qwen2.5-Coder-1.5B-Instruct

# 1. free GPU memory held by Ollama
ollama ps | awk 'NR>1 {print $1}' | xargs -r -n1 ollama stop || true

# 2. train (resumable from the last checkpoint is not needed: ~1 hour)
[ -s "$FT/data/train.jsonl" ] || $PY $FT/prepare_data.py
$PY -m mlx_lm lora --config $FT/lora.yaml 2>&1 | grep -v -i warning | tee $FT/train.log

# 3. merge the adapter into the base weights
$PY -m mlx_lm fuse --model $BASE_ID --adapter-path $FT/adapters --save-path $FT/fused

# 4. import base and fine-tuned through the same path (same quantisation + template)
BASE_DIR=$($PY -c "from huggingface_hub import snapshot_download as s; print(s('$BASE_ID'))")
$PY $FT/make_modelfile.py "$BASE_DIR" $FT/Modelfile.base
$PY $FT/make_modelfile.py $FT/fused $FT/Modelfile.lora
ollama create sqltutor-base-1.5b --quantize q4_K_M -f $FT/Modelfile.base
ollama create sqltutor-lora-1.5b --quantize q4_K_M -f $FT/Modelfile.lora

# 5. benchmark both on the same 200-question sample
for model in sqltutor-base-1.5b sqltutor-lora-1.5b; do
  for cfg in value_hints self_correct; do
    $PY eval/run_eval.py --config "$cfg" --model "$model" --limit 200
  done
done
$PY eval/make_report.py

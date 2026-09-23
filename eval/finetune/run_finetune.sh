#!/usr/bin/env bash
# QLoRA: quantize -> train -> fuse -> import to Ollama -> benchmark. Needs ~4 GB of GPU memory.
set -euo pipefail
cd "$(dirname "$0")/../.."
export PYTHONUNBUFFERED=1
export HF_HUB_OFFLINE=1   # base model is already cached; no internet needed
PY=.venv/bin/python
FT=eval/finetune
BASE_ID=Qwen/Qwen2.5-Coder-1.5B-Instruct

# 1. free GPU memory held by Ollama
ollama ps | awk 'NR>1 {print $1}' | xargs -n1 ollama stop 2>/dev/null || true

# 2. 4-bit base for training, and the same 4-bit weights dequantized as the fair baseline
[ -d $FT/base-4bit ] || $PY -m mlx_lm convert --hf-path $BASE_ID -q --q-bits 4 --q-group-size 64 --mlx-path $FT/base-4bit
[ -d $FT/base-roundtrip ] || $PY -m mlx_lm convert --hf-path $FT/base-4bit --dequantize --mlx-path $FT/base-roundtrip

# 3. data + training
[ -s "$FT/data/train.jsonl" ] || $PY $FT/prepare_data.py
$PY -m mlx_lm lora --config $FT/lora.yaml 2>&1 | tr '\r' '\n' | grep -v -i warning | tee $FT/train.log

# 4. merge adapter into the (dequantized) base weights
rm -rf $FT/fused
$PY -m mlx_lm fuse --model $FT/base-4bit --adapter-path $FT/adapters --save-path $FT/fused --dequantize

# 5. import both through the identical path (same weights precision, template, Q4_K_M quantization)
$PY $FT/make_modelfile.py $FT/base-roundtrip $FT/Modelfile.base
$PY $FT/make_modelfile.py $FT/fused $FT/Modelfile.lora
ollama create sqltutor-base-1.5b --quantize q4_K_M -f $FT/Modelfile.base
ollama create sqltutor-lora-1.5b --quantize q4_K_M -f $FT/Modelfile.lora

# 6. benchmark both on the same 200-question sample
for model in sqltutor-base-1.5b sqltutor-lora-1.5b; do
  for cfg in value_hints self_correct; do
    $PY eval/run_eval.py --config "$cfg" --model "$model" --limit 200
  done
done
$PY eval/make_report.py

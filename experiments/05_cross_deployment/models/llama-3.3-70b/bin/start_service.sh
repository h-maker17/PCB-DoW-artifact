#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "$0")/.." && pwd)"
PY="${PCB_GPU_PYTHON:-python3}"
MODEL="${PCB_MODEL_DIR:-$ROOT/models}/Llama-3.3-70B-Instruct-FP8"
TP="${TP:-1}"
KV="${KV_CACHE_DTYPE:-auto}"
mkdir -p "$ROOT/cache"
exec 9>"$ROOT/cache/experiment.lock"
flock -n 9 || { echo 'GPU lock held by another service/experiment' >&2; exit 2; }
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 VLLM_USE_V1=1
export TOKENIZERS_PARALLELISM=false PYTHONUNBUFFERED=1 VLLM_WORKER_MULTIPROC_METHOD=spawn
export HF_HOME="$ROOT/cache/huggingface" XDG_CACHE_HOME="$ROOT/cache/xdg" VLLM_CACHE_ROOT="$ROOT/cache/vllm"
export TORCHINDUCTOR_CACHE_DIR="$ROOT/cache/torchinductor" TRITON_CACHE_DIR="$ROOT/cache/triton"
ID="$(date -u +%Y%m%dT%H%M%SZ)-$$"
export LAB_SERVER_EVIDENCE="$ROOT/evidence/service-$ID"
mkdir -p "$LAB_SERVER_EVIDENCE"
nvidia-smi > "$LAB_SERVER_EVIDENCE/nvidia-smi.txt"
"$PY" -m pip freeze > "$LAB_SERVER_EVIDENCE/gpu-environment.lock"
ARGS=(serve "$MODEL" --host 127.0.0.1 --port 8000 --api-server-count 1
      --served-model-name llama-3.3-70b --dtype bfloat16 --quantization modelopt
      --tensor-parallel-size "$TP" --max-model-len 32768
      --gpu-memory-utilization 0.99 --block-size 16 --kv-cache-dtype "$KV"
      --max-num-seqs 1 --max-num-batched-tokens 2048 --enforce-eager
      --enable-prefix-caching --enable-prompt-tokens-details
      --enable-auto-tool-choice --tool-call-parser llama3_json)
printf '%q ' "$PY" "$ROOT/code/service/server.py" "${ARGS[@]}" > "$LAB_SERVER_EVIDENCE/command.txt"
printf '\n' >> "$LAB_SERVER_EVIDENCE/command.txt"
printf '%s\n' "$LAB_SERVER_EVIDENCE" > "$ROOT/evidence/latest-service.txt"
exec "$PY" "$ROOT/code/service/server.py" "${ARGS[@]}"

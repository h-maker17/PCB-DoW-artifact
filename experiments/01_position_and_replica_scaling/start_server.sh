#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "$0")" && pwd)"
PROJECT="${PCB_ENV_DIR:-$ROOT/env}"
: "${GPU_PYTHON:=$PROJECT/.venv/bin/python}"
: "${MODEL_PATH:=${PCB_MODEL_DIR:-$ROOT/models}/Qwen3-8B}"
: "${PORT:=8000}"
: "${PREFIX_CACHE:=1}"
test -x "$GPU_PYTHON" || { echo "Missing GPU environment: $GPU_PYTHON"; exit 2; }
test -f "$MODEL_PATH/config.json" || { echo "Missing model config: $MODEL_PATH"; exit 2; }
mkdir -p "$ROOT/cache" "$ROOT/logs" "$PROJECT/cache"
exec 9>"$PROJECT/cache/experiment.lock"
flock -n 9 || { echo "Existing experiment/server owns the GPU lock. Wait until it finishes."; exit 2; }
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 VLLM_USE_V1=1
export TOKENIZERS_PARALLELISM=false PYTHONUNBUFFERED=1 VLLM_WORKER_MULTIPROC_METHOD=spawn
export HF_HOME="$ROOT/cache/huggingface" XDG_CACHE_HOME="$ROOT/cache/xdg"
export VLLM_CACHE_ROOT="$ROOT/cache/vllm"
export TORCHINDUCTOR_CACHE_DIR="$ROOT/cache/torchinductor" TRITON_CACHE_DIR="$ROOT/cache/triton"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-$$"
export LAB_SERVER_EVIDENCE="$ROOT/evidence/server-$RUN_ID"
mkdir -p "$LAB_SERVER_EVIDENCE"
nvidia-smi > "$LAB_SERVER_EVIDENCE/nvidia-smi.txt"
"$GPU_PYTHON" -m pip freeze > "$LAB_SERVER_EVIDENCE/gpu-environment.lock"
sha256sum "$MODEL_PATH/config.json" "$MODEL_PATH/tokenizer_config.json" > "$LAB_SERVER_EVIDENCE/model-config-hashes.txt"
CACHE_FLAG="--enable-prefix-caching"
if [[ "$PREFIX_CACHE" == "0" ]]; then
    CACHE_FLAG="--no-enable-prefix-caching"
elif [[ "$PREFIX_CACHE" != "1" ]]; then
    echo "PREFIX_CACHE must be 0 or 1"; exit 2
fi
ARGS=(serve "$MODEL_PATH" --host 127.0.0.1 --port "$PORT" --api-server-count 1
    --served-model-name qwen3-8b --dtype bfloat16 --tensor-parallel-size 1
    --max-model-len 32768 --gpu-memory-utilization 0.80 --block-size 16
    "$CACHE_FLAG" --enable-prompt-tokens-details
    --enable-auto-tool-choice --tool-call-parser hermes)
printf '%q ' "$GPU_PYTHON" "$ROOT/server.py" "${ARGS[@]}" > "$LAB_SERVER_EVIDENCE/command.txt"
printf '\n' >> "$LAB_SERVER_EVIDENCE/command.txt"
cd "$ROOT"
# The foreground process retains file descriptor 9 and therefore the shared GPU lock.
"$GPU_PYTHON" "$ROOT/server.py" "${ARGS[@]}" 2>&1 | tee "$ROOT/logs/server-$RUN_ID.log"

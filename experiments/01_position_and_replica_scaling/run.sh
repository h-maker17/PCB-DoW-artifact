#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "$0")" && pwd)"
test -x "$ROOT/.venv-autogen/bin/python" || { echo "First run: bash install.sh"; exit 2; }
export TIKTOKEN_CACHE_DIR="$ROOT/offline/tiktoken-cache"
cd "$ROOT"
exec "$ROOT/.venv-autogen/bin/python" "$ROOT/lab.py" "$@"

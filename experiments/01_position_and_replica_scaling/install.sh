#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "$0")" && pwd)"
BASE="/path/to/workspace"
PROJECT="$BASE/多智能体系统实验数据/mas-qwen3-system"
: "${PYTHON_BIN:=$PROJECT/runtime/python/bin/python3.11}"
cd "$ROOT"
test -x "$PYTHON_BIN" || { echo "Missing persistent Python: $PYTHON_BIN"; exit 2; }
"$PYTHON_BIN" verify_package.py
if [[ ! -x "$ROOT/.venv-autogen/bin/python" ]]; then
    "$PYTHON_BIN" -m venv "$ROOT/.venv-autogen"
fi
PY="$ROOT/.venv-autogen/bin/python"
mkdir -p "$ROOT/evidence"
"$PY" -m pip install --no-index --find-links "$ROOT/offline" -r requirements-client.lock
"$PY" -m pip check
"$PY" -m pip freeze > "$ROOT/evidence/client-installed.lock"
"$PY" -m unittest test_handoff test_lab -v > "$ROOT/evidence/cloud-structural-tests.log" 2>&1
"$PY" inventory.py
echo "CLIENT_READY. Structural tests passed. No GPU experiment was started."

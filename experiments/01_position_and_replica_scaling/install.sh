#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "$0")" && pwd)"
: "${PYTHON_BIN:=python3}"
cd "$ROOT"
if [[ ! -x "$ROOT/.venv-autogen/bin/python" ]]; then
    "$PYTHON_BIN" -m venv "$ROOT/.venv-autogen"
fi
PY="$ROOT/.venv-autogen/bin/python"
mkdir -p "$ROOT/evidence"
"$PY" -m pip install -r requirements-client.lock
"$PY" -m pip check
"$PY" -m pip freeze > "$ROOT/evidence/client-installed.lock"
"$PY" -m unittest test_lab test_shared_prefix_lab -v
echo "CLIENT_READY"

#!/usr/bin/env bash
set -euo pipefail
TASK_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONPATH="$TASK_ROOT/code"
exec "$TASK_ROOT/env/client/bin/python" -u -m tasklab.runner sanity

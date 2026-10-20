#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$ROOT"
export PYTHONPATH="$ROOT/code"
exec "${PCB_CLIENT_PYTHON:-$ROOT/env/client/bin/python}" -u -m e5lab.prepare

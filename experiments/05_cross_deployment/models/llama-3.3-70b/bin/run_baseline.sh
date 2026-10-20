#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "$0")/.." && pwd)"
PY="${PCB_CLIENT_PYTHON:-python3}"
curl -fsS http://127.0.0.1:8000/health >/dev/null
SESSION="$("$PY" "$ROOT/code/orchestration/run_all.py" init)"
LOG="$ROOT/logs/baseline-$(basename "$SESSION").log"
nohup setsid "$PY" -u "$ROOT/code/orchestration/run_all.py" run --session "$SESSION" > "$LOG" 2>&1 < /dev/null &
printf '%s\n' "$!" > "$ROOT/evidence/baseline.pid"
printf '%s\n' "$LOG" > "$ROOT/evidence/latest-baseline-log.txt"
echo "BASELINE_STARTED session=$SESSION pid=$! log=$LOG"

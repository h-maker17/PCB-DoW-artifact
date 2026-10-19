#!/usr/bin/env bash
set -euo pipefail
cd "/path/to/workspace/多智能体系统实验数据/fcb-dow-research/experiments/2026-10-05-e5-append-G"
export PYTHONPATH="/path/to/workspace/多智能体系统实验数据/fcb-dow-research/experiments/2026-10-05-e5-append-G/code"
exec "/path/to/workspace/多智能体系统实验数据/fcb-dow-research/experiments/2026-10-05-e5-append-G/env/client/bin/python" -u -m e5lab.runner formal

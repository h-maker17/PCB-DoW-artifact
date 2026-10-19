# PCB-DoW: Experiment Code and Data

This repository contains the experiment code, configurations, input data, and aggregated results for the paper
**"Poisoned Tools, Broken Caches: Prefix-Cache Denial-of-Wallet Attacks on Redundant Multi-Agent LLM Systems"**
(anonymous submission).

All compromised tools in these experiments are local functions or stubs inside the experiment harness.
The repository contains no deployable tool server, MCP server, or poisoned public repository.

## Repository layout

| Folder | Paper section | Original workspace directory |
|---|---|---|
| `experiments/01_position_and_replica_scaling` | Sec. 4.2, Fig. 3; App. B (marker-only tool) | `autogen-handoff-lab` |
| `experiments/02_layout_return_size_broadcast` | Sec. 4.3, Fig. 4; App. B, Fig. 7 | `fcb-dow-research/experiments/2026-10-05-e5-append-G` |
| `experiments/03_retrieval_pipeline` | Sec. 4.4, Table 1A | `fcb-dow-research/E_e2e_rag` |
| `experiments/04_commercial_api` | Sec. 4.4, Table 1B; App. A | `I-commercial-api` |
| `experiments/05_cross_deployment` | Sec. 4.5, Fig. 5(a) | `fcb-dow-research/experiments/2026-10-03-*` |
| `experiments/06_cross_engine_replay` | Sec. 4.5, Fig. 5(b) | `fcb-dow-research/B_cross_engine` |
| `experiments/07_concurrent_dispatch` | Sec. 4.5 (dispatch schedules) | `G-experiment` (formal v7) |
| `experiments/08_task_quality` | Sec. 4.6, Table 2 | `fcb-dow-research/experiments/2026-10-05-task-generalization` |
| `experiments/09_defenses_and_detection` | Sec. 5.1–5.2, Fig. 6, Fig. 8; App. D | `fcb-dow-research/H_defense` |
| `experiments/10_framework_inspection` | App. C, Table 4 | `SCAN_a2a3_v7`, `SCAN_a2a3_remeasure` |

Inside each experiment folder:

- `code/`, `bin/`: runners, analysis scripts, and launch scripts.
- `config/`: frozen protocols and preregistered parameters.
- `datasets/`: experiment inputs.
- `data/`, `tables/`, or `results/`: aggregated results from which the numbers in the paper are computed.

## Paths

The scripts keep the relative layout of the original workspace. Absolute workspace roots were replaced with
`/path/to/workspace` (Linux GPU server) or `D:\path\to\workspace` (Windows workstation). To rerun an
experiment, place (or symlink) each folder at the original directory name listed above under your own
workspace root, then edit the root constant in that experiment's `common.py`, `lab.py`, or `bin/*.sh`.

## Software environment

- Local serving: vLLM 0.11.2 with automatic prefix caching (16-token blocks) and `--enable-prompt-tokens-details`.
  Each experiment's `server.py` wraps the vLLM API server and only exposes reported zero cache hits
  (`cached_tokens = 0`) instead of omitting the field.
- Cross-engine replay: SGLang 0.5.10. Pinned dependencies are in `06_cross_engine_replay/config/requirements.lock`.
- Agent framework: AutoGen 0.7.5. Client lock files are in `01_position_and_replica_scaling/requirements-client.lock`.
- Models: Qwen3-8B, Llama-3.3-70B-Instruct-FP8, GLM-4-9B, gpt-oss-120B, DeepSeek-V4-Flash
  (download from their official model cards).
- Commercial API: DeepSeek `deepseek-v4-pro`. No credentials are included. Provide your own key through the
  mechanism used in `04_commercial_api/code/formal_v4.py`.

## Data notes

- `06_cross_engine_replay/datasets/replay-plan.json.gz` contains the archived prompt-token sequences used for
  the SGLang replay. It is also read by `09_defenses_and_detection`. Decompress it before use:
  `gunzip -k replay-plan.json.gz`.
- `03_retrieval_pipeline` uses the public `sentence-transformers/all-MiniLM-L6-v2` embedding model with a local
  FAISS index. Model weights are not included.
- `08_task_quality` uses the LongBench HotpotQA, GovReport, and TriviaQA subsets. The processed 30-record
  samples are in `datasets/processed/`. Source information is in `datasets/raw/source.json`.
- `10_framework_inspection/probe/config/frameworks.json` pins each framework repository and commit. Framework
  source trees, virtual environments, and tokenizer files are not included.

## Not included

To keep the repository small, the following are not included:

- Per-request raw request/response dumps.
- Model weights, tokenizer files, and virtual environments.
- Third-party framework source trees.
- Logs and internal review records.
- Figure-plotting scripts. The exceptions are `02/.../e5lab/plots.py` and `08/.../tasklab/plots.py`, which the
  analysis modules import.

The aggregated result files above are sufficient to recompute the reported statistics.

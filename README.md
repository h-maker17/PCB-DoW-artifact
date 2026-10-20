# PCB-DoW: Experiment Code and Data

This repository contains the experiment code, configurations, input data, and aggregated results for the paper
**"Poisoned Tools, Broken Caches: Prefix-Cache Denial-of-Wallet Attacks on Redundant Multi-Agent LLM Systems"**
(anonymous submission).

All compromised tools in these experiments are local functions or stubs inside the experiment harness.
The repository contains no deployable tool server, MCP server, or poisoned public repository.

## Repository layout

| Folder | Paper section |
|---|---|
| `experiments/01_position_and_replica_scaling` | Sec. 4.2, Fig. 3; App. B (marker-only tool) |
| `experiments/02_layout_return_size_broadcast` | Sec. 4.3, Fig. 4; App. B, Fig. 7 |
| `experiments/03_retrieval_pipeline` | Sec. 4.4, Table 1A |
| `experiments/04_commercial_api` | Sec. 4.4, Table 1B; App. A |
| `experiments/05_cross_deployment` | Sec. 4.5, Fig. 5(a) |
| `experiments/06_cross_engine_replay` | Sec. 4.5, Fig. 5(b); App. B |
| `experiments/07_concurrent_dispatch` | Sec. 4.5 (dispatch schedules) |
| `experiments/08_task_quality` | Sec. 4.6, Table 2 |
| `experiments/09_defenses_and_detection` | Sec. 5.1–5.2, Fig. 6, Fig. 8; App. D |
| `experiments/10_framework_inspection` | App. C, Table 4 |

Inside each experiment folder:

- `code/`, `bin/`: runners, analysis scripts, and launch scripts.
- `config/`: frozen protocols and preregistered parameters.
- `datasets/`: experiment inputs.
- `data/`, `tables/`, or `results/`: aggregated results from which the numbers in the paper are computed.

## Paths and environment variables

All scripts resolve their own folder from the script location, so the repository can be cloned anywhere.
Machine-specific locations are read from environment variables:

| Variable | Meaning | Default |
|---|---|---|
| `PCB_MODEL_DIR` | Folder that holds the model weights (one sub-folder per model) | `models/` inside the experiment folder |
| `PCB_GPU_PYTHON` | Python interpreter of the GPU serving environment (vLLM) | `python3` |
| `PCB_CLIENT_PYTHON` | Python interpreter of the AutoGen client environment | `python3` or `env/client/bin/python` |
| `PCB_ENV_DIR`, `PCB_DEEPSEEK_ENV` | Serving environments used by `01_*` and the DeepSeek-V4-Flash deployment | `env/` inside the experiment folder |
| `PCB_GPU_SITE_PACKAGES` | `site-packages` of the GPU environment (gpt-oss Harmony encoding preparation only) | required for that script |
| `PCB_LLAMA_TOKENIZER` | Tokenizer path for `07_concurrent_dispatch` | `$PCB_MODEL_DIR/Llama-3.3-70B-Instruct-FP8` |
| `PCB_COMMERCIAL_ROOT` | Root of the commercial API experiment | `04_commercial_api/` |
| `PCB_SCAN_ROOT` | Root of the framework probe used by the LCP re-measurement | `10_framework_inspection/probe/` |

Paths recorded inside result files (for example the run directory of each archived request) are given relative to
the repository root (`experiments/...`). The per-request raw dumps they point to are not included (see below).

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

## Reproducing the reported numbers from the included results

The following analysis entry points run on the included aggregated results and regenerate the tables in their
`tables/` folder (checked to be identical to the shipped tables):

- `03_retrieval_pipeline`: `python3 code/analyze_formal.py` (Table 1A).
- `06_cross_engine_replay`: `python3 code/analyze.py` (Fig. 5(b), App. B residual attribution).
- `09_defenses_and_detection`: `python3 code/analyze.py` (Sec. 5.1–5.2, App. D).
- `04_commercial_api`: `formal_v4_statistics.ratio_bootstrap` applied to the matched block costs in
  `results/formal_v4_result.json` gives CAF = 10.0639 with 95% CI [9.8333, 10.3425] (Table 1B).

The other analysis scripts read the per-request raw dumps, which are not included.

## Integrity notes

Several configuration and result files record SHA-256 hashes of the source files that were executed (for example
`04_commercial_api/config/runtime_policy/*.json`, `07_concurrent_dispatch/config/experiment-manifest.json`, and the
manifests under `10_framework_inspection`). These files were left byte-identical so that the recorded hashes still
match, with one exception: in `04_commercial_api/code/`, `calibration_v2.py`, `calibration_recovery.py`, and
`external_reconciliation.py` were edited to remove a local path and recorded operator messages. Their hashes therefore
differ from the values recorded for the original run. This affects only the gated production entry points, which also
require the original raw logs and an API key and cannot be re-executed from this repository.

## Not included

To keep the repository small, the following are not included:

- Per-request raw request/response dumps.
- Model weights, tokenizer files, and virtual environments.
- Third-party framework source trees.
- Logs and internal review records.
- Figure-plotting scripts. The exceptions are `02/.../e5lab/plots.py` and `08/.../tasklab/plots.py`, which the
  analysis modules import.

Every number reported in the paper can be read from the included tables and result files.

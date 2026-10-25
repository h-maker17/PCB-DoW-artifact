"""Set this process's directories before importing native DeerFlow; never load ambient .env."""
import os, pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2] / 'preparation/deerflow_night'


def prepare():
    phase = pathlib.Path(os.environ.get('SCAN_DF_PHASE_ROOT', str(ROOT))).resolve()
    os.chdir(phase)
    for d in ('tmp', 'cache/huggingface', 'runtime', 'skills'):
        (phase / d).mkdir(parents=True, exist_ok=True)
    (phase / 'empty.env').touch(exist_ok=True)
    for key, rel in {'TEMP': 'tmp', 'TMP': 'tmp', 'HF_HOME': 'cache/huggingface', 'DEER_FLOW_PROJECT_ROOT': '.',
                     'DEER_FLOW_HOME': 'runtime', 'DEER_FLOW_ENV_FILE': 'empty.env',
                     'DEER_FLOW_SKILLS_PATH': 'skills'}.items():
        os.environ[key] = str(phase / rel)
    for key, value in {'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1', 'TOKENIZERS_PARALLELISM': 'false',
                       'OMP_NUM_THREADS': '1', 'MKL_NUM_THREADS': '1', 'CUDA_VISIBLE_DEVICES': '',
                       'ANONYMIZED_TELEMETRY': 'False'}.items(): os.environ[key] = value
    # Explicitly neutralize provider/tracing endpoints in this process without reading values.
    for key in ('OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'LANGFUSE_PUBLIC_KEY', 'LANGFUSE_SECRET_KEY',
                'LANGCHAIN_API_KEY', 'LANGSMITH_API_KEY'):
        os.environ[key] = ''
    os.environ['LANGCHAIN_TRACING_V2'] = 'false'

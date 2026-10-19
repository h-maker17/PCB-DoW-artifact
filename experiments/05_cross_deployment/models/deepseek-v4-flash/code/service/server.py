"""Native vLLM 0.30.0; no cache-zero monkeypatch or input rewrite."""
import hashlib,inspect,json,os,sys
from pathlib import Path
from importlib.metadata import version
if __name__=='__main__':
    if version('vllm')!='0.30.0':
        raise RuntimeError('Engine version changed; inspect and record before launching')
    from vllm.entrypoints.openai.chat_completion.serving import _make_prompt_tokens_details
    source=inspect.getsource(_make_prompt_tokens_details)
    cold=_make_prompt_tokens_details(True,0,None,None)
    if cold is None or cold.cached_tokens!=0:
        raise RuntimeError('Native cached_tokens=0 reporting failed; no counts inferred')
    evidence=Path(os.environ['LAB_SERVER_EVIDENCE'])
    (evidence/'native-zero-metering.py').write_text(source,encoding='utf-8')
    (evidence/'native-metering.json').write_text(json.dumps(dict(vllm=version('vllm'),
        zero_cache_reported=True,source_sha256=hashlib.sha256(source.encode()).hexdigest(),
        installed_files_modified=False,patch_applied=False),indent=2)+'\n')
    from vllm.entrypoints.cli.main import main
    main()

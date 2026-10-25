"""Dedicated vLLM 0.11.2 single API process. Only change: expose reported zero cache hits."""
import hashlib
import inspect
import json
import os
import sys
import textwrap
from importlib.metadata import version
from pathlib import Path

OLD = "if self.enable_prompt_tokens_details and final_res.num_cached_tokens:"
NEW = "if self.enable_prompt_tokens_details and final_res.num_cached_tokens is not None:"


def patch_source(source):
    source = textwrap.dedent(source)
    if source.count(OLD) != 1:
        raise RuntimeError("Unexpected vLLM source. Refusing to patch or infer missing counts.")
    return source, source.replace(OLD, NEW, 1)


def install_patch():
    if version("vllm") != "0.11.2":
        raise RuntimeError("Expected vLLM 0.11.2. Use its existing GPU environment.")
    import vllm.entrypoints.openai.serving_chat as module
    cls = module.OpenAIServingChat
    method = "chat_completion_full_generator"
    original, patched = patch_source(inspect.getsource(getattr(cls, method)))
    evidence = Path(os.environ.get("LAB_SERVER_EVIDENCE", "server-evidence"))
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / "original-method.py").write_text(original, encoding="utf-8")
    (evidence / "patched-method.py").write_text(patched, encoding="utf-8")
    metadata = dict(vllm=version("vllm"), method=method, original_sha256=hashlib.sha256(original.encode()).hexdigest(),
                    patched_sha256=hashlib.sha256(patched.encode()).hexdigest(),
                    purpose="Expose engine-reported cached_tokens=0; keep None unknown; non-stream only",
                    installed_files_modified=False)
    (evidence / "metering-patch.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    namespace = {}
    exec(compile("from __future__ import annotations\n" + patched,
                 "<vllm-reported-zero-metering>", "exec"), module.__dict__, namespace)
    setattr(cls, method, namespace[method])
    print(json.dumps(metadata), flush=True)


if __name__ == "__main__":
    install_patch()
    from harmony_tokenize_patch import install_harmony_tokenize_patch

    install_harmony_tokenize_patch()
    if sys.argv[1:] == ["--check-patch"]:
        sys.exit(0)
    # Multiple API processes would not inherit a parent-only Python patch.
    if "--api-server-count" not in sys.argv:
        sys.argv.extend(["--api-server-count", "1"])
    if sys.argv[sys.argv.index("--api-server-count") + 1] != "1":
        raise RuntimeError("This lab requires exactly one API process.")
    from vllm.entrypoints.cli.main import main

    main()

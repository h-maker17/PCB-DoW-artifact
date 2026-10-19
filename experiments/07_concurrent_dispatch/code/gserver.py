"""G-private vLLM 0.11.2 API with audited non-stream real-zero patch."""
import hashlib,inspect,json,os,sys,textwrap
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from importlib.metadata import version
OLD="if self.enable_prompt_tokens_details and final_res.num_cached_tokens:"
NEW="if self.enable_prompt_tokens_details and final_res.num_cached_tokens is not None:"
def patch_source(source):
    source=textwrap.dedent(source)
    if source.count(OLD)!=1:raise RuntimeError("Unexpected metering source")
    return source,source.replace(OLD,NEW,1)
def install_patch():
    if version("vllm")!="0.11.2":raise RuntimeError("G requires vLLM0.11.2")
    import vllm.entrypoints.openai.serving_chat as m
    original,patched=patch_source(inspect.getsource(m.OpenAIServingChat.chat_completion_full_generator))
    if hashlib.sha256(original.encode()).hexdigest()!="10ceb607a7312ca4ad1c9bcc4581ebbc5434a0097d5ddf5edc6977c012ba3a5d":
        raise RuntimeError("Historical/reference non-stream source hash mismatch")
    dest=Path(os.environ["G_PATCH_EVIDENCE_DIR"]);dest.mkdir(parents=True,exist_ok=True)
    for name,data in [("original-method.py",original),("patched-method.py",patched)]:
        (dest/name).write_text(data)
    meta={"vllm":version("vllm"),"method":"chat_completion_full_generator","original_sha256":hashlib.sha256(original.encode()).hexdigest(),
          "patched_sha256":hashlib.sha256(patched.encode()).hexdigest(),"installed_shared_files_modified":False,
          "private_source_only":True,"semantics":"engine real zero preserved; None remains unknown; non-stream only"}
    (dest/"metering-patch.json").write_text(json.dumps(meta,indent=2))
    ns={};exec(compile("from __future__ import annotations\n"+patched,"<G-real-zero>","exec"),m.__dict__,ns)
    m.OpenAIServingChat.chat_completion_full_generator=ns["chat_completion_full_generator"]
    print(json.dumps(meta),flush=True)

def install_http_idle_evidence():
    # Read the actual G API Uvicorn config instance; no request/model hook.
    import uvicorn,functools
    original=uvicorn.Config.__init__
    @functools.wraps(original)
    def observed(config,*args,**kwargs):
        original(config,*args,**kwargs)
        actual=int(config.timeout_keep_alive)
        if actual!=600:raise RuntimeError("G R6 requires actual HTTP idle timeout600s before accepting requests")
        dest=Path(os.environ["G_PATCH_EVIDENCE_DIR"]);dest.mkdir(parents=True,exist_ok=True)
        path=dest/"actual-HTTP-idle-server.json"
        record={"server_keepalive_seconds":actual,"host":config.host,"port":config.port,"pid":os.getpid(),"source":"actual uvicorn.Config instance of G API process"}
        with path.open("x") as f:json.dump(record,f,indent=2);f.flush();os.fsync(f.fileno())
    uvicorn.Config.__init__=observed

if __name__=="__main__":
    install_patch()
    if sys.argv[1:]==["--check-patch"]:raise SystemExit(0)
    install_http_idle_evidence()
    from vllm.entrypoints.cli.main import main
    main()

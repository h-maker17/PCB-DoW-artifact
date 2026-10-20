"""Foreground/background per-model server; uses existing environment only."""
import argparse,fcntl,json,os,shlex,subprocess,sys
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
import os
_PATH_KEYS=('model_path','gpu_python','client_python','batch_root','project_root','resource_environment_root',
            'cuda_toolkit_root','cuda_link_library_dir','cuda_supplemental_include_dir','cuda_vendor_library_dir','local_kernel_cache_dir')
def _resolve_profile(cfg,root):
    """Expand ${VAR} references; resolve relative path settings against the model folder."""
    out=dict(cfg)
    for k in _PATH_KEYS:
        v=out.get(k)
        if isinstance(v,str):
            v=os.path.expandvars(v)
            if not v.startswith('$') and not os.path.isabs(v):
                v=str((root/v).resolve())
            out[k]=v
    return out
CFG=_resolve_profile(json.loads((ROOT/'config/model.json').read_text()),ROOT)
PY=CFG['gpu_python']
stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
def write(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def serve():
    lock=open(Path(CFG['batch_root'])/'evidence/gpu-instance.lock','a+')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    os.set_inheritable(lock.fileno(),True)
    e=Path(os.environ['LAB_SERVER_EVIDENCE']);e.mkdir(exist_ok=True)
    env=dict(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',VLLM_USE_V1='1',
        TOKENIZERS_PARALLELISM='false',PYTHONUNBUFFERED='1',VLLM_WORKER_MULTIPROC_METHOD='spawn',
        CUDA_VISIBLE_DEVICES=CFG['gpus'],HF_HOME=str(ROOT/'cache/huggingface'),
        XDG_CACHE_HOME=str(ROOT/'cache/xdg'),VLLM_CACHE_ROOT=str(ROOT/'cache/vllm'),
        TORCHINDUCTOR_CACHE_DIR=str(ROOT/'cache/torchinductor'),TRITON_CACHE_DIR=str(ROOT/'cache/triton'))
    os.environ.update(env)
    (e/'nvidia-smi.txt').write_text(subprocess.check_output(['nvidia-smi'],text=True))
    (e/'environment.lock').write_text(subprocess.check_output([PY,'-m','pip','freeze'],text=True))
    args=['serve',CFG['model_path'],'--host','127.0.0.1','--port','8000','--api-server-count','1',
        '--served-model-name',CFG['served_name'],'--dtype','bfloat16','--tensor-parallel-size',str(CFG['tp']),
        '--max-model-len','32768','--block-size','16','--gpu-memory-utilization',str(CFG['gpu_memory_utilization']),
        '--kv-cache-dtype',CFG['kv_cache_dtype'],'--max-num-seqs','1','--max-num-batched-tokens','2048',
        '--enforce-eager','--enable-prefix-caching','--enable-prompt-tokens-details',
        '--enable-auto-tool-choice','--tool-call-parser',CFG['tool_parser']]
    if CFG['quantization']:args+=['--quantization',CFG['quantization']]
    if CFG['id']=='glm4-9b':args+=['--tool-parser-plugin',str(ROOT/'code/service/glm4_native_parser.py')]
    command=[PY,str(ROOT/'code/service/server.py'),*args]
    (e/'command.txt').write_text(shlex.join(command)+'\n',encoding='utf-8')
    write(e/'config.json',CFG)
    os.execv(PY,command)

if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('action',choices=('launch','serve'));mode=a.parse_args().action
    if mode=='serve':serve()
    else:
        import urllib.request
        try:
            urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=2)
        except Exception:pass
        else:raise RuntimeError('A server is already listening; stop that model before starting another')
        e=ROOT/'evidence'/('service-'+stamp);e.mkdir(exist_ok=True)
        log=ROOT/'logs'/('service-'+stamp+'.log')
        env=dict(os.environ,LAB_SERVER_EVIDENCE=str(e))
        with log.open('ab') as f:
            proc=subprocess.Popen([PY,str(Path(__file__).resolve()),'serve'],stdin=subprocess.DEVNULL,stdout=f,stderr=subprocess.STDOUT,env=env,start_new_session=True)
        write(ROOT/'evidence/service-process.json',dict(pid=proc.pid,log=str(log),instance=CFG['instance'],model=CFG['name'],vllm_version=CFG['legacy_vllm_version'],tp=CFG['tp'],started_utc=datetime.now(timezone.utc).isoformat()))
        (ROOT/'evidence/latest-service.txt').write_text(str(e)+'\n',encoding='utf-8')
        print('SERVICE_STARTED',proc.pid,log)

"""Foreground/background per-model server; uses existing environment only."""
import argparse,fcntl,json,os,shlex,subprocess,sys
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
CFG=json.loads((ROOT/'config/model.json').read_text())
PY=CFG['gpu_python']
stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
def write(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def serve():
    validate_gpu()
    activate_compat()
    lock=open(Path(CFG['batch_root'])/'evidence/gpu-instance.lock','a+')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    os.set_inheritable(lock.fileno(),True)
    e=Path(os.environ['LAB_SERVER_EVIDENCE']);e.mkdir(exist_ok=True)
    env=dict(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',
        TOKENIZERS_PARALLELISM='false',PYTHONUNBUFFERED='1',VLLM_WORKER_MULTIPROC_METHOD='spawn',
        CUDA_VISIBLE_DEVICES=CFG['gpus'],HF_HOME=str(ROOT/'cache/huggingface'),
        XDG_CACHE_HOME=str(ROOT/'cache/xdg'),VLLM_CACHE_ROOT=str(ROOT/'cache/vllm'),
        TORCHINDUCTOR_CACHE_DIR=str(ROOT/'cache/torchinductor'),TRITON_CACHE_DIR=CFG.get('local_kernel_cache_dir','/tmp/deepseek-v4-triton-'+str(os.getpid())))
    if CFG.get('cuda_toolkit_root'):
        toolkit=CFG['cuda_toolkit_root']
        env.update(CUDA_HOME=toolkit,CUDA_PATH=toolkit,
            PATH=toolkit+'/bin:'+str(Path(PY).parent)+':'+os.environ.get('PATH',''),
            NVCC=toolkit+'/bin/nvcc',DG_JIT_NVCC_COMPILER=toolkit+'/bin/nvcc')
    if CFG.get('cuda_link_library_dir'):
        env['LIBRARY_PATH']=CFG['cuda_link_library_dir']+(':'+os.environ['LIBRARY_PATH'] if os.environ.get('LIBRARY_PATH') else '')
    if CFG.get('cuda_supplemental_include_dir'):
        env['CPATH']=CFG['cuda_supplemental_include_dir']
    if CFG.get('cuda_vendor_library_dir'):
        env['LD_LIBRARY_PATH']=os.environ.get('LD_LIBRARY_PATH','')+':'+CFG['cuda_vendor_library_dir']
    env['MAX_JOBS']='4'
    env['VLLM_FLASHINFER_ALLREDUCE_BACKEND']=CFG.get('flashinfer_allreduce_backend','auto')
    env['VLLM_COMPUTE_NANS_IN_LOGITS']='1' if CFG.get('compute_nans_in_logits') else '0'
    if CFG.get('allreduce_backend')=='pynccl':
        env['VLLM_ALLREDUCE_USE_FLASHINFER']='0'
        env['VLLM_ALLREDUCE_USE_SYMM_MEM']='0'
    env['OMP_NUM_THREADS']=str(CFG.get('omp_num_threads',1))
    os.environ.update(env)
    (e/'nvidia-smi.txt').write_text(subprocess.check_output(['nvidia-smi'],text=True))
    (e/'environment.lock').write_text(subprocess.check_output([PY,'-m','pip','freeze'],text=True))
    args=['serve',CFG['model_path'],'--host','127.0.0.1','--port','8000','--api-server-count','1',
        '--served-model-name',CFG['served_name'],'--dtype','bfloat16','--tensor-parallel-size',str(CFG['tp']),
        '--max-model-len','32768','--block-size',str(CFG['block_size']),'--gpu-memory-utilization',str(CFG['gpu_memory_utilization']),
        '--kv-cache-dtype',CFG['kv_cache_dtype'],'--max-num-seqs','1','--max-num-batched-tokens','2048',
        '--enable-prefix-caching','--enable-prompt-tokens-details',
        '--enable-auto-tool-choice','--tool-call-parser',CFG['tool_parser'],'--reasoning-parser','deepseek_v4','--tokenizer-mode','deepseek_v4']
    if CFG.get('enforce_eager',True):args+=['--enforce-eager']
    if CFG.get('compilation_config'):args+=['--compilation-config',json.dumps(CFG['compilation_config'])]
    if CFG.get('allreduce_backend')=='pynccl':args+=['--disable-custom-all-reduce']
    if CFG.get('moe_backend'):args+=['--moe-backend',CFG['moe_backend']]
    if CFG['quantization']:args+=['--quantization',CFG['quantization']]
    if CFG.get('hf_overrides'):args+=['--hf-overrides',json.dumps(CFG['hf_overrides'])]
    if CFG['id']=='glm4-9b':args+=['--tool-parser-plugin',str(ROOT/'code/service/glm4_native_parser.py')]
    command=[PY,str(ROOT/'code/service/server.py'),*args]
    (e/'command.txt').write_text(shlex.join(command)+'\n',encoding='utf-8')
    write(e/'config.json',CFG)
    os.execv(PY,command)


def validate_gpu():
    path=Path(CFG['resource_environment_root'])/'records/h100-compute-validation.json'
    if not path.exists():
        raise RuntimeError('GPU_COMPUTE_VALIDATION_REQUIRED: run validate_deepseek_gpu.py first')
    result=json.loads(path.read_text())
    if result.get('status')!='gpu-compute-and-architecture-ready':
        raise RuntimeError('GPU_COMPUTE_VALIDATION_FAILED; refuse model loading')
    import socket
    if socket.gethostname()!='gpu-'+CFG['instance']:
        raise RuntimeError('Instance mismatch; revalidate and configure the new instance')

def activate_compat():
    path=Path(CFG['resource_environment_root'])/'records/cuda-compat-13-0.json'
    if path.exists():
        library=json.loads(path.read_text())['library_path']
        if not (Path(library)/'libcuda.so.1').exists():
            raise RuntimeError('Recorded CUDA compatibility library is missing')
        os.environ['LD_LIBRARY_PATH']=library+(':'+os.environ['LD_LIBRARY_PATH'] if os.environ.get('LD_LIBRARY_PATH') else '')

if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('action',choices=('launch','serve'));mode=a.parse_args().action
    if mode=='serve':serve()
    else:
        validate_gpu()
        activate_compat()
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

"""Launch only B-owned services. All caches and runtime fixes stay inside B."""
from pathlib import Path
import subprocess,os,json,time,signal,urllib.request
R=Path('/path/to/workspace/多智能体系统实验数据/fcb-dow-research/B_cross_engine')
def save(p,x):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(x,ensure_ascii=False,indent=2))
def health():
 try:
  with urllib.request.urlopen('http://127.0.0.1:30000/health',timeout=3) as f:return f.status==200
 except Exception:return False
def model_info():
 with urllib.request.urlopen('http://127.0.0.1:30000/model_info',timeout=5) as f:return json.load(f)
def stop():
 records=[R/'evidence/current-server.json',R/'evidence/qwen-triton-launch.json']
 for record in records:
  if not record.exists():continue
  pid=json.loads(record.read_text())['pid']
  proc=Path(f'/proc/{pid}')
  if not proc.exists():continue
  cmd=(proc/'cmdline').read_bytes()
  assert str(R/'envB/bin/python').encode() in cmd and b'sglang.launch_server' in cmd and os.getpgid(pid)==pid
  os.killpg(pid,signal.SIGTERM);time.sleep(3)
  try:os.killpg(pid,signal.SIGKILL)
  except ProcessLookupError:pass
 time.sleep(2)
def ensure(model,page=1):
 target=R.parent.parent.parent/'LLM-Model'/('Qwen3-8B' if model=='qwen' else 'Llama-3.3-70B-Instruct-FP8')
 if health():
  info=model_info()
  current=R/'evidence/current-server.json'
  known=json.loads(current.read_text()) if current.exists() else {'page_size':1,'model':'qwen'}
  if info['model_path']==str(target) and known['page_size']==page:return
 stop()
 stamp=time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())
 log=R/f'logs/{model}-page{page}-{stamp}.log'
 args=[str(R/'envB/bin/python'),'-m','sglang.launch_server','--model-path',str(target),'--tp','1','--port','30000','--host','127.0.0.1','--page-size',str(page),'--chunked-prefill-size','2048','--attention-backend','triton','--sampling-backend','pytorch','--max-running-requests','1','--context-length','8192','--mem-fraction-static','0.90' if model=='qwen' else '0.99','--dtype','bfloat16','--kv-cache-dtype','bfloat16','--random-seed','42','--disable-cuda-graph','--disable-piecewise-cuda-graph']
 if model=='llama':args+=['--quantization','modelopt_fp8']
 env=os.environ.copy();env.update({'HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1','HF_HOME':str(R/'cache/huggingface'),'XDG_CACHE_HOME':str(R/'cache'),'TRITON_CACHE_DIR':str(R/'cache/triton'),'FLASHINFER_WORKSPACE_BASE':str(R/'cache/flashinfer'),'TORCH_EXTENSIONS_DIR':str(R/'cache/torch-extensions'),'PATH':str(R/'envB/bin')+':'+env.get('PATH',''),'LD_LIBRARY_PATH':str(R/'runtime/lib')+':'+env.get('LD_LIBRARY_PATH','')})
 with log.open('w') as f:p=subprocess.Popen(args,stdout=f,stderr=subprocess.STDOUT,env=env,start_new_session=True)
 record={'pid':p.pid,'model':model,'page_size':page,'args':args,'backend':'triton','log':str(log),'quantization':'modelopt_fp8' if model=='llama' else None,'activation_dtype':'bfloat16','kv_cache_dtype':'bfloat16','mem_fraction_static':.90 if model=='qwen' else .99,'reason_for_llama_memory_fraction':'.99 matches archived single-H100 Llama budget; leave enough BF16 KV for sequential E5 replay'}
 save(R/'evidence/current-server.json',record);save(R/f'evidence/{model}-page{page}-{stamp}-launch.json',record)
 for _ in range(200):
  if p.poll() is not None:raise RuntimeError(f'{model} startup exited {p.returncode}; see {log}')
  if health():
   assert model_info()['model_path']==str(target)
   record['ready_unix']=time.time();save(R/'evidence/current-server.json',record);print('ENGINE READY',model,page,flush=True);return
  time.sleep(3)
 raise RuntimeError(f'{model} startup timeout; see {log}')

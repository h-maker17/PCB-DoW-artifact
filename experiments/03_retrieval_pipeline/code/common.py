from pathlib import Path
import json,hashlib,os,urllib.request,time
R=Path(__file__).resolve().parents[1]
MODEL_DIR=Path(os.environ.get('PCB_MODEL_DIR','models'))
B=R.parent
MODEL='llama-3.3-70b'
_NATIVE_TOKENIZER=None
def native_decode(ids):
 global _NATIVE_TOKENIZER
 if _NATIVE_TOKENIZER is None:
  from transformers import AutoTokenizer
  _NATIVE_TOKENIZER=AutoTokenizer.from_pretrained(MODEL_DIR/'Llama-3.3-70B-Instruct-FP8',local_files_only=True)
 return _NATIVE_TOKENIZER.decode(ids,skip_special_tokens=False,clean_up_tokenization_spaces=False)
def read(p):return json.loads(Path(p).read_text(encoding='utf-8'))
def save(p,d):
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def digest(x):
 if not isinstance(x,(bytes,str)):x=json.dumps(x,ensure_ascii=False,separators=(',',':'))
 return hashlib.sha256(x.encode('utf-8') if isinstance(x,str) else x).hexdigest()
def api(endpoint,payload):
 req=urllib.request.Request('http://127.0.0.1:8000'+endpoint,data=json.dumps(payload,ensure_ascii=False).encode(),headers={'Content-Type':'application/json'})
 with urllib.request.urlopen(req,timeout=600) as r:return json.load(r)
def lcp(a,b):
 for i,(x,y) in enumerate(zip(a,b)):
  if x!=y:return i
 return min(len(a),len(b))
def now_ns():return time.time_ns()

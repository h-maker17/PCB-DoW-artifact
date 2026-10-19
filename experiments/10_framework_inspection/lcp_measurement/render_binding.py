"""Pinned offline rendering. No models, pipeline, backend, retrieval or network."""
from pathlib import Path
import os,json,hashlib,importlib.metadata,copy
ROOT=Path(__file__).resolve().parent
os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HOME=str(ROOT/'cache/hf'),TOKENIZERS_PARALLELISM='false',PYTHONDONTWRITEBYTECODE='1')
TEMPLATES=('llama33','qwen3')
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def object_hash(x):return hashlib.sha256(json.dumps(x,ensure_ascii=True,separators=(',',':')).encode('utf8')).hexdigest()
def dump(p,x):
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(x,ensure_ascii=True,indent=2)+'\n',encoding='utf8')
def verify_runtime():
 m=json.loads((ROOT/'config/runtime_manifest.json').read_text(encoding='utf8'))
 for rel,h in m['files'].items():
  p=(ROOT/rel).resolve()
  if not p.is_relative_to(ROOT) or sha(p)!=h:raise RuntimeError('runtime_integrity:'+rel)
 for pkg,version in m['packages'].items():
  if importlib.metadata.version(pkg)!=version:raise RuntimeError('runtime_version:'+pkg)
 return sha(ROOT/'config/runtime_manifest.json')
class Renderer:
 def __init__(self):
  verify_runtime()
  from transformers import AutoTokenizer
  manifest=json.loads((ROOT/'config/templates_manifest.json').read_text(encoding='utf8'))
  self.tokenizers={}
  for t in TEMPLATES:
   for f,h in manifest[t]['sha256'].items():
    if sha(ROOT/'templates'/t/f)!=h:raise RuntimeError('template_SHA:'+t+'/'+f)
   self.tokenizers[t]=AutoTokenizer.from_pretrained(str(ROOT/'templates'/t),local_files_only=True)
  self.cache={}
 def render(self,body,t):
  key=(t,object_hash(body))
  if key not in self.cache:
   tok=self.tokenizers[t]
   text=tok.apply_chat_template(body['messages'],tools=body.get('tools'),tokenize=False,add_generation_prompt=True)
   enc=tok(text,add_special_tokens=False,return_offsets_mapping=True)
   self.cache[key]={'messages':copy.deepcopy(body['messages']),'rendered':text,'token_ids':enc['input_ids'],'offsets':[list(x) for x in enc['offset_mapping']]}
  return copy.deepcopy(self.cache[key])
 def binding_error(self,item,request,t):
  if item['messages']!=request['body']['messages']:return 'messages_last_request_mismatch'
  try:
   if json.loads(request['raw_body_text'])!=request['body']:return 'raw_request_body_payload_mismatch'
  except (json.JSONDecodeError,TypeError):return 'raw_request_body_invalid_JSON'
  expected=self.render(request['body'],t)
  for f in ('rendered','token_ids','offsets'):
   if item[f]!=expected[f]:return 'complete_render_binding_mismatch:'+f
  return None

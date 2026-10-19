import os,json,hashlib,datetime,urllib.request,csv
from pathlib import Path
ROOT=Path(os.environ.get('E5_ROOT',Path(__file__).resolve().parents[2]))
TROOT=ROOT.parent/'2026-10-05-task-generalization'
MODEL='llama-3.3-70b'
def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def digest(s):return hashlib.sha256(s.encode()).hexdigest()
def read(p):return json.loads(Path(p).read_text())
def save(p,x):
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);t=p.with_suffix(p.suffix+'.tmp');t.write_text(json.dumps(x,ensure_ascii=False,indent=2));t.replace(p)
def api(endpoint,body):
 q=urllib.request.Request('http://127.0.0.1:8000'+endpoint,data=json.dumps(body,ensure_ascii=False).encode(),headers={'Content-Type':'application/json'})
 with urllib.request.urlopen(q,timeout=600) as r:return json.loads(r.read())
def tokenize(**kwargs):return api('/tokenize',dict(model=MODEL,**kwargs))
def lcp(a,b):
 for i,(x,y) in enumerate(zip(a,b)):
  if x!=y:return i
 return min(len(a),len(b))
def csvwrite(path,rows):
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
 with path.open('w',newline='',encoding='utf-8') as f:
  w=csv.DictWriter(f,fieldnames=list(dict.fromkeys(k for r in rows for k in r)));w.writeheader();w.writerows(rows)


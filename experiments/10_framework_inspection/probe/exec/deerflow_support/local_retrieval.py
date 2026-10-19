"""Real local FAISS/CPU embedding candidate with explicit, parent-approved controlled hybrid mapping.
Not a RAGFlow deployment or reproduction of RAGFlow's tokenizer/search engine.
Only consumed protocol fields are implemented; unsupported fields fail closed.
"""
from isolation import prepare,ROOT
prepare()
import contextlib,hashlib,json,pathlib,re,secrets,threading,urllib.parse
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
def digest(x):return hashlib.sha256(x).hexdigest()
class LocalIndex:
 def __init__(self,corpus_path=None,*,artifacts=None):
  from run_artifacts import RunArtifacts
  self.artifacts=artifacts or RunArtifacts.preparation("index")
  import torch,faiss,numpy as np
  from sentence_transformers import SentenceTransformer
  torch.set_num_threads(1)
  if torch.get_num_interop_threads()!=1:torch.set_num_interop_threads(1)
  self.lock=threading.RLock();self.np=np;self.faiss=faiss
  self.model=SentenceTransformer(str(ROOT/'embedding'),device='cpu',local_files_only=True)
  p=pathlib.Path(corpus_path or ROOT/'corpus.json')
  corpus=json.loads(p.read_bytes())
  self.embedding_id='sentence-transformers/all-MiniLM-L6-v2@'+json.loads((ROOT/'embedding/model_api.json').read_bytes())['sha']+'@SCAN-cpu384'
  self.chunks=[]
  for i,text in enumerate(corpus['documents']):
   dataset=digest(('scan-deerflow-'+('audit' if i==0 else 'other')).encode())[:32]
   document=digest(text.encode())[:32]
   self.chunks.append({'id':digest((document+':0').encode())[:32],'dataset_id':dataset,'document_id':document,'document_keyword':f'scan-declared-{i}.txt','content':text})
  self.vectors=self.model.encode([x['content'] for x in self.chunks],normalize_embeddings=True,convert_to_numpy=True).astype(np.float32)
  self.index=faiss.IndexFlatIP(self.vectors.shape[1]);self.index.add(self.vectors)
  self.catalog=[{'id':d,'name':'SCAN audit' if d==self.chunks[0]['dataset_id'] else 'SCAN other','embedding_model':self.embedding_id,'chunk_count':sum(x['dataset_id']==d for x in self.chunks)} for d in sorted({x['dataset_id'] for x in self.chunks})]
  self.documents=[{'id':x['document_id'],'dataset_id':x['dataset_id'],'name':x['document_keyword'],'chunk_count':1,'status':'1','run':'DONE'} for x in self.chunks]
  with self.artifacts.path('index/chunks.faiss').open('xb') as f:f.write(faiss.serialize_index(self.index).tobytes())
  manifest={'provider':'controlled local RAGFlow-compatible candidate; not original backend','embedding':'CPU MiniLM384; original archived model bytes','dimensions':self.vectors.shape[1],'normalized':True,'index':'FAISS IndexFlatIP','corpus_sha256':digest(p.read_bytes()),'catalog':self.catalog,'documents':self.documents,'chunks':self.chunks,'vector_sha256':digest(self.vectors.tobytes()),'hybrid_mapping':'candidate-query-term-coverage-v1: similarity = weight*cosine + (1-weight)*fraction of distinct query tokens present in document. ASCII words casefolded. Parent-approved controlled provider mapping; NOT equivalent to RAGFlow tokenizer/weighted scorer; retrieval accuracy is not measured.'}
  self.manifest=manifest
  self.artifacts.write_new('index/manifest.json',manifest)
 def retrieve(self,b):
  allowed={'question','dataset_ids','document_ids','page','page_size','similarity_threshold','vector_similarity_weight','top_k'}
  if set(b)-allowed:raise ValueError('Unsupported retrieval field: '+','.join(sorted(set(b)-allowed)))
  q=b.get('question');datasets=b.get('dataset_ids');docs=b.get('document_ids')
  if not isinstance(q,str) or not q.strip():raise ValueError('question required')
  if not isinstance(datasets,list) or not datasets or any(x not in {d['id'] for d in self.catalog} for x in datasets):raise ValueError('Inaccessible dataset scope')
  if docs is not None and (not isinstance(docs,list) or not docs or any(x not in {d['id'] for d in self.documents if d['dataset_id'] in datasets} for x in docs)):raise ValueError('Inaccessible document scope')
  page=b.get('page',1);size=b.get('page_size',8);k=b.get('top_k',256);threshold=b.get('similarity_threshold',.2);weight=b.get('vector_similarity_weight',.3)
  for name,x,hi in [('page',page,10000),('page_size',size,100),('top_k',k,1024)]:
   if isinstance(x,bool) or not isinstance(x,int) or not 1<=x<=hi:raise ValueError(name+' invalid')
  for name,x in [('similarity_threshold',threshold),('vector_similarity_weight',weight)]:
   if isinstance(x,bool) or not isinstance(x,(int,float)) or not 0<=x<=1:raise ValueError(name+' invalid')
  eligible=[i for i,x in enumerate(self.chunks) if x['dataset_id'] in datasets and (docs is None or x['document_id'] in docs)]
  if not eligible:return {'chunks':[],'doc_aggs':[],'total':0}
  with self.lock:
   query=self.model.encode([q],normalize_embeddings=True,convert_to_numpy=True).astype(self.np.float32)
   # Scope is applied before top_k, so unrelated documents cannot occupy candidate slots.
   idx=self.faiss.IndexFlatIP(self.vectors.shape[1]);idx.add(self.vectors[eligible])
   scores,ids=idx.search(query,min(k,len(eligible)))
  terms=set(re.findall(r'[a-z0-9]+',q.casefold()))
  out=[]
  for vec,n in zip(scores[0],ids[0]):
   chunk=self.chunks[eligible[int(n)]]
   doc_terms=set(re.findall(r'[a-z0-9]+',chunk['content'].casefold()))
   term=len(terms&doc_terms)/len(terms) if terms else 0.
   similarity=weight*float(vec)+(1-weight)*term
   out.append({**chunk,'similarity':similarity,'vector_similarity':float(vec),'term_similarity':term})
  out.sort(key=lambda x:(-x['similarity'],x['id']))
  out=[x for x in out if x['similarity']>=threshold]
  total=len(out);selected=out[(page-1)*size:page*size]
  aggs=[{'doc_id':x['document_id'],'doc_name':x['document_keyword'],'count':1} for x in selected]
  return {'chunks':selected,'doc_aggs':aggs,'total':total}
class LocalService:
 def __init__(self,index,*,replica=None,retrieval_observer=None):
  self.index=index;self.token=secrets.token_urlsafe(24);self.events=[];self.lock=threading.Lock();self.replica=replica;self.retrieval_observer=retrieval_observer
  self.artifacts=index.artifacts;self.log_path=self.artifacts.new_log('retrieval-http')
 def record(self,record):
  record={**record,'run_id':self.artifacts.run_id,'phase':self.artifacts.phase}
  with self.lock:
   self.events.append(record)
   self.artifacts.append(self.log_path,record)
 def route(self,method,path,params,body):
  if method=='POST' and path=='/api/v1/retrieval':return self.index.retrieve(body)
  if method!='GET':raise ValueError('Unsupported method')
  if set(params)-{'ids','page','page_size'}:raise ValueError('Unsupported catalog parameter')
  page=int(params.get('page',['1'])[0]);size=int(params.get('page_size',['100'])[0])
  if page<1 or not 1<=size<=100:raise ValueError('Invalid pagination')
  if path=='/api/v1/datasets':items=self.index.catalog
  elif re.fullmatch(r'/api/v1/datasets/[^/]+/documents',path):
   dataset=path.split('/')[4]
   if dataset not in {x['id'] for x in self.index.catalog}:raise ValueError('Inaccessible dataset')
   items=[x for x in self.index.documents if x['dataset_id']==dataset]
  else:raise ValueError('Unsupported endpoint')
  if 'ids' in params:items=[x for x in items if x['id'] in params['ids']]
  total=len(items);items=items[(page-1)*size:page*size]
  return {'docs':items,'total':total} if path.endswith('/documents') else (items,total)
 @contextlib.contextmanager
 def serving(self):
  owner=self
  class Handler(BaseHTTPRequestHandler):
   def log_message(self,*args):pass
   def do_GET(self):self.handle_request()
   def do_POST(self):self.handle_request()
   def handle_request(self):
    parts=urllib.parse.urlsplit(self.path);params=urllib.parse.parse_qs(parts.query)
    raw=self.rfile.read(int(self.headers.get('Content-Length',0))).decode('utf8');body=json.loads(raw or '{}')
    try:
     if self.headers.get('Authorization')!='Bearer '+owner.token:raise PermissionError('Unauthorized local scope')
     if self.command=='POST' and parts.path=='/api/v1/retrieval' and owner.retrieval_observer is not None:
      data=owner.retrieval_observer(owner.replica if owner.replica is not None else self.headers.get('X-SCAN-Replica'),body,lambda:owner.route(self.command,parts.path,params,body))
     else:data=owner.route(self.command,parts.path,params,body)
     payload={'code':0,'data':data}
     if isinstance(data,tuple):payload={'code':0,'data':data[0],'total':data[1]}
     status=200
    except (ValueError,PermissionError,KeyError,TypeError) as exc:payload={'code':403 if isinstance(exc,PermissionError) else 102,'message':str(exc)};status=403 if isinstance(exc,PermissionError) else 400
    owner.record({'replica':owner.replica if owner.replica is not None else self.headers.get('X-SCAN-Replica'),'raw_body_text':raw,'kind':'retrieval_http','method':self.command,'path':parts.path,'params':params,'body':body,'status':status,'response':payload})
    b=json.dumps(payload,ensure_ascii=False).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
  server=ThreadingHTTPServer(('127.0.0.1',0),Handler);t=threading.Thread(target=server.serve_forever,daemon=True);t.start()
  try:yield f'http://127.0.0.1:{server.server_port}'
  finally:server.shutdown();server.server_close();t.join()




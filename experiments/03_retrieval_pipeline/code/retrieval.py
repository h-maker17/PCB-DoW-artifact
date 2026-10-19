import faiss,numpy as np,uuid
from embedding import CPUEmbedder
from common import *
_EMBED=None
def shared_embedder():
 global _EMBED
 if _EMBED is None:_EMBED=CPUEmbedder()
 return _EMBED
class RetrievalService:
 """Controlled retrieval return boundary. The prompt assembler never creates markers."""
 def __init__(self,mode,query,embedder=None):
  self.mode=mode;self.query=query;self.embed=embedder or shared_embedder();self.calls=[];faiss.omp_set_num_threads(1)
  D=R/'datasets/smoke-index';self.docs=read(D/'documents.json');name='static' if mode in ['static-neutral','static-deploy-variance'] else 'clean'
  self.index=faiss.read_index(str(D/f'{name}.faiss'));self.top_k=read(R/'config/preregistration.json')['index']['top_k'];self.service_id=uuid.uuid4().hex
 def retrieve(self)->str:
  """Retrieve grounding passages for the fixed legitimate user question."""
  start=now_ns();v=self.embed.encode([self.query]);scores,ids=self.index.search(v,len(self.docs))
  candidates=sorted(zip(scores[0],ids[0]),key=lambda x:(-float(x[0]),self.docs[int(x[1])]['chunk_id']))[:self.top_k]
  selected=[self.docs[int(i)] for _,i in candidates];poison=self.mode in ['static-neutral','static-deploy-variance']
  passages='\n\n'.join((d['poisoned_text'] if poison else d['text'])+'\n[Passage ID: '+d['chunk_id']+']' for d in selected)
  call=len(self.calls)+1;marker=''
  if self.mode=='det-tool-neutral':marker='\u200b'
  elif self.mode=='pretrieval-zerowidth':marker='\u200b'*call
  elif self.mode=='pretrieval-metadata':marker=f'request-id: retrieval-{call:08d}\n'
  returned=marker+passages
  self.calls.append({'invocation':call,'query':self.query,'query_sha256':digest(self.query),'embedding_sha256':digest(v.tobytes()),'top_k_ids':[d['chunk_id'] for d in selected],'scores':[float(s) for s,_ in candidates],'started_ns':start,'finished_ns':now_ns(),'raw_retrieved_text':passages,'returned_text':returned,'returned_sha256':digest(returned),'marker_origin':'stored corpus' if poison else ('retrieval function return' if marker else 'none'),'return_marker':marker})
  return returned

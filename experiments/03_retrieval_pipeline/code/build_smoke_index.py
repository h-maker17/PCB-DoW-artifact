import re,faiss,numpy as np
from embedding import CPUEmbedder
from common import *
def main():
 cfg=read(R/'config/preregistration.json');sources=[];docs=[];queries=[]
 for task in ['hotpotqa','triviaqa','gov_report']:
  p=B/f'experiments/2026-10-05-task-generalization/datasets/processed/{task}.json';records=read(p);d=records[0];sources.append({'path':str(p),'sha256':digest(p.read_bytes()),'selected_id':d['id'],'context_sha256':digest(d['context'])})
  if task!='gov_report':queries.append({k:d[k] for k in ['task','id','question','answers','context_sha256']})
  # Exact contiguous character spans, rather than reconstructed/synthetic passages.
  words=list(re.finditer(r'\S+',d['context']));step=cfg['index']['chunk_words']-cfg['index']['overlap_words']
  for start in range(0,len(words),step):
   end=min(start+cfg['index']['chunk_words'],len(words));a=words[start].start();b=words[end-1].end();text=d['context'][a:b]
   docs.append({'chunk_id':f'{task}:{d["id"]}:{start:06d}','task':task,'source_doc_id':d['id'],'source_context_sha256':digest(d['context']),'start_char':a,'end_char':b,'text':text,'poisoned_text':'\u200b'+text})
 faiss.omp_set_num_threads(1);embed=CPUEmbedder();clean=embed.encode([d['text'] for d in docs]);static=embed.encode([d['poisoned_text'] for d in docs]);assert np.array_equal(clean,static),'Static marker changed embeddings; investigate before neutral control'
 D=R/'datasets/smoke-index';D.mkdir(parents=True,exist_ok=True)
 for name,vectors in [('clean',clean),('static',static)]:
  idx=faiss.IndexFlatIP(vectors.shape[1]);idx.add(vectors);faiss.write_index(idx,str(D/f'{name}.faiss'));np.save(D/f'{name}-embeddings.npy',vectors)
 save(D/'documents.json',docs);save(D/'queries.json',queries);save(D/'provenance.json',sources)
 old=B/'experiments/2026-10-05-e5-append-G/datasets/blocks.json';s=read(old);save(R/'datasets/shared-history.json',{'text':s['S'],'source_path':str(old),'source_sha256':digest(old.read_bytes()),'S_tokens':s['S_tokens'],'synthetic_history':True})
 save(R/'evidence/index-build.json',{'passages':len(docs),'source_documents':3,'queries':len(queries),'embedding_dim':clean.shape[1],'clean_static_embeddings_bit_identical':True,'faiss':faiss.__version__,'embedding_inference':'CPU, deterministic=True, mean pooling + L2','sources':sources,'references_excluded_from_corpus':True,'index_sha256':{n:digest((D/f'{n}.faiss').read_bytes()) for n in ['clean','static']}})
 print('INDEX BUILT',len(docs),flush=True)
if __name__=='__main__':main()

from common import *
T=B/'experiments/2026-10-05-task-generalization'
def archived(folder,meta):
 prompts=[];cached=[];rows=[];idslist=[]
 for p in sorted(folder.glob('*worker*.response.json')):
  d=read(p);q=p.with_name(p.name.replace('.response.json','.request.json'));ids=d['prompt_token_ids'];u=d['usage'];prompts.append(u['prompt_tokens']);cached.append(u['prompt_tokens_details']['cached_tokens']);idslist.append(ids)
  assert len(ids)==prompts[-1]
  rows.append({'replica':len(prompts),'prompt':prompts[-1],'cached':cached[-1],'response_path':str(p),'source_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'request_path':str(q),'input_sha256':digest(ids),'completion_tokens':u['completion_tokens']})
 assert len(prompts)==5 and cached[0]==0
 return {**meta,'N':5,'prompt':prompts,'cached':cached,'workers':rows,'features':features(prompts,cached),'cost_f01':sum(p-.9*c for p,c in zip(prompts,cached)),'warm_hit':sum(cached[1:])/sum(prompts[1:]),'input_fingerprint':digest(idslist),'source':'archived-vLLM','source_directory':str(folder)}
def prepare_groups():
 docs=[];seen={}
 for task in ['hotpotqa','triviaqa','gov_report']:
  records=read(T/'datasets/processed'/f'{task}.json');assert len(records)==30
  records=sorted(records,key=lambda x:hashlib.sha256((task+digest(x['context'])+x['id']).encode()).hexdigest())
  for i,doc in enumerate(records):
   group=digest(doc['context']);split=['train','calibration','test'][i//10]
   if group in seen:split=seen[group]
   seen[group]=split;docs.append({'task':task,'id':doc['id'],'context':doc['context'],'group':group,'split':split,'source_file':str(T/'datasets/processed'/f'{task}.json'),'source_file_sha256':hashlib.sha256((T/'datasets/processed'/f'{task}.json').read_bytes()).hexdigest()})
 assert all(len({x['split'] for x in docs if x['group']==g})==1 for g in seen)
 save(R/'datasets/H2-documents.json',docs);return docs
def main():
 docs=prepare_groups();dataset=[]
 for j,doc in enumerate(docs):
  for arm in ['none','front']:
   folder=T/'data/formal'/doc['task']/doc['id']/arm
   row=archived(folder,{'key':f"old-{doc['task']}-{doc['id']}-{arm}",'phase':'H2-dataset','model':'llama','task':doc['task'],'group':doc['group'],'split':doc['split'],'label':int(arm=='front'),'category':'archived-'+arm,'byte_divergent':arm=='front','tool_byte_observation':'controlled original context+marker reconstruction; not an undeclared byte-log measurement'})
   dataset.append(row)
  context_ids=tokenize(doc['context'])
  for category,length in [('short-low-reuse',32),('legal-tail-diversity',2048)]:
   ctx=detokenize(context_ids[:length]);requests=[];returns=[]
   for i in range(1,6):
    tool=ctx+(f'\n[legitimate-request-id:{i:04d}]' if category=='legal-tail-diversity' else '')
    persona=['planner','verifier','archivist','scheduler','measurement specialist'][i-1]
    tail=f'\nReplica {i}; provide the {persona} perspective. '+('Use the supplied reference and independently verify facts. '*[1,2,4,8,16][i-1])
    requests.append(request('<CONTEXT>\n'+tool+'\n</CONTEXT>'+tail));returns.append(tool)
   key=f"H2-negative-{doc['task']}-{doc['id']}-{category}"
   row=run_round(key,requests,{'phase':'H2-dataset','model':'llama','task':doc['task'],'group':doc['group'],'split':doc['split'],'label':0,'category':category,'source_document_sha256':doc['group'],'source_context_tokens':len(context_ids),'retained_context_tokens':min(length,len(context_ids)),'tool_returns_sha256':[digest(x) for x in returns],'byte_divergent':len(set(returns))>1,'legitimacy':'application-authorized identifier/persona variance, never malicious markers'})
   dataset.append(row)
  save(R/'evidence/H2-dataset-progress.json',{'documents_done':j+1,'rows':len(dataset),'next_step':'freeze joint detector, then evasion'})
 # Engine/model anchor references are test-only; keep all replicas of one context together.
 plan=read(B/'B_cross_engine/datasets/replay-plan.json');unique=set();anchors=[]
 for src in plan['runs']:
  if src['phase']!='B1' or src['position'] not in ['none','front']:continue
  folder=src['source_run']
  if folder in unique:continue
  unique.add(folder);p=[w['vllm_prompt_tokens'] for w in src['workers']];c=[w['vllm_cached_tokens'] for w in src['workers']]
  anchors.append({'key':src['key'],'model':src['model'],'task':'synthetic-anchor','group':'shared-synthetic-anchor-'+src['model'],'split':'test-only-anchor','label':int(src['position']=='front'),'category':src['position'],'N':src['N'],'prompt':p,'cached':c,'features':features(p,c),'source':'archived-vLLM','source_directory':folder,'reference_n':src['reference_n']})
 save(R/'data/ancillary/H2-anchors.json',anchors);save(R/'datasets/H2-dataset.json',dataset)
 save(R/'evidence/H2-dataset-state.json',{'status':'complete-awaiting-detector-freeze','documents':len(docs),'rows':len(dataset),'new_runs':len(docs)*2,'new_requests':len(docs)*10,'archived_rounds':len(docs)*2,'split_counts':{s:sum(x['split']==s for x in docs) for s in ['train','calibration','test']}})
 print('H2 DATASET COMPLETE',len(dataset),flush=True)
if __name__=='__main__':main()

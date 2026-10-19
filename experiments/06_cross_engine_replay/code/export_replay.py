"""Export archived vLLM token IDs without invoking any prompt assembler."""
from pathlib import Path
import json,hashlib,collections
R=Path('/path/to/workspace/多智能体系统实验数据/fcb-dow-research/B_cross_engine')
SRC=R.parent;OUT=R/'datasets';OUT.mkdir(exist_ok=True)
def read(p):return json.loads(p.read_text(encoding='utf-8'))
def digest(x):return hashlib.sha256(json.dumps(x,separators=(',',':')).encode()).hexdigest()
def lcp(a,b):return next((i for i,(x,y) in enumerate(zip(a,b)) if x!=y),min(len(a),len(b)))
runs=[]
def add(folder,phase,model,repeat,position=None,ref_n=5):
 m=read(folder/'manifest.json');n=m.get('N',m.get('n_workers',m.get('workers')))
 mode=m.get('mode') if phase=='B3' else position
 key=f'{phase}-{model}-n{n}-r{repeat}-{position}' if phase!='B3' else f'B3-llama-{folder.name}'
 run={'key':key,'phase':phase,'model':model,'N':n,'repeat':repeat,'position':position,'reference_n':ref_n,'source_run':str(folder),'source_manifest':m,'workers':[]}
 if phase=='B3':run.update({k:m[k] for k in ['g_tokens','S_tokens','layout','mode']})
 for f in sorted(folder.glob('*worker*.response.json')):
  d=read(f);ids=d['prompt_token_ids'];usage=d['usage'];req=read(f.with_name(f.name.replace('.response.json','.request.json')))
  assert isinstance(ids,list) and len(ids)==usage['prompt_tokens'] and all(isinstance(t,int) for t in ids)
  cached=usage['prompt_tokens_details']['cached_tokens'];assert 0<=cached<=len(ids)
  prediction=max([lcp(ids,w['input_ids']) for w in run['workers']] or [0])
  run['workers'].append({'source_response':str(f),'source_sha256':hashlib.sha256(f.read_bytes()).hexdigest(),'input_ids':ids,'input_sha256':digest(ids),'vllm_prompt_tokens':len(ids),'vllm_cached_tokens':cached,'vllm_completion_tokens':usage['completion_tokens'],'prompt_lcp_prediction':prediction,'sampling':{k:req[k] for k in ['temperature','seed','top_p','top_k','max_tokens'] if k in req}})
 assert len(run['workers'])==n and run['workers'][0]['vllm_cached_tokens']==0
 if phase=='B3':run['baseline_key']='B3-llama-'+folder.name.replace(m['mode'],'const-tool')
 elif phase=='B2':run['baseline_key']=key.rsplit('-',1)[0]+'-none'
 runs.append(run)
q=SRC/'experiments/2026-10-02-qwen-anchor/data';qi=read(next(q.glob('cross-anchor*'))/'anchor-index.json')
positions={'none':'none','prefix':'front','midfix':'middle','suffix':'marker-at-end'}
for rep in range(1,6):
 for orig in ['none','prefix']:add(q/qi[f'e1/r{rep}/{orig}'],'B1','qwen',rep,positions[orig])
 for orig,folder in [('midfix','20261002T080902Z-mapshared-midfix-eaf811'),('suffix','20261002T080910Z-mapshared-suffix-8c6f71')]:add(SRC.parent/'autogen-handoff-runs'/folder,'B1','qwen',rep,positions[orig],1)
 for n in [3,5,10,20]:
  for orig in ['none','prefix']:add(q/qi[f'e2/r{rep}/n{n}/{orig}'],'B2','qwen',rep,positions[orig])
ll=SRC/'experiments/2026-10-03-llama70b-baseline/data/session-20261003T053328Z-f7d64c'
for phase,group in [('B1','e1'),('B2','e2')]:
 for folder in sorted((ll/group).iterdir()):
  if not (folder/'manifest.json').exists():continue
  m=read(folder/'manifest.json');orig=m['variance'];assert orig in positions
  add(folder,phase,'llama',m['replicate'],positions[orig])
for folder in sorted((SRC/'experiments/2026-10-05-e5-append-G/data/formal').iterdir()):
 if (folder/'result.json').exists():add(folder,'B3','llama',read(folder/'manifest.json')['repeat'])
assert len(runs)==300 and len({r['key'] for r in runs})==300
counts=collections.Counter((r['phase'],r['model']) for r in runs)
assert dict(counts)=={('B1','qwen'):20,('B2','qwen'):40,('B1','llama'):20,('B2','llama'):40,('B3','llama'):180}
requests=sum(r['N'] for r in runs);assert requests==3210
for r in runs:
 if 'baseline_key' in r:assert any(s['key']==r['baseline_key'] for s in runs)
plan={'schema':1,'method':'native token-exact replay; never reconstruct text or inject salt tokens','runs':runs}
(OUT/'replay-plan.json').write_text(json.dumps(plan,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
manifest={'runs':len(runs),'requests':requests,'counts':{str(k):v for k,v in counts.items()},'plan_sha256':hashlib.sha256((OUT/'replay-plan.json').read_bytes()).hexdigest(),'source_response_files':len({w['source_response'] for r in runs for w in r['workers']}),'source_write_operations':0,'qwen_middle_after_vllm_reference_n':1,'B_repeats':5}
(OUT/'replay-manifest.json').write_text(json.dumps(manifest,indent=2));print(json.dumps(manifest,indent=2))

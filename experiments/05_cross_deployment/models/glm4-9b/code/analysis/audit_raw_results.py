import hashlib,json
from pathlib import Path
r=Path('/path/to/workspace/多智能体系统实验数据/fcb-dow-research/experiments/2026-10-03-c-remaining3-models/models/glm4-9b')
s=Path((r/'config/latest-session.txt').read_text().strip());status=json.loads((s/'status.json').read_text());assert status['phase']=='complete'
idx=json.loads((s/'run-index.json').read_text());count=0;salts=[]
totals={k:0 for k in ('prompt_tokens','cached_tokens','uncached_tokens','completion_tokens','total_tokens')}
for rel in idx.values():
    d=s/rel;m=json.loads((d/'manifest.json').read_text());salts.append(m['cache_salt']);prior=[]
    for row in json.loads((d/'usage.json').read_text()):
        req=json.loads((d/(row['call']+'.request.json')).read_text());res=json.loads((d/(row['call']+'.response.json')).read_text());ids=res['prompt_token_ids']
        assert req['cache_salt']==m['cache_salt']
        assert ids==json.loads((d/(row['call']+'.tokenize.json')).read_text())['tokens']
        assert len(ids)==row['prompt_tokens']==res['usage']['prompt_tokens']
        assert row['cached_tokens']==res['usage']['prompt_tokens_details']['cached_tokens']
        assert row['prompt_tokens']==row['cached_tokens']+row['uncached_tokens']
        assert row['total_tokens']==row['prompt_tokens']+row['completion_tokens']
        def lcp(a,b):return next((i for i,(x,y) in enumerate(zip(a,b)) if x!=y),min(len(a),len(b)))
        assert row['cached_tokens']<=max((lcp(p,ids) for p in prior),default=0)
        prior.append(ids);count+=1
        for k in totals:totals[k]+=row[k]
assert count==555 and len(idx)==75 and len(set(salts))==75
result=dict(formal_runs=len(idx),requests=count,independent_salts=True,cold_first_worker=True,actual_ids_match_tokenize=True,cached_tokens_bounded_by_actual_prefix=True,totals=totals)
(r/'evidence/raw-request-audit.json').write_text(json.dumps(result,indent=2))
print('GLM_RAW_AUDIT_PASSED',result,flush=True)

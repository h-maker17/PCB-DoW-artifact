"""Common sealed offline episode entry; real archives have no loading route."""
import copy,json
from pathlib import Path
from frozen_core import token_lcp,classify,pair_integrity,repeat_invariant
from observer import Observer
from input_authority import verify_episode,GateClosed
from render_binding import TEMPLATES,Renderer

def invalid(status,reason=None):return {'status':status,'reason':reason,'classification':None,'observer_calls':0}
def validate_shape(runs):
 if not isinstance(runs,list) or len(runs)!=2:return 'exactly_two_repeats_required'
 try:
  for r in runs:
   if set(r['trace'])!={'benign','attacked'} or set(r['measurements'])!={'benign','attacked'}:return 'exact_two_arms_required'
   for phase in ('benign','attacked'):
    raw=r['trace'][phase]
    if set(r['measurements'][phase])!=set(TEMPLATES):return 'exact_two_templates_required'
    if any(type(x['replica']) is not int or x['replica'] not in range(4) for x in raw['requests']):return 'unknown_request_replica'
    if {x['replica'] for x in raw['requests']}!=set(range(4)):return 'complete_N4_requests_required'
    reps=[x['replica'] for x in raw['returns']]
    if any(type(x) is not int or x not in range(4) for x in reps) and reps!=['broadcast']:return 'unknown_return_replica'
    if reps!=['broadcast'] and (len(reps)!=4 or set(reps)!=set(range(4))):return 'unique_N4_or_single_broadcast_returns_required'
    for t in TEMPLATES:
     m=r['measurements'][phase][t]; finals=m['final']
     if len(finals)!=4 or any(type(x['replica']) is not int for x in finals) or sorted(x['replica'] for x in finals)!=list(range(4)):return 'unique_N4_final_required'
     expected={(i,j) for i in range(4) for j in range(sum(x['replica']==i for x in raw['requests']))}
     actual=[(x['replica'],x['request_round']) for x in m['all_prompts']]
     if len(actual)!=len(expected) or set(actual)!=expected:return 'all_request_rounds_required'
     for item in finals:
      req=[x for x in raw['requests'] if x['replica']==item['replica']]
      if item['request_round']!=len(req)-1:return 'last_actual_request_required'
      archived=[x for x in m['all_prompts'] if (x['replica'],x['request_round'])==(item['replica'],item['request_round'])]
      if archived!=[item]:return 'final_all_prompts_binding_mismatch'
 except (KeyError,TypeError,ValueError):return 'malformed_episode'
 return None

def measure_run(*args,**kwargs):
 raise GateClosed('Standalone run cannot provide two-repeat scientific classification; use measure_episode with an issued Episode.')

def _measure_run(root,run,contract_id,history,queries,obs,binding_errors):
 raw=run['trace']; measures={};proofs={}
 for phase in ('benign','attacked'):
  measures[phase]={};proofs[phase]={}
  for t in TEMPLATES:
   final=[];details=[]
   for item in sorted(run['measurements'][phase][t]['final'],key=lambda x:x['replica']):
    replica=item['replica'];requests=[r for r in raw[phase]['requests'] if r['replica']==replica];round_no=len(requests)-1
    candidates=[r for r in raw[phase]['returns'] if r['replica']==replica]
    if not candidates:candidates=[r for r in raw[phase]['returns'] if r['replica']=='broadcast']
    native=candidates[0];bound=copy.deepcopy(item);bound['request']=requests[-1]
    binding={'call_key':native['call_key'],'source_replica':native['replica'],'target_replica':replica,'request_round':round_no}
    binding.update(run.get('binding_override',{}))
    error=binding_errors.get((phase,t,replica,round_no))
    if error:
     result={'G':{'status':'ambiguous','reason':error},'S':{'status':'ambiguous','reason':error},'proof':{'complete_binding_verified':False}}
    else:
     result=obs.observe(bound,native,binding,run.get('observer_contract',contract_id),history,queries,phase)
     result['proof']['complete_binding_verified']=True
    final.append({**item,'G':result['G'],'S':result['S']});details.append(result)
   measures[phase][t]={'lcp':token_lcp([x['token_ids'] for x in final]),'final':final};proofs[phase][t]=details
 classes={t:classify(measures['benign'][t],measures['attacked'][t]) for t in TEMPLATES}
 return {'status':'measured','classification':classes,'measurements':measures,'proofs':proofs,'observer_calls':obs.calls}

def measure_episode(root,episode,renderer):
 if type(renderer) is not Renderer or getattr(renderer.binding_error,'__func__',None) is not Renderer.binding_error or getattr(renderer.render,'__func__',None) is not Renderer.render:return invalid('gate_closed','untrusted_renderer')
 try:e=verify_episode(episode,renderer)
 except (GateClosed,RuntimeError,ValueError,OSError,KeyError,TypeError,IndexError) as exc:return invalid('gate_closed',str(exc))
 runs=e.runs
 error=validate_shape(runs)
 if error:return invalid('invalid_incomplete_episode',error)
 if any(not pair_integrity(r['trace']['benign'],r['trace']['attacked']) for r in runs):return invalid('invalid_pair')
 original=[repeat_invariant(r) for r in runs]
 if original[0]!=original[1]:return invalid('invalid_repeat')
 all_errors=[]
 for run in runs:
  errors={}
  for phase in ('benign','attacked'):
   for t in TEMPLATES:
    for item in run['measurements'][phase][t]['all_prompts']:
     requests=[x for x in run['trace'][phase]['requests'] if x['replica']==item['replica']]
     err=renderer.binding_error(item,requests[item['request_round']],t)
     if err:errors[(phase,t,item['replica'],item['request_round'])]=err
  all_errors.append(errors)
 if any(all_errors) and e.scope=='constructed_control':return invalid('invalid_input_binding',str(all_errors))
 # Registered malformed synthetic specimens can yield only ambiguous, never a scientific claim.
 obs=Observer(e.source_root)
 results=[_measure_run(root,r,e.contract,e.history,e.queries,obs,errors) for r,errors in zip(runs,all_errors)]
 normalized=[{k:v for k,v in x.items() if k!='observer_calls'} for x in results]
 if normalized[0]!=normalized[1]:return {'status':'invalid_repeat_new_outputs','classification':None,'observer_calls':obs.calls,'runs':results}
 if any(all_errors) and any(v['class']!='ambiguous' for v in results[0]['classification'].values()):raise RuntimeError('binding_failure_scientific_claim')
 return {'status':'measured','classification':results[0]['classification'],'observer_calls':obs.calls,'runs':results,'binding_failures':[[{'key':list(k),'reason':v} for k,v in d.items()] for d in all_errors],'input_receipt':e.receipt}

if __name__=='__main__':raise SystemExit('Arbitrary file classification disabled. Use test_runner.py; actual archive gate stays closed.')

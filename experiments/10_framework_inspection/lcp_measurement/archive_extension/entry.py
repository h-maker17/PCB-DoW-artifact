"""Admitted archive execution uses the accepted shared measurement function unchanged."""
import copy
from observer import Observer
from offline_entry import _measure_run
from frozen_core import pair_integrity,repeat_invariant
from render_binding import ROOT,Renderer,TEMPLATES
from archive_extension.integrity import ArchiveGateClosed,require_admission,read_json,EXT,objhash
from archive_extension.loader import verify_archive_episode
from archive_extension.source_contracts import ArchiveSourceStore
from archive_extension.query_texts import collect_query_texts

class _FixturePermit:
 def __init__(self,token):self.token=token
_FIXTURE_TOKEN=object()
def fixture_permit():return _FixturePermit(_FIXTURE_TOKEN)

def closed(reason):return {'status':'gate_closed','classification':None,'observer_calls':0,'reason':reason}
def observer_for_archive():
 obs=Observer(ROOT);obs.store=ArchiveSourceStore();return obs

def diagnostic_only(e,observer):
 history=(ROOT/'config/shared_S.txt').read_text(encoding='utf8');queries=collect_query_texts(e.runs);locations=[]
 for ri,run in enumerate(e.runs):
  for phase in ('benign','attacked'):
   for t in TEMPLATES:
    for item in run['measurements'][phase][t]['final']:
     replica=item['replica'];reqs=[x for x in run['trace'][phase]['requests'] if x['replica']==replica];native=next(x for x in run['trace'][phase]['returns'] if x['replica']==replica or x['replica']=='broadcast')
     bound=copy.deepcopy(item);bound['request']=reqs[-1];binding={'call_key':native['call_key'],'source_replica':native['replica'],'target_replica':replica,'request_round':item['request_round']}
     output=observer.observe(bound,native,binding,e.dataset['source_contract'],history,queries,phase)
     locations.append({'repeat':ri+1,'phase':phase,'template':t,'replica':replica,'diagnostic_only':True,**output})
 return locations

def measure_archive_episode(e,renderer,permit=None,diagnose_invalid=True):
 # Permission is checked before real archive loading/observation. Test permit never admits real input.
 try:
  if getattr(e,'input_kind',None)=='archive_fixture':
   if type(permit)is not _FixturePermit or permit.token is not _FIXTURE_TOKEN:raise ArchiveGateClosed('archive_fixture_execution_not_issued')
  else:
   if permit is not None:raise ArchiveGateClosed('test_permit_cannot_admit_framework_archive')
   require_admission()
  e=verify_archive_episode(e,renderer)
 except (ArchiveGateClosed,RuntimeError,ValueError,KeyError,TypeError,OSError) as exc:return closed(str(exc))
 pair_ok=all(pair_integrity(r['trace']['benign'],r['trace']['attacked']) for r in e.runs)
 repeat_ok=None if len(e.runs)!=2 else repeat_invariant(e.runs[0])==repeat_invariant(e.runs[1])
 invalid_status='invalid_pair' if not pair_ok else 'invalid_repeat' if repeat_ok is False else 'invalid_incomplete_episode' if len(e.runs)!=2 else None
 fixed=e.dataset.get('fixed_independent_invalid')
 if fixed and invalid_status!=fixed:return {'status':'invalid_historical_envelope_changed','classification':None,'observer_calls':0,'expected_invalid':fixed,'observed_invalid':invalid_status}
 if e.dataset['role']=='diagnostic' or invalid_status:
  obs=observer_for_archive();locations=diagnostic_only(e,obs) if diagnose_invalid else []
  return {'status':invalid_status or 'diagnostic_only','classification':None,'observer_calls':obs.calls,'actual_repeats':len(e.runs),'pair_valid':pair_ok,'repeat_valid':repeat_ok,'diagnostic_locations':locations,'new_LCP_computations':0,'new_classify_computations':0}
 observer=observer_for_archive();history=(ROOT/'config/shared_S.txt').read_text(encoding='utf8');queries=collect_query_texts(e.runs)
 results=[_measure_run(ROOT,r,e.dataset['source_contract'],history,queries,observer,{}) for r in e.runs]
 # Only cumulative observer_calls is instrumentation bookkeeping. Scientific output and stable proofs stay exact.
 projection=[{k:v for k,v in r.items() if k!='observer_calls'} for r in results]
 if projection[0]!=projection[1]:return {'status':'invalid_repeat_new_outputs','classification':None,'observer_calls':observer.calls,'runs':results}
 classes=results[0]['classification'];dependent=len({x['class'] for x in classes.values()})>1
 return {'status':'measured','classification':classes,'template_dependent':dependent,'actual_repeats':2,'observer_calls':observer.calls,'runs':results,'pair_valid':True,'repeat_valid':True}

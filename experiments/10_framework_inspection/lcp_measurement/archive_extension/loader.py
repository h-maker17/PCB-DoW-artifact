"""Fixed 13-file archive loader and non-measuring preparation path."""
import copy,json,hashlib,re
from archive_extension.integrity import ROOT,EXT,ArchiveGateClosed,verify_extension,read_json,verify_file,exact_manifest,objhash,sha
from archive_extension.canonical import recompute_canonical
from archive_extension.source_contracts import source_request_binding
from render_binding import Renderer,TEMPLATES
from frozen_core import pair_integrity,repeat_invariant
from offline_entry import validate_shape
_TOKEN=object()
class ArchiveEpisode:
 def __init__(self,token,dataset,runs,records,preparation,input_kind='framework_archive'):
  if token is not _TOKEN:raise ArchiveGateClosed('archive_episode_not_issued')
  self.dataset=dataset;self.runs=runs;self.records=records;self.preparation=preparation;self.input_kind=input_kind;self._token=token;self.seal=objhash(self.payload())
 def payload(self):return [self.dataset,self.runs,self.records,self.preparation,self.input_kind]

def _issue(dataset,runs,records,preparation,input_kind='framework_archive'):return ArchiveEpisode(_TOKEN,dataset,runs,records,preparation,input_kind)

def run_shape(runs,diagnostic=False):
 if not diagnostic:return validate_shape(runs)
 if len(runs)==2:return validate_shape(runs)
 if len(runs)!=1:return 'diagnostic_actual_one_repeat_required'
 # Same single-run rules as the accepted validator; no fabricated second run.
 r=runs[0]
 try:
  if set(r['trace'])!={'benign','attacked'} or set(r['measurements'])!={'benign','attacked'}:return 'exact_two_arms_required'
  for phase in ('benign','attacked'):
   raw=r['trace'][phase]
   if set(r['measurements'][phase])!=set(TEMPLATES):return 'exact_two_templates_required'
   if any(type(x['replica']) is not int or x['replica'] not in range(4) for x in raw['requests']) or {x['replica'] for x in raw['requests']}!=set(range(4)):return 'complete_strict_N4_requests_required'
   replicas=[x['replica'] for x in raw['returns']]
   if replicas!=['broadcast'] and (any(type(x)is not int for x in replicas) or len(replicas)!=4 or set(replicas)!=set(range(4))):return 'strict_unique_N4_returns_required'
   for t in TEMPLATES:
    m=r['measurements'][phase][t]
    if len(m['final'])!=4 or any(type(x['replica']) is not int for x in m['final']) or sorted(x['replica'] for x in m['final'])!=list(range(4)):return 'unique_N4_final_required'
    expect={(i,j) for i in range(4) for j in range(sum(x['replica']==i for x in raw['requests']))};actual=[(x['replica'],x['request_round']) for x in m['all_prompts']]
    if len(actual)!=len(expect) or set(actual)!=expect:return 'all_rounds_required'
    for item in m['final']:
     requests=[x for x in raw['requests'] if x['replica']==item['replica']]
     if item['request_round']!=len(requests)-1 or [x for x in m['all_prompts'] if (x['replica'],x['request_round'])==(item['replica'],item['request_round'])]!=[item]:return 'final_request_round_binding_mismatch'
 except (KeyError,TypeError,ValueError):return 'malformed_archive_episode'
 return None

def native_integrity(native,phase,policy):
 chunks=native.get('raw_chunks');returned=native.get('returned_chunks')
 if not isinstance(chunks,list) or not chunks or any(not isinstance(x,str) or not x for x in chunks):raise ArchiveGateClosed('unrecoverable_native_chunks')
 for x in chunks:x.encode('utf8',errors='strict')
 if len(set(chunks))!=len(chunks):raise ArchiveGateClosed('duplicate_native_chunks')
 key=native.get('call_key')
 if not isinstance(key,str) or not re.fullmatch(r'R(?:\d+|broadcast)_C\d{4,}',key):raise ArchiveGateClosed('native_call_key_invalid')
 expected=list(chunks)
 if phase=='attacked':expected[0]=policy['marker_format'].format(call_key=key)+expected[0]
 if returned!=expected or native.get('raw')!='\n\n'.join(chunks) or native.get('returned')!='\n\n'.join(returned):raise ArchiveGateClosed('native_return_frozen_byte_contract_mismatch')
 if native.get('sha256')!=hashlib.sha256(native['returned'].encode()).hexdigest():raise ArchiveGateClosed('native_return_digest_mismatch')

def prepare_runs(dataset,runs,records,renderer,canonical=True):
 if type(renderer) is not Renderer:raise ArchiveGateClosed('untrusted_archive_renderer')
 policy=read_json(ROOT/'config/policy.json');history=(ROOT/'config/shared_S.txt').read_text(encoding='utf8');shape=run_shape(runs,dataset['role']=='diagnostic')
 if shape:raise ArchiveGateClosed('archive_shape:'+shape)
 proofs=[]
 for ri,(run,rec) in enumerate(zip(runs,records)):
  if run['identity']!=rec['identity'] or run['code']['commit']!=rec['acquisition_commit']:raise ArchiveGateClosed('archive_dataset_identity_or_commit_mismatch')
  if sha(ROOT/rec['pipeline_source'])!=rec['pipeline_sha256']:raise ArchiveGateClosed('acquisition_pipeline_source_SHA_mismatch')
  expected_templates={t:v['sha256'] for t,v in read_json(ROOT/'config/templates_manifest.json').items() if t in TEMPLATES}
  if 'templates_sha256' in run and run['templates_sha256']!=expected_templates:raise ArchiveGateClosed('archive_template_receipt_mismatch')
  if dataset['scientific_eligible'] and 'templates_sha256' not in run:raise ArchiveGateClosed('scientific_template_receipt_missing')

  if canonical:
   rebuilt,canonical_proof=recompute_canonical(run,rec)
  else:
   # Pure test-only source fixtures use the exact raw canonical function without external exception evidence.
   rebuilt,canonical_proof=recompute_canonical(run,rec)
  for phase in ('benign','attacked'):
   
   for n in run['trace'][phase]['returns']:
    native_integrity(n,phase,policy)
    events=run['trace'][phase]['events']
    starts=[x for x in events if x.get('kind')=='retrieve_start' and x.get('call_key')==n['call_key'] and x.get('replica')==n['replica']]
    ends=[x for x in events if x.get('kind')=='retrieve_return' and x.get('call_key')==n['call_key'] and x.get('replica')==n['replica']]
    if len(starts)!=1 or len(ends)!=1 or starts[0].get('query')!=n['query'] or ends[0].get('sha256')!=n['sha256']:raise ArchiveGateClosed('native_start_return_request_lineage_mismatch')

   for t in TEMPLATES:
    for item in run['measurements'][phase][t]['all_prompts']:
     requests=[x for x in run['trace'][phase]['requests'] if x['replica']==item['replica']];req=requests[item['request_round']]
     error=renderer.binding_error(item,req,t)
     if error:raise ArchiveGateClosed('archive_complete_binding:'+error)
     # Verify original complete S using only the stored old span; do not invoke a locator.
     if item.get('S',{}).get('status')=='ok':
      a,b=item['S']['chars']
      if item['rendered'][a:b]!=history:raise ArchiveGateClosed('original_complete_S_binding_mismatch')
    for item in run['measurements'][phase][t]['final']:
     replica=item['replica'];reqs=[x for x in run['trace'][phase]['requests'] if x['replica']==replica];native=[x for x in run['trace'][phase]['returns'] if x['replica']==replica]
     if not native:native=[x for x in run['trace'][phase]['returns'] if x['replica']=='broadcast']
     if len(native)!=1:raise ArchiveGateClosed('native_final_return_binding_not_unique')
     binding=source_request_binding(native[0],reqs[-1],dataset['source_contract'])
     proofs.append({'repeat':rec['repeat'],'phase':phase,'template':t,'replica':replica,'final_request_round':item['request_round'],'source_binding':binding})
  proofs.append({'repeat':rec['repeat'],'canonical':canonical_proof})
 pair=[pair_integrity(r['trace']['benign'],r['trace']['attacked']) for r in runs]
 same=None if len(runs)!=2 else repeat_invariant(runs[0])==repeat_invariant(runs[1])
 return {'status':'BINDING_PREPARED_ONLY','dataset':dataset['id'],'role':dataset['role'],'actual_repeats':len(runs),'source_bindings':proofs,'old_pair_validity_rechecked':pair,'old_repeat_invariant_rechecked':same,'classification':None,'new_locator_calls':0,'new_LCP_calls':0,'new_classify_calls':0}

def load_archive(dataset_id,role,renderer):
 runtime=verify_extension();manifest=exact_manifest(read_json(EXT/'archive_manifest.json'));dataset=next((d for d in manifest['datasets'] if d['id']==dataset_id),None)
 if dataset is None or dataset['role']!=role:raise ArchiveGateClosed('unregistered_archive_dataset_or_role')
 runs=[]
 for rec in dataset['files']:
  original=read_json(verify_file(rec));run=copy.deepcopy(original)
  if rec['payload']=='completed_phase_traces':run['trace']=run.pop('completed_phase_traces')
  runs.append(run)
 preparation=prepare_runs(dataset,runs,dataset['files'],renderer)
 return _issue(copy.deepcopy(dataset),runs,copy.deepcopy(dataset['files']),preparation)

def verify_archive_episode(e,renderer):
 if type(e) is not ArchiveEpisode or e._token is not _TOKEN or objhash(e.payload())!=e.seal:raise ArchiveGateClosed('archive_unissued_or_mutated_episode')
 if e.input_kind!='framework_archive':
  from archive_extension.fixture_tests import verify_fixture_episode
  return verify_fixture_episode(e,renderer)
 actual=load_archive(e.dataset['id'],e.dataset['role'],renderer)
 if actual.payload()!=e.payload():raise ArchiveGateClosed('archive_loaded_identity_or_preparation_changed')
 return e

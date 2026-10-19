"""Only public input routes: registered synthetic generator or SHA allowlisted control loader."""
from pathlib import Path
import json,copy,hashlib
from render_binding import ROOT,sha,object_hash,verify_runtime
_TOKEN=object()
class GateClosed(RuntimeError):pass
class Episode:
 def __init__(self,token,runs,scope,contract,history,queries,receipt,source_root=None):
  if token is not _TOKEN:raise GateClosed('unissued_input')
  self.runs=runs;self.scope=scope;self.contract=contract;self.history=history;self.queries=queries
  self.receipt=receipt;self.source_root=Path(source_root or ROOT);self._token=token
  self.seal=object_hash(self.payload())
 def payload(self):return [self.runs,self.scope,self.contract,self.history,self.queries,self.receipt,str(self.source_root)]
def _issue(runs,scope,contract,history,queries,receipt,source_root=None):
 return Episode(_TOKEN,runs,scope,contract,history,queries,receipt,source_root)
def verify_episode(e,renderer):
 if type(e) is not Episode or e._token is not _TOKEN:raise GateClosed('unregistered_input_object')
 if object_hash(e.payload())!=e.seal:raise GateClosed('issued_input_bytes_changed')
 if e.scope not in ('synthetic','constructed_control'):raise GateClosed('real_archive_gate_closed')
 runtime=verify_runtime()
 if e.receipt['runtime_manifest_sha256']!=runtime:raise GateClosed('runtime_receipt_mismatch')
 reg=json.loads((ROOT/'config/source_registry.json').read_text(encoding='utf8'))['contracts']
 if e.contract not in reg or reg[e.contract]['scope']!=e.scope:raise GateClosed('scope_contract_mismatch')
 if hashlib.sha256(e.history.encode('utf8')).hexdigest()!=json.loads((ROOT/'config/policy.json').read_text(encoding='utf8'))['shared_S_sha256']:raise GateClosed('complete_history_SHA_mismatch')
 if e.scope=='constructed_control':
  if e.source_root.resolve()!=ROOT.resolve() or e.contract!='control_scalar':raise GateClosed('control_source_root_or_contract_mismatch')
  if e.receipt['route']!='SHA_allowlisted_control_loader':raise GateClosed('control_loader_route_mismatch')
  manifest=json.loads((ROOT/'config/control_manifest.json').read_text(encoding='utf8'))
  allowed={x['file']:x for x in manifest['files']}
  if len(e.receipt['inputs'])!=2:raise GateClosed('control_receipt_incomplete')
  rows=[allowed.get(rec.get('original_file')) for rec in e.receipt['inputs']]
  if any(x is None for x in rows):raise GateClosed('control_not_allowlisted')
  if len({x['file'] for x in rows})!=2:raise GateClosed('control_repeat_files_not_unique')
  if len({(x['variant'],x['control']) for x in rows})!=1 or [x['repeat'] for x in rows]!=[1,2]:raise GateClosed('control_episode_identity_or_repeat_mismatch')

  for rec,run in zip(e.receipt['inputs'],e.runs):
   if rec['original_file'] not in allowed:raise GateClosed('control_not_allowlisted')
   a=allowed[rec['original_file']];p=ROOT/'control_inputs'/(hashlib.sha256(rec['original_file'].encode('utf8')).hexdigest()[:24]+'.json')
   if sha(p)!=a['sha256'] or rec['sha256']!=a['sha256']:raise GateClosed('control_input_SHA_mismatch')
   if object_hash(json.loads(p.read_text(encoding='utf8')))!=object_hash(run):raise GateClosed('control_object_not_loaded_bytes')
  queries=sorted({x['query'] for r in e.runs for arm in r['trace'].values() for x in arm['returns'] if x.get('query')})
  if e.queries!=queries:raise GateClosed('control_native_queries_mismatch')
 else:
  known=json.loads((ROOT/'config/synthetic_expected.json').read_text(encoding='utf8'))['cases']+json.loads((ROOT/'config/additional_expected.json').read_text(encoding='utf8'))['cases']
  recipe=next((x for x in known if x['id']==e.receipt['case_id']),None)
  if recipe is None:raise GateClosed('unregistered_synthetic_recipe')
  if recipe.get('assembly','empty_join')!=e.contract or e.receipt['assembly']!=e.contract:raise GateClosed('synthetic_recipe_contract_mismatch')
  if e.receipt['route']!='registered_fixture_generator':raise GateClosed('synthetic_generator_route_mismatch')
  if e.receipt['generator_sha256']!=sha(ROOT/'fixtures.py'):raise GateClosed('synthetic_generator_SHA_mismatch')
  if e.receipt['runs_sha256']!=object_hash(e.runs):raise GateClosed('synthetic_output_SHA_mismatch')
  from fixtures import _build,_make_receipt,QUERY
  expected_runs,expected_history,expected_root=_build(recipe,renderer,materialize_sources=False)
  if e.source_root.resolve()!=expected_root.resolve():raise GateClosed('synthetic_source_root_mismatch')
  if e.history!=expected_history or e.queries!=[QUERY]:raise GateClosed('synthetic_history_queries_mismatch')
  if object_hash(expected_runs)!=object_hash(e.runs):raise GateClosed('synthetic_deterministic_generation_mismatch')
  if e.receipt!=_make_receipt(recipe,expected_runs):raise GateClosed('synthetic_full_generation_receipt_mismatch')
 return e
def load_control(variant,control):
 verify_runtime()
 m=json.loads((ROOT/'config/control_manifest.json').read_text(encoding='utf8'))
 rows=sorted([x for x in m['files'] if x['variant']==variant and x['control']==control],key=lambda x:x['repeat'])
 if len(rows)!=2 or [x['repeat'] for x in rows]!=[1,2]:raise GateClosed('control_not_allowlisted')
 runs=[];receipts=[]
 for x in rows:
  p=ROOT/'control_inputs'/(hashlib.sha256(x['file'].encode('utf8')).hexdigest()[:24]+'.json')
  if sha(p)!=x['sha256']:raise GateClosed('control_input_SHA_mismatch')
  runs.append(json.loads(p.read_text(encoding='utf8')));receipts.append({'original_file':x['file'],'local_file':p.relative_to(ROOT).as_posix(),'sha256':x['sha256']})
 history=(ROOT/'config/shared_S.txt').read_text(encoding='utf8')
 queries=sorted({x['query'] for r in runs for arm in r['trace'].values() for x in arm['returns'] if x.get('query')})
 return _issue(runs,'constructed_control','control_scalar',history,queries,{'route':'SHA_allowlisted_control_loader','runtime_manifest_sha256':sha(ROOT/'config/runtime_manifest.json'),'inputs':receipts})

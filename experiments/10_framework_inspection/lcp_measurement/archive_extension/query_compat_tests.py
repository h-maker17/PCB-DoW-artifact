"""Pre-registered post-result compatibility tests; synthetic inputs only."""
import ast,copy,json,hashlib
from pathlib import Path
from archive_extension.integrity import ROOT,EXT,read_json,write_json,sha,objhash,verify_extension
from archive_extension.query_texts import collect_query_texts
from archive_extension.fixture_tests import make_fixture,_build,recipes
from archive_extension.entry import measure_archive_episode,fixture_permit
from render_binding import Renderer,TEMPLATES
from observer import Observer,encode_text

def inventory(query):
 return [{'trace':{'benign':{'returns':[{'query':copy.deepcopy(query)}]},'attacked':{'returns':[]}}}]

def tests():
 verify_extension();expected=read_json(EXT/'query_compat_expected.json');out=[];renderer=Renderer()
 def add(name,ok,evidence):
  out.append({'id':name,'passed':bool(ok),'evidence':evidence});print('QUERY_COMPAT',name,'PASS' if ok else 'FAIL',flush=True)
 for spec in expected['good']:
  runs=inventory(spec['query_value']);before=copy.deepcopy(runs);got=collect_query_texts(runs)
  add('type_'+spec['id'],got==spec['expected_texts'] and runs==before,{'expected':spec['expected_texts'],'observed':got,'input_unmodified':runs==before})
 invalid=[(x['id'],x['query_value']) for x in expected['bad']]+[('bytes',b'query'),('tuple',('query',)),('set',{'query'})]
 for name,value in invalid:
  runs=inventory(value);before=copy.deepcopy(runs)
  try:got=collect_query_texts(runs);ok=False;evidence={'unexpected_texts':got}
  except TypeError as exc:ok=str(exc)=='native_query_must_be_str_or_flat_list_str';evidence={'exception':'TypeError','message':str(exc)}
  add('reject_'+name,ok and runs==before,evidence|{'input_unmodified':runs==before})
 runs=inventory(['query one','query two']);runs[0]['trace']['attacked']['returns']=[{'query':'query three'}];runs+=inventory(['query two','query four']);before=copy.deepcopy(runs)
 add('all_runs_arms_returns',collect_query_texts(runs)==['query four','query one','query three','query two'] and runs==before,{'full_inventory_captured':True,'input_unmodified':runs==before})
 tree=ast.parse((EXT/'entry.py').read_text(encoding='utf8'));functions={x.name:x for x in tree.body if isinstance(x,ast.FunctionDef)}
 shared=all(sum(isinstance(x,ast.Call) and isinstance(x.func,ast.Name) and x.func.id=='collect_query_texts' for x in ast.walk(functions[name]))==1 for name in ('diagnostic_only','measure_archive_episode'))
 add('both_entry_paths_share_helper',shared,{'scientific_and_diagnostic':shared})
 baseline=read_json(ROOT/'freeze_manifest.json');mismatches=[x['file'] for x in baseline['files'] if sha(ROOT/x['file'])!=x['sha256']]
 add('baseline_math_observer_inputs_parameters_48_unchanged',not mismatches,{'baseline_manifest_entries':len(baseline['files']),'mismatches':mismatches})
 # The unchanged Observer reads fixed, hashed synthetic source ASTs; no fake plan or coordinates.
 for spec in expected['observer_cases']:
  recipe=next(x for x in recipes() if x['id']=='fixture_native_lc');_,runs,_=_build(recipe,renderer);run=runs[0];history=(ROOT/'config/shared_S.txt').read_text(encoding='utf8')
  obs=Observer(EXT/'sources/query_guard_truth');plan=obs.store.plan(spec['contract'],3)
  for phase,arm in run['trace'].items():
   for native in arm['returns']:native['query']=copy.deepcopy(spec['query_value'])
   for event in arm['events']:
    if event['kind']=='retrieve_start':event['query']=copy.deepcopy(spec['query_value'])
  before=objhash(run);texts=collect_query_texts([run]);assert objhash(run)==before
  native=run['trace']['benign']['returns'][0];block=''.join(encode_text(native['returned_chunks'][value] if kind=='chunk' else value,plan['codec']) for kind,value in plan['fragments'])
  record=copy.deepcopy(run['trace']['benign']['requests'][0]);record['body']['messages'][0]['content']='Context:\n'+block+'\nShared history:\n'+history;record['raw_body_text']=json.dumps(record['body'],ensure_ascii=False)
  observed=[]
  for template in TEMPLATES:
   item={'replica':0,'request_round':0,**renderer.render(record['body'],template),'request':record};binding={'call_key':native['call_key'],'source_replica':0,'target_replica':0,'request_round':0}
   observed.append({'template':template,**obs.observe(item,native,binding,spec['contract'],history,texts,'benign')})
  ok=all(x['G']['status']==spec['expected_G_status'] and x['G'].get('reason')==spec['expected_reason'] and x['S']['status']=='ok' for x in observed)
  add('full_query_source_proved_gap_'+spec['id'],ok,{'expected_G_status':spec['expected_G_status'],'expected_reason':spec['expected_reason'],'inspection_texts':texts,'observed':observed,'no_actual_archive_input':True})
 for string_id,list_id in expected['paired_entry_cases']:
  left=make_fixture(string_id,renderer);right=make_fixture(list_id,renderer);left_before=objhash(left.payload());right_before=objhash(right.payload())
  first=measure_archive_episode(left,renderer,fixture_permit());second=measure_archive_episode(right,renderer,fixture_permit())
  unchanged=left_before==objhash(left.payload()) and right_before==objhash(right.payload())
  equal=first==second
  add('paired_full_entry_'+list_id,equal and unchanged and second['status']=='measured',{'full_scientific_result_equal':equal,'result_sha256':objhash(first),'input_raw_canonical_event_query_models_unmodified':unchanged,'status':second['status'],'classes':{k:v['class'] for k,v in (second.get('classification') or {}).items()}})
 e=make_fixture('fixture_query_inside_native_chunk',renderer);before=objhash(e.payload());result=measure_archive_episode(e,renderer,fixture_permit())
 add('list_full_query_in_native_chunk_allowed',result['status']=='measured' and all(x['class']=='full_collapse' for x in result['classification'].values()) and before==objhash(e.payload()),{'classes':result.get('classification'),'raw_canonical_models_unmodified':before==objhash(e.payload())})
 for case_id in expected['diagnostic_cases']:
  e=make_fixture(case_id,renderer);before=objhash(e.payload());result=measure_archive_episode(e,renderer,fixture_permit(),diagnose_invalid=True);status=e.dataset['fixed_independent_invalid'];calls=len(e.runs)*2*2*4
  add('diagnostic_shared_helper_'+case_id,result['status']==status and result['classification'] is None and result['observer_calls']==calls and result['new_LCP_computations']==0 and result['new_classify_computations']==0 and before==objhash(e.payload()),{'independent_status':result['status'],'observer_calls':result['observer_calls'],'expected_calls':calls,'classification':result['classification'],'raw_canonical_models_unmodified':before==objhash(e.payload())})
 write_json(EXT/'results/query_compatibility_results.json',out)
 summary={'total':len(out),'passed':sum(x['passed'] for x in out),'failed':[x['id'] for x in out if not x['passed']],'all_passed':all(x['passed'] for x in out),'runtime_manifest_sha256':sha(EXT/'runtime_manifest.json'),'post_result_technical_compatibility_fix':True,'real_archive_locator_LCP_classify_calls':0,'original_fixture_expectations_changed':False,'original_48_expectations_changed':False}
 write_json(EXT/'results/query_compatibility_summary.json',summary);print(json.dumps(summary,indent=2),flush=True)
 return 0 if summary['all_passed'] else 1
if __name__=='__main__':raise SystemExit(tests())

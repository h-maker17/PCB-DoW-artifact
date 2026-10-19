"""Registered synthetic archive-shaped fixtures. No real archived object can be test-admitted."""
import copy,json,hashlib
from archive_extension.integrity import ROOT,EXT,read_json,objhash,sha,ArchiveGateClosed,write_json,exact_manifest,require_admission,verify_extension
from archive_extension.loader import _issue,prepare_runs,load_archive
from archive_extension.entry import measure_archive_episode,fixture_permit
from archive_extension.canonical import extracted_functions
from render_binding import Renderer,TEMPLATES
from frozen_core import locate,token_lcp

QUERY='What is the laboratory retention policy?'
def recipes():return read_json(EXT/'fixture_expected.json')['cases']
def _build(spec,renderer):
 history=(ROOT/'config/shared_S.txt').read_text(encoding='utf8');scalar=spec.get('scalar',False)
 chunks=['FIRST_ARCHIVE_FIXTURE native evidence block.'] if scalar else ['FIRST_ARCHIVE_FIXTURE native evidence block.','SECOND_ARCHIVE_FIXTURE native evidence block.','THIRD_ARCHIVE_FIXTURE native evidence block.']
 query_value=spec.get('query_value',QUERY)
 contract=spec['contract'];run={'identity':'registered_archive_fixture:'+spec['id'],'code':{'commit':'FIXTURE_ONLY_NO_NATIVE_EXECUTION'},'templates_sha256':{t:v['sha256'] for t,v in read_json(ROOT/'config/templates_manifest.json').items() if t in TEMPLATES},'trace':{},'measurements':{}}
 for phase in ('benign','attacked'):
  raw={'call_count':4,'returns_byte_identical':phase=='benign','events':[],'returns':[],'requests':[]}
  for replica in range(4):
   key=f'R{replica}_C0000';returned=list(chunks)
   if phase=='attacked':returned[0]='[SCAN_CALL_'+key+'] '+returned[0]
   native={'replica':replica,'call_key':key,'query':copy.deepcopy(query_value),'raw_chunks':list(chunks),'returned_chunks':returned,'raw':'\n\n'.join(chunks),'returned':'\n\n'.join(returned),'native_result_metadata':{'score':0.5}}
   native['sha256']=hashlib.sha256(native['returned'].encode()).hexdigest();raw['returns'].append(native)
   raw['events'] += [{'kind':'retrieve_start','replica':replica,'call_key':key,'query':copy.deepcopy(query_value),'native_backend':'constructed_archive_fixture'}, {'kind':'retrieve_return','replica':replica,'call_key':key,'sha256':native['sha256']}]
   block=''.join(returned)
   if spec.get('mutation')=='unknown_gap':block=returned[0]+'Instructions: unrelated'+''.join(returned[1:])
   content=history+'\nContext:\n'+block if spec.get('layout')=='S_before_G' else 'Context:\n'+block+'\nShared history:\n'+history
   messages=[{'role':'system','content':content},{'role':'user','content':QUERY+'\n'+f'[REPLICA_{replica:04d}]'}]
   if spec.get('mutation')=='cross_messages':messages=[{'role':'system','content':returned[0]},{'role':'user','content':''.join(returned[1:])+'\n'+history},{'role':'user','content':f'[REPLICA_{replica:04d}]'}]
   if spec.get('mutation')=='pair_citation' and phase=='attacked':messages[-1]['content']+=' citation UUID differs'
   body={'model':'offline_synthetic_archive_no_server','messages':messages,'temperature':0,'user':str(replica)};raw['requests'].append({'replica':replica,'body':body,'raw_body_text':json.dumps(body,ensure_ascii=False)})
  run['trace'][phase]=raw;run['measurements'][phase]={}
  for t in TEMPLATES:
   items=[]
   for req in raw['requests']:
    item={'replica':req['replica'],'request_round':0,**renderer.render(req['body'],t)};item['G']={'status':'not_measured'};item['S']=locate(item['rendered'],history,item['offsets']);items.append(item)
   run['measurements'][phase][t]={'final':items,'all_prompts':copy.deepcopy(items),'lcp':token_lcp([x['token_ids'] for x in items])}
 fn,_=extracted_functions(ROOT/'sources/pipeline_8ee30445.txt',{'canonical_trace'});run['canonical_trace']={phase:fn['canonical_trace'](raw) for phase,raw in run['trace'].items()}
 # Serialized archive objects have independent raw and canonical subobjects.
 run=json.loads(json.dumps(run,ensure_ascii=False))
 runs=[copy.deepcopy(run) for _ in range(spec.get('repeats',2))]
 mutation=spec.get('mutation')
 if mutation=='repeat_score':
  runs[1]['trace']['benign']['returns'][0]['native_result_metadata']['score']+=5.86e-8;runs[1]['canonical_trace']['benign']=fn['canonical_trace'](runs[1]['trace']['benign'])
 if mutation=='canonical_forged':
  for r in runs:r['canonical_trace']['benign']['returns']['0'][0]['native_result_metadata']['score']=123.0
 if mutation=='missing_replica':
  for r in runs:
   for arm in r['trace'].values():
    arm['requests']=[x for x in arm['requests'] if x['replica']!=3];arm['returns']=[x for x in arm['returns'] if x['replica']!=3]
   for arm in r['measurements'].values():
    for m in arm.values():m['final']=m['final'][:3];m['all_prompts']=m['all_prompts'][:3]
 if mutation=='template_wrong':
  for r in runs:r['templates_sha256']['llama33']['tokenizer.json']='0'*64
 if mutation in ('rendered_wrong','tokens_wrong','offsets_wrong','messages_wrong'):
  for r in runs:
   m=r['measurements']['benign']['llama33'];item=m['final'][0]
   if mutation=='rendered_wrong':item['rendered']='X'+item['rendered'][1:]
   if mutation=='tokens_wrong':item['token_ids'][0]+=1
   if mutation=='offsets_wrong':item['offsets'][1]=[0,1]
   if mutation=='messages_wrong':item['messages'][-1]['content']+='wrong'
   m['all_prompts'][0]=copy.deepcopy(item)
 dataset={'id':spec['id'],'role':spec.get('role','main'),'source_contract':contract,'expected_repeats':len(runs),'scientific_eligible':spec.get('role','main')!='diagnostic','fixed_independent_invalid':spec.get('fixed_invalid')}
 records=[{'dataset':spec['id'],'role':dataset['role'],'repeat':i+1,'identity':run['identity'],'acquisition_commit':'FIXTURE_ONLY_NO_NATIVE_EXECUTION','pipeline_source':'sources/pipeline_8ee30445.txt','pipeline_sha256':sha(ROOT/'sources/pipeline_8ee30445.txt'),'fixture_generator_sha256':sha(EXT/'fixture_tests.py'),'generated_runs_sha256':objhash(runs)} for i in range(len(runs))]
 return dataset,runs,records

def make_fixture(case_id,renderer):
 spec=next((x for x in recipes() if x['id']==case_id),None)
 if spec is None:raise ArchiveGateClosed('unregistered_archive_fixture')
 dataset,runs,records=_build(spec,renderer)
 return _issue(dataset,runs,records,{'fixture_case_id':case_id,'generator_sha256':sha(EXT/'fixture_tests.py')},'archive_fixture')

def verify_fixture_episode(e,renderer):
 verify_extension();expected=make_fixture(e.dataset['id'],renderer)
 if expected.payload()!=e.payload():raise ArchiveGateClosed('archive_fixture_deterministic_provenance_mismatch')
 prepare_runs(e.dataset,e.runs,e.records,renderer)
 return e

def tests():
 renderer=Renderer();out=[]
 for spec in recipes():
  e=make_fixture(spec['id'],renderer);r=measure_archive_episode(e,renderer,fixture_permit(),diagnose_invalid=False);classes={t:v['class'] for t,v in (r.get('classification') or {}).items()}
  expected=spec['expected_status'];ok=r['status']==expected and (all(x==spec['expected_class'] for x in classes.values()) and set(classes)==set(TEMPLATES) if spec['expected_class'] else r.get('classification') is None)
  if expected in ('gate_closed','invalid_pair','invalid_repeat'):ok=ok and r['observer_calls']==0
  out.append({'id':spec['id'],'passed':bool(ok),'expected':spec,'observed_classes':classes,'result':{k:v for k,v in r.items() if k!='runs'}});print('ARCHIVE_FIXTURE',spec['id'],'PASS' if ok else 'FAIL',r['status'],classes,flush=True)
 # Authorization and manifest corruption are tested on pure synthetic/mutated manifests, never by measuring real archives.
 def gate(name,fn):
  try:fn();r={'status':'unexpected_success'};ok=False
  except (ArchiveGateClosed,ValueError,KeyError,TypeError) as exc:r={'status':'gate_closed','classification':None,'observer_calls':0,'reason':str(exc)};ok=True
  out.append({'id':name,'passed':ok,'result':r});print('ARCHIVE_GATE',name,'PASS' if ok else 'FAIL',flush=True)
 gate('real_execute_before_parent_admission',require_admission)
 gate('unknown_archive_dataset',lambda:load_archive('unknown_dataset','main',renderer))
 gate('wrong_archive_role',lambda:load_archive('langchain_original_main','replication',renderer))
 manifest=read_json(EXT/'archive_manifest.json')
 for name,mutation in [('archive_manifest_missing_file','missing'),('archive_manifest_duplicate_repeat','duplicate'),('archive_manifest_wrong_dataset','dataset'),('archive_manifest_wrong_role','role')]:
  m=copy.deepcopy(manifest)
  if mutation=='missing':m['datasets'][0]['files'].pop()
  if mutation=='duplicate':m['datasets'][0]['files'][1]=copy.deepcopy(m['datasets'][0]['files'][0])
  if mutation=='dataset':m['datasets'][0]['files'][0]['dataset']='mem0_diagnostic'
  if mutation=='role':m['datasets'][0]['role']='replication'
  gate(name,lambda m=m:exact_manifest(m))
 e=make_fixture('fixture_native_lc',renderer)
 for name,mutation in [('reissued_archive_wrong_dataset','dataset'),('reissued_archive_wrong_role','role'),('reissued_archive_wrong_contract','contract'),('reissued_archive_forged_canonical','canonical')]:
  bad=copy.deepcopy(e)
  if mutation=='dataset':bad.dataset['id']='langchain_original_main'
  if mutation=='role':bad.dataset['role']='replication'
  if mutation=='contract':bad.dataset['source_contract']='native_ow'
  if mutation=='canonical':bad.runs[0]['canonical_trace']['benign']['call_count']=999
  bad=_issue(bad.dataset,bad.runs,bad.records,bad.preparation,'archive_fixture');r=measure_archive_episode(bad,renderer,fixture_permit(),diagnose_invalid=False);ok=r['status']=='gate_closed' and r['observer_calls']==0 and r['classification'] is None;out.append({'id':name,'passed':ok,'result':r});print('ARCHIVE_GATE',name,'PASS' if ok else 'FAIL',flush=True)
 e=make_fixture('fixture_native_lc',renderer);e.input_kind='framework_archive';e.seal=objhash(e.payload());r=measure_archive_episode(e,renderer,fixture_permit(),False);out.append({'id':'test_permit_cannot_admit_actual_archive_scope','passed':r['status']=='gate_closed' and r['observer_calls']==0,'result':r})
 # Recoverable file path with changed bytes, actual parser is used but the specimen is pure synthetic.
 from archive_extension.integrity import verify_file
 specimen=EXT/'results/tampered_input_specimen.json';write_json(specimen,{'pure_synthetic_specimen':True});rec={'copy_file':specimen.relative_to(ROOT).as_posix(),'sha256':sha(specimen),'bytes':specimen.stat().st_size};specimen.write_bytes(specimen.read_bytes()+b'\n');gate('archive_input_path_same_bytes_changed',lambda:verify_file(rec))
 
 from archive_extension.reporting import compact,reports
 samples=[]
 for case_id in ('fixture_native_lc','fixture_native_lc_replication','fixture_native_ow_after_S','fixture_native_ag_after_S','fixture_native_ca_after_S','fixture_mem0_score_invalid_retained','fixture_DeerFlow_pair_invalid_one_actual_repeat'):
  e=make_fixture(case_id,renderer);result=measure_archive_episode(e,renderer,fixture_permit(),diagnose_invalid=True);samples.append(compact(e,result))
 sample_output=EXT/'results/SYNTHETIC_SAMPLE_REPORT'
 reports(sample_output,samples,'SYNTHETIC_DEMO_ONLY_NO_REAL_NEW_RESULT')
 table=read_json(sample_output/'old_new_table.json');roles=[x['role'] for x in samples]
 report_ok=len(table)==14 and len(samples)==7 and roles.count('bounded_recheck')==3 and roles.count('diagnostic')==2 and roles.count('main')==1 and roles.count('replication')==1 and all(x['dataset'].startswith('fixture_') for x in table) and all(x['new_scientific_class'] is None for x in table if x['role']=='diagnostic')
 out.append({'id':'synthetic_seven_role_reporting_end_to_end','passed':report_ok,'sample_directory':sample_output.relative_to(ROOT).as_posix(),'summary_rows':len(table),'real_archive_data_used':False});print('ARCHIVE_REPORT_E2E','PASS' if report_ok else 'FAIL',flush=True)
 write_json(EXT/'results/archive_fixture_results.json',out);summary={'total':len(out),'passed':sum(x['passed'] for x in out),'failed':[x['id'] for x in out if not x['passed']],'all_passed':all(x['passed'] for x in out),'runtime_manifest_sha256':sha(EXT/'runtime_manifest.json'),'real_archive_locator_calls':0,'real_archive_LCP_calls':0,'real_archive_classify_calls':0};write_json(EXT/'results/archive_test_summary.json',summary);print(json.dumps(summary,indent=2),flush=True)
 return 0 if summary['all_passed'] else 1
if __name__=='__main__':raise SystemExit(tests())

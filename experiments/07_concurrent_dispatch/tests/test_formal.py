import pathlib,sys,tempfile,json,unittest,asyncio,math,hashlib,shutil,zipfile,copy,os
from unittest.mock import patch
sys.dont_write_bytecode=True
REV=pathlib.Path(os.environ['G_FORMAL_REVISION_ROOT']);sys.path.insert(0,str(REV/'code'))
from formal_core import *
from review_fixtures import reviewed_recovery as recover_unfinished,retain_and_resume
from gformal_archive import archive,rebuild
OUT=pathlib.Path(os.environ['G_FORMAL_TEST_OUTPUT']);OUT.mkdir(parents=True,exist_ok=True)
DOCS=read(REV/'data/documents.json');CELLS=schedule(DOCS)
def minimal_manifest():return dict(experiment_id='G_FORMAL_CPU_FIXTURE',phase='formal',model='llama',simulation=True,D=30,R=3)
class FakeBackend:
 """Explicit synthetic backend: no HTTP, model or GPU access."""
 def __init__(self,fail=None):self.calls=0;self.tools=0;self.fail=fail;self.drains=0
 async def execute(self,cell,info,dest,guard):
  guard();n=cell['N'];self.tools+=1 if cell['arm']=='B' else n
  if self.fail=='exception':raise RuntimeError('synthetic before generation')
  requests=[]
  for i in range(n):
   self.calls+=1;p=100;h=0 if cell['arm']=='A' or i==0 else 80;t=i*.00001
   requests.append(dict(request_id=info['attempt_id']+f'-r{i}',response_id=info['attempt_id']+f'-response{i}',replica_id=i+1,P_i=p,h_i=h,O_i=2,native_ids_exact_offline=True,metric_valid=True,prompt_token_ids_sha256=hashlib.sha256(json.dumps(list(range(100))).encode()).hexdigest(),HTTP_status=200,success=True,client_send_monotonic=t,client_done_monotonic=t+.001))
  isolation={'engine_reset':{'success':True},'before':{'vllm:num_requests_running':0,'vllm:num_requests_waiting':0},'after':{'vllm:num_requests_running':0,'vllm:num_requests_waiting':0}}
  P=sum(r['P_i'] for r in requests);H=sum(r['h_i'] for r in requests)
  row=dict(complete=True,P=P,H=H,U=P-H,O=2*n,tool_call_count=1 if cell['arm']=='B' else n,round_latency=.001+(n-1)*.00001,expected_requests=n,sent_requests=n,completed_requests=n,failed_requests=0,evidence={'backend_overlap_status':'confirmed_none' if cell['schedule']=='S' else 'unknown','request_mapping_fraction':None,'completed_scheduled_steps_fraction':None},synthetic_CPU_fixture=True,physical_GPU_validation=False)
  if self.fail=='capacity':
   requests[0].update(success=False,HTTP_status=500,metric_valid=False,P_i=None,h_i=None,O_i=None);row.update(complete=False,P=None,H=None,U=None,failure_class='capacity_failure')
  if self.fail=='meter':requests[0]['h_i']=101
  if self.fail=='burst':requests[-1]['client_send_monotonic']=.06
  if self.fail=='duplicate':requests[-1]['request_id']=requests[0]['request_id']
  save(dest/'synthetic-requests.json',requests);save(dest/'isolation.json',isolation)
  return row,requests,isolation,{i+1:list(range(100)) for i in range(n)},.1
 async def drain_after(self,cell,row):self.drains+=1
 def failure_snapshot(self):return {'sent_count_known':True,'sent_requests':0,'failure_class':'transport_protocol_failure'}
class FormalTests(unittest.IsolatedAsyncioTestCase):
 def temp(self):return tempfile.TemporaryDirectory(dir=OUT)
 def runner(self,p,cells=None,backend=None):return Runner(p,minimal_manifest(),CELLS if cells is None else cells,'a'*64,backend or FakeBackend(),lambda:None)
 def decision(self,r):
  e=next(e for e in r.ledger.events if e['event']=='finished')
  return e,dict(approved=True,reason=e['reason'],retry_of=e['attempt_id'],source_revision='a'*64,review_reference='synthetic technical adjudication',result_sha256=e['result_sha256'])
 def resume(self,r):return retain_and_resume(r.ledger)
 def test_schedule_complete_deterministic_original30(self):
  self.assertEqual(CELLS,schedule(DOCS));self.assertEqual(len(CELLS),1620);self.assertEqual(sum(c['N'] for c in CELLS),20250);self.assertEqual(len({cellkey(c) for c in CELLS}),1620)
  self.assertEqual(sum((1 if c['arm']=='B' else c['N']) for c in CELLS),14040)
  self.assertTrue(all(d['used_before'] for d in DOCS));self.assertFalse({d['id'] for d in DOCS}&{d['id'] for d in read(REV/'data/pilot-documents.json')})
 def test_duplicate_missing_position_and_requests_rejected(self):
  for bad in (CELLS[:-1],CELLS[:-1]+[CELLS[0]]):
   with self.assertRaises(ValueError):validate_schedule(bad,[d['id'] for d in DOCS])
  x=copy.deepcopy(CELLS);x[0]['position']=99
  with self.assertRaises(ValueError):validate_schedule(x,[d['id'] for d in DOCS])
 async def test_full_matrix_pipeline_counts_noop_resume(self):
  with self.temp() as p:
   backend=FakeBackend();r=self.runner(p,backend=backend);out=await r.run('CPU-full');self.assertEqual(out['attempted_original_rounds'],1620);self.assertEqual(backend.calls,20250);self.assertEqual(backend.tools,14040);self.assertEqual(out['first']['invalid_or_failed'],0)
   same=self.runner(p,backend=backend);self.assertEqual(out,await same.run('CPU-noop'));self.assertEqual(backend.calls,20250)
   lock=pathlib.Path(p)/'protocol/validity-lock.json';lock_validity(r.ledger,lock,'synthetic validity review before effects')
   result=analysis(r.ledger,lock,DOCS);self.assertEqual(result['primary']['CAF']['n_valid'],30);self.assertEqual(result['interaction']['log']['n_valid'],30);self.assertEqual(len(result['all_cost_contrasts']),36);self.assertEqual(result['counts']['first']['known_actual_sent'],20250)
   self.assertEqual(result['primary_direction'],'increase');self.assertTrue(result['no_pilot_records'])
   save(OUT/'full-matrix-evidence.json',dict(GPU_requests=0,backend='explicit synthetic CPU fixture',rounds=1620,model_mock_requests=20250,actual_tool_mock_calls=14040,all_coordinator_ledger_validator_stats_methods_real=True,formal_model_results=False,counts=result['counts'],validity_lock_before_effect=True))
 async def test_persist_then_pause_and_one_reviewed_retry(self):
  c=next(c for c in CELLS if c['N']==20 and c['schedule']=='C');cells=[c]
  with self.temp() as p:
   r=self.runner(p,cells,FakeBackend('meter'))
   with self.assertRaises(RuntimeError):await r.run('CPU-meter')
   self.assertEqual(r.ledger.counts()['first']['attempts'],1);self.assertFalse(r.ledger.unfinished());e,d=self.decision(r)
   with self.assertRaises(ValueError):await self.runner(p,cells).run('CPU-unapproved-resume')
   resume=self.resume(r);d.update(adjudication_sha256=resource_state(r.ledger)['overrides'][e['attempt_id']]['adjudication_sha256'],resource_state_sha256=resource_state(r.ledger)['binding_sha256'])
   r2=self.runner(p,cells);await r2.run('CPU-retry',{e['logical_id']:d},resume);self.assertEqual(r2.ledger.counts()['retry']['attempts'],1)
   with self.assertRaises(ValueError):r2.ledger.start(c,'CPU-third',d)
 async def test_exception_zero_sent_is_not_twenty_failed_requests(self):
  c=CELLS[0]
  with self.temp() as p:
   r=self.runner(p,[c],FakeBackend('exception'))
   with self.assertRaises(RuntimeError):await r.run('CPU-pre-generation-exception')
   e=next(e for e in r.ledger.events if e['event']=='finished');self.assertEqual(e['sent_requests'],0);self.assertTrue(e['sent_count_known']);self.assertIsNone(e['failed_requests'])
 async def test_capacity_retained_no_technical_retry(self):
  with self.temp() as p:
   r=self.runner(p,CELLS[:2],FakeBackend('capacity'));await r.run('CPU-capacity');self.assertEqual(r.ledger.counts()['first']['attempts'],2)
   e,d=self.decision(r)
   with self.assertRaises(ValueError):r.ledger.start(CELLS[0],'CPU-retry-capacity',d)
 async def test_duplicate_ids_and_burst_fail_closed(self):
  c=next(c for c in CELLS if c['N']==20 and c['schedule']=='C')
  for mode in ('duplicate','burst'):
   with self.temp() as p:
    r=self.runner(p,[c],FakeBackend(mode))
    with self.assertRaises(RuntimeError):await r.run('CPU-'+mode)
    self.assertEqual(r.ledger.counts()['first']['invalid_or_failed'],1)
 def test_unfinished_does_not_reset_attempt_or_retry(self):
  c=CELLS[0]
  with self.temp() as p:
   l=Ledger(p,'G_FORMAL_CPU_FIXTURE',[c],'a'*64);l.plan();l.start(c,'CPU-killed')
   with self.assertRaises(ValueError):l.start(c,'CPU-restarted')
   for evidence in ({},{'experiment_id':'G_FORMAL_CPU_FIXTURE','source_revision':'a'*64,'G_service_stopped':True,'own_port_closed':False,'owned_GPU_idle':True}):
    with self.assertRaises(ValueError):recover_unfinished(l,evidence,'review')
   good={'experiment_id':'G_FORMAL_CPU_FIXTURE','source_revision':'a'*64,'G_service_stopped':True,'own_port_closed':True,'owned_GPU_idle':True}
   recover_unfinished(l,good,'synthetic cleanup review');e=next(e for e in l.events if e['event']=='finished');self.assertFalse(e['sent_count_known']);self.assertIsNone(e['sent_requests']);self.assertEqual(e['reason'],'log_corruption')
   self.assertFalse(l.unfinished())
 def test_foreign_pilot_and_partial_ledger_rejected(self):
  with self.temp() as p:
   l=Ledger(p,'G_FORMAL_CPU_FIXTURE',[CELLS[0]],'a'*64);l.plan();raw=l.path.read_bytes();l.path.write_bytes(raw[:-1])
   with self.assertRaises(RuntimeError):l.reload()
   l.path.write_bytes(raw.replace(b'"formal"',b'"pilot"'))
   with self.assertRaises(ValueError):l.reload()
 async def test_disk_guard_before_attempt_or_backend(self):
  with self.temp() as p:
   b=FakeBackend();r=self.runner(p,[CELLS[0]],b)
   def stop():raise RuntimeError('synthetic disk20GiB floor')
   r.guard=stop
   with self.assertRaises(RuntimeError):await r.run('CPU-no-disk')
   self.assertEqual(b.calls,0);self.assertEqual(r.ledger.counts()['attempted_original_rounds'],0)
 async def test_validity_lock_tamper_and_raw_hash_rejected(self):
  with self.temp() as p:
   r=self.runner(p,[CELLS[0]]);await r.run('CPU-one');lock=pathlib.Path(p)/'protocol/validity-lock.json';lock_validity(r.ledger,lock,'synthetic review')
   event=next(e for e in r.ledger.events if e['event']=='finished');(pathlib.Path(p)/event['result_path']).write_text('{}')
   with self.assertRaises(ValueError):analysis(r.ledger,lock,DOCS)
 def test_bootstrap_exact_and_no_repeat_resampling(self):
  r=bootstrap([0,math.log(4)],True);self.assertAlmostEqual(r['estimate'],2);self.assertEqual(r['CI95'],[1,4]);self.assertEqual(r,bootstrap([0,math.log(4)],True));self.assertIsNone(bootstrap([1])['CI95'])
 async def test_archive_rebuild_pipeline(self):
  # Small real frozen schedule subset retained as unattempted, not fabricated completed.
  with self.temp() as p:
   root=pathlib.Path(p)/'raw';m=read(REV/'experiment-manifest.json');source=verify_source(REV);backend=FakeBackend();r=Runner(root,m,read(REV/'schedule.json'),source,backend,lambda:None)
   # Execute only the actual first cell; the complete remaining1620 plan stays explicit.
   original=r.cells;r.cells=original[:1];await r.run('CPU-archive');r.cells=original
   lock_validity(r.ledger,root/'protocol/validity-lock.json','synthetic pre-effect review');expected=analysis(r.ledger,root/'protocol/validity-lock.json',DOCS)
   from gformal_reports import reports
   rows,decisions=r.ledger.selected();expected['secondary_tables']=reports(r.ledger,rows,decisions,DOCS)
   zip_path=pathlib.Path(p)/'evidence.zip';meta=archive(root,REV,zip_path);got=rebuild(zip_path,pathlib.Path(p)/'rebuilt');self.assertEqual(expected,got);self.assertTrue(meta['files']>0)
 def test_manifest_defaults_cannot_launch_Popen(self):
  import gformal_stage
  with patch.object(gformal_stage.subprocess,'Popen') as proc:
   with self.assertRaises((ValueError,FileNotFoundError)):gformal_stage.main(REV/'approval-template.json','formal-CPU-must-not-start')
   proc.assert_not_called()
if __name__=='__main__':
 result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(FormalTests))
 save(OUT/'formal-tests-result.json',dict(passed=result.wasSuccessful(),tests_run=result.testsRun,errors=len(result.errors),failures=len(result.failures),GPU_requests=0,scope='formal coordinator/ledger/validator/analysis/archive CPU regression; production HTTP separate integration test'))
 raise SystemExit(0 if result.wasSuccessful() else 1)

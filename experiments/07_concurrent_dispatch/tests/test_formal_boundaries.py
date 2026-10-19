import os,pathlib,sys,unittest,tempfile,math,copy
REV=pathlib.Path(os.environ['G_FORMAL_REVISION_ROOT']);sys.path.insert(0,str(REV/'tests'));sys.path.insert(0,str(REV/'code'))
from test_formal import FakeBackend,DOCS,CELLS,OUT,minimal_manifest
from formal_core import *
from unittest.mock import patch
class Boundaries(unittest.IsolatedAsyncioTestCase):
 def test_external_GPU_conflict_stops_without_killing_other_process(self):
  import gformal_stage
  m=read(REV/'experiment-manifest.json')
  with patch.object(gformal_stage,'require_disk'),patch.object(gformal_stage.subprocess,'check_output',side_effect=[m['GPU_UUID']+'\n','4321\n']),patch.object(gformal_stage.subprocess,'Popen') as p:
   with self.assertRaisesRegex(ValueError,'GPU occupied'):gformal_stage.resource_guard(m,preflight=True)
   p.assert_not_called()
 async def test_descriptive_schedule_cost_and_cell_denominators(self):
  from gformal_reports import reports
  with tempfile.TemporaryDirectory(dir=OUT) as p:
   ids={d['id'] for d in DOCS[:2]};cells=[c for c in CELLS if c['document_id'] in ids];r=Runner(p,minimal_manifest(),cells,'a'*64,FakeBackend(),lambda:None);await r.run('CPU-secondary');rows,v=r.ledger.selected();x=reports(r.ledger,rows,v,DOCS)
   self.assertEqual(len(x['schedule_cost_comparisons']),36);self.assertEqual(len(x['per_cell_plan_failure_raw_metrics']),18)
   for c in x['schedule_cost_comparisons']:self.assertEqual(c['ratio']['estimate'],1);self.assertEqual(c['ratio']['n_valid'],2);self.assertEqual(c['ratio']['CI95'],[1,1])
   for c in x['per_cell_plan_failure_raw_metrics']:self.assertEqual(c['planned_rounds'],6);self.assertEqual(c['first']['attempts'],6);self.assertEqual(c['first']['known_actual_sent'],6*c['N']);self.assertEqual(c['first']['request_failure_rate'],0)
 async def test_primary_two_arms_three_repeats_independent_of_S(self):
  with tempfile.TemporaryDirectory(dir=OUT) as p:
   ids={d['id'] for d in DOCS[:2]};cells=[c for c in CELLS if c['document_id'] in ids and c['N']==20 and c['schedule']=='C' and c['arm'] in ('A','D')]
   r=Runner(p,minimal_manifest(),cells,'a'*64,FakeBackend(),lambda:None);await r.run('CPU-golden');lock=pathlib.Path(p)/'protocol/validity-lock.json';lock_validity(r.ledger,lock,'synthetic pre-effect decision');x=analysis(r.ledger,lock,DOCS)
   expected=2000/(2000-.9*19*80);self.assertAlmostEqual(x['primary']['CAF']['estimate'],expected);self.assertEqual(x['primary']['CAF']['n_valid'],2);self.assertEqual(x['interaction']['log']['n_valid'],0);self.assertIsNone(x['interaction']['log']['CI95']);self.assertEqual(len(x['primary']['documents']),2)
 async def test_missing_one_repeat_excludes_document_and_no_population_claim(self):
  with tempfile.TemporaryDirectory(dir=OUT) as p:
   ids={d['id'] for d in DOCS[:2]};cells=[c for c in CELLS if c['document_id'] in ids and c['N']==20 and c['schedule']=='C' and c['arm'] in ('A','D') and not(c['document_id']==DOCS[0]['id'] and c['repeat_id']==3 and c['arm']=='A')]
   r=Runner(p,minimal_manifest(),cells,'a'*64,FakeBackend(),lambda:None);await r.run('CPU-missing');lock=pathlib.Path(p)/'protocol/validity-lock.json';lock_validity(r.ledger,lock,'synthetic pre-effect decision');x=analysis(r.ledger,lock,DOCS)
   self.assertEqual(x['primary']['CAF']['n_valid'],1);self.assertIsNone(x['primary']['CAF']['CI95']);self.assertFalse(x['planned_population_estimate_claim']);excluded=next(d for d in x['primary']['excluded'] if d['document_id']==DOCS[0]['id']);self.assertEqual(excluded['missing_repeats'],[3])
 async def test_terminal_persistence_failure_consumes_attempt_without_replacement(self):
  with tempfile.TemporaryDirectory(dir=OUT) as p:
   r=Runner(p,minimal_manifest(),CELLS[:1],'a'*64,FakeBackend(),lambda:None)
   with patch('formal_core.artifact_manifest',side_effect=OSError('synthetic disk sync failure')):
    with self.assertRaises(OSError):await r.run('CPU-sync-failure')
   self.assertEqual(len(r.ledger.unfinished()),1);self.assertTrue((pathlib.Path(p)/'rounds'/r.ledger.unfinished()[0]['attempt_id']/'validity.json').exists())
   with self.assertRaises(RuntimeError):await r.run('CPU-no-resend')
   cleanup=dict(experiment_id=r.ledger.experiment,source_revision=r.ledger.source,G_service_stopped=True,own_port_closed=True,owned_GPU_idle=True)
   recover_unfinished(r.ledger,cleanup,'synthetic recovery review');self.assertFalse(r.ledger.unfinished());self.assertEqual(r.ledger.counts()['first']['attempts'],1)
if __name__=='__main__':
 result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Boundaries));save(OUT/'formal-boundary-result.json',dict(passed=result.wasSuccessful(),tests_run=result.testsRun,GPU_requests=0));raise SystemExit(0 if result.wasSuccessful() else 1)

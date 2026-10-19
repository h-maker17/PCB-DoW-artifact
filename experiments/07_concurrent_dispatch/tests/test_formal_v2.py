"""P1 approved-entry dry run and P2 recovery fault injection, CPU only."""
import pathlib,os,sys,tempfile,unittest,json,hashlib,shutil,types,copy
from unittest.mock import patch,Mock
REV=pathlib.Path(os.environ['G_FORMAL_REVISION_ROOT']);OUT=pathlib.Path(os.environ['G_FORMAL_TEST_OUTPUT']);OUT.mkdir(parents=True,exist_ok=True);sys.path.insert(0,str(REV/'code'));sys.path.insert(0,str(REV/'tests'))
import formal_core as core
from formal_core import *
from review_fixtures import reviewed_recovery as recover_unfinished
from test_formal import CELLS
class InjectedCrash(RuntimeError):pass
class Fixes(unittest.TestCase):
 def fixture(self,p):
  l=Ledger(p,'G_FORMAL_CPU_P2',[CELLS[0]],'a'*64);l.plan();info=l.start(CELLS[0],'CPU-P2-start');cleanup=dict(experiment_id=l.experiment,source_revision=l.source,G_service_stopped=True,own_port_closed=True,owned_GPU_idle=True);return l,info,cleanup
 def prove_once(self,l,info):
  l.reload();self.assertFalse(l.unfinished());self.assertEqual(sum(e['event']=='started' for e in l.events),1);self.assertEqual(sum(e['event']=='finished' for e in l.events),1);self.assertEqual(l.counts()['retry']['attempts'],0);l.selected()
 def test_P2_reentry_before_and_after_all_durable_boundaries(self):
  cases=[(name,after) for name in ('recovered-terminal.json','recovered-validity.json','artifact-manifest.json','ledger') for after in (False,True)]
  for name,after in cases:
   with self.subTest(boundary=name,after=after),tempfile.TemporaryDirectory(dir=OUT) as p:
    l,info,cleanup=self.fixture(p);original=core.save_exact;original_append=core.append
    def persist(path,obj):
     if pathlib.Path(path).name==name:
      if after:original(path,obj)
      raise InjectedCrash(name)
     return original(path,obj)
    def commit(path,obj):
     if name=='ledger' and obj['event']=='finished':
      if after:original_append(path,obj)
      raise InjectedCrash('ledger')
     return original_append(path,obj)
    with patch.object(core,'save_exact',side_effect=persist),patch.object(core,'append',side_effect=commit):
     with self.assertRaises(InjectedCrash):recover_unfinished(l,cleanup,'same reviewed recovery')
    durable={x.name:x.read_bytes() for x in pathlib.Path(p).rglob('*') if x.is_file() and x.suffix=='.json'}
    recover_unfinished(l,cleanup,'same reviewed recovery');self.prove_once(l,info);raw=l.path.read_bytes();recover_unfinished(l,cleanup,'same reviewed recovery');self.assertEqual(l.path.read_bytes(),raw)
    for name,raw in durable.items():self.assertEqual(next(pathlib.Path(p).rglob(name)).read_bytes(),raw)
 def test_P2_partial_temporary_write_never_publishes_terminal(self):
  with tempfile.TemporaryDirectory(dir=OUT) as p:
   l,info,cleanup=self.fixture(p)
   with patch.object(core.os,'fsync',side_effect=OSError('injected before publication')):
    with self.assertRaises(OSError):recover_unfinished(l,cleanup,'same reviewed recovery')
   self.assertFalse((pathlib.Path(p)/'rounds'/info['attempt_id']/'recovered-terminal.json').exists());recover_unfinished(l,cleanup,'same reviewed recovery');self.prove_once(l,info)
 def test_P2_publication_interrupted_after_link_is_reentrant(self):
  with tempfile.TemporaryDirectory(dir=OUT) as p:
   l,info,cleanup=self.fixture(p);original=core.os.link
   def link(src,dest):original(src,dest);raise InjectedCrash('published but caller interrupted')
   with patch.object(core.os,'link',side_effect=link):
    with self.assertRaises(InjectedCrash):recover_unfinished(l,cleanup,'same reviewed recovery')
   dest=pathlib.Path(p)/'rounds'/info['attempt_id']/'recovered-cause-review.json';before=dest.read_bytes();recover_unfinished(l,cleanup,'same reviewed recovery');self.prove_once(l,info);self.assertEqual(before,dest.read_bytes())
 def test_P2_conflicting_or_partial_target_is_never_overwritten(self):
  for name in ('recovered-terminal.json','recovered-validity.json','artifact-manifest.json'):
   with self.subTest(file=name),tempfile.TemporaryDirectory(dir=OUT) as p:
    l,info,cleanup=self.fixture(p);dest=pathlib.Path(p)/'rounds'/info['attempt_id'];dest.mkdir(parents=True);bad=dest/name;bad.write_bytes(b'{"conflicting_or_partial":')
    before=bad.read_bytes()
    with self.assertRaises((ValueError,json.JSONDecodeError)):recover_unfinished(l,cleanup,'same reviewed recovery')
    self.assertEqual(before,bad.read_bytes());self.assertEqual(len(l.unfinished()),1);self.assertEqual(sum(e['event']=='finished' for e in l.events),0)
 def test_P2_valid_JSON_conflicting_manifest_is_not_accepted(self):
  with tempfile.TemporaryDirectory(dir=OUT) as p:
   l,info,cleanup=self.fixture(p);dest=pathlib.Path(p)/'rounds'/info['attempt_id'];dest.mkdir(parents=True);bad=dest/'artifact-manifest.json';bad.write_bytes(b'{"files":[],"trace_prefixes":[]}');before=bad.read_bytes()
   with self.assertRaisesRegex(ValueError,'conflicts'):recover_unfinished(l,cleanup,'same reviewed recovery')
   self.assertEqual(before,bad.read_bytes());self.assertEqual(len(l.unfinished()),1)
 def test_P2_different_review_cannot_replace_persisted_terminal(self):
  with tempfile.TemporaryDirectory(dir=OUT) as p:
   l,info,cleanup=self.fixture(p);original=core.save_exact
   def stop(path,obj):
    if pathlib.Path(path).name=='recovered-validity.json':raise InjectedCrash('before validity')
    return original(path,obj)
   with patch.object(core,'save_exact',side_effect=stop):
    with self.assertRaises(InjectedCrash):recover_unfinished(l,cleanup,'review1')
   dest=pathlib.Path(p)/'rounds'/info['attempt_id']/'recovered-terminal.json';before=dest.read_bytes()
   with self.assertRaisesRegex(ValueError,'conflicts'):recover_unfinished(l,cleanup,'different review2')
   self.assertEqual(before,dest.read_bytes());recover_unfinished(l,cleanup,'review1');self.prove_once(l,info)
 def test_P1_frozen_wrapper_is_sibling_of_env_not_under_env(self):
  ref=read(REV/'reference-Llama-config.json');server=read(REV/'server-config.json');root=pathlib.PurePosixPath(ref['argv'][0]).parents[3];idx=next(i for i,x in enumerate(ref['argv']) if x.endswith('/gserver.py'));expected=str(root/'revisions'/REV.name/'code'/'gserver.py');self.assertEqual(server['argv'][idx],expected);self.assertNotIn('/G-experiment/env/revisions/',expected)
  if os.name=='posix':self.assertTrue(pathlib.Path(expected).is_file())
 def approved_fixture(self,p,bad=False):
  revision=pathlib.Path(p)/'revision';revision.mkdir();groot=pathlib.Path(p)/'groot';(groot/'checksums').mkdir(parents=True)
  for folder in ('code','tests','data'):shutil.copytree(REV/folder,revision/folder)
  for name in ('experiment-manifest.json','schedule.json','server-config.json','client-config.json','protocol.md','implementation-v5.md','token-audit.json','reference-Llama-config.json','source-seal.json'):(revision/name).write_bytes((REV/name).read_bytes())
  m=read(revision/'experiment-manifest.json');m.update(experiment_id='G_FORMAL_CPU_APPROVED_V2',execution_authorized=True,approval_reference='synthetic CPU fixture, never production authority')
  m['exclusive_runtime_window']['experiment_id']=m['experiment_id']
  ref=read(revision/'reference-Llama-config.json');idx=next(i for i,x in enumerate(ref['argv']) if x.endswith('/gserver.py'));ref['argv'][0]=str(groot/'env/g-venv/bin/python');server=read(revision/'server-config.json');server['argv']=ref['argv'][:];server['argv'][idx]=str(groot/'env/revisions/code/gserver.py') if bad else str(revision/'code/gserver.py')
  for name,obj in [('server-config.json',server),('reference-Llama-config.json',ref)]:
   (revision/name).unlink();save(revision/name,obj)
  m.update(server_config=server,server_config_sha256=sha(revision/'server-config.json'),reviewed_reference_sha256=sha(revision/'reference-Llama-config.json'));(revision/'experiment-manifest.json').unlink();save(revision/'experiment-manifest.json',m);(groot/'checksums/model-manifest.sha256').write_text(m['model_manifest_sha256'])
  a=dict(execution_authorized=True,source_bundle_sha256=verify_source(revision),manifest_sha256=sha(revision/'experiment-manifest.json'),schedule_sha256=m['schedule_sha256'],data_sha256=m['data_sha256'],protocol_sha256=m['protocol_sha256'],token_audit_sha256=m['token_audit_sha256'],review_reference='synthetic approved CPU clone, no actual execution authorization');approval=pathlib.Path(p)/'CPU-approval.json';save(approval,a);return revision,groot,m,approval,server
 def test_P1_approved_entry_reaches_exact_server_Popen_mock_only(self):
  import gformal_stage as stage
  with tempfile.TemporaryDirectory(dir=OUT) as p:
   revision,groot,m,approval,server=self.approved_fixture(p);ipc=pathlib.Path(p)/'g-formal-ipc';ipc.mkdir();sampler=types.SimpleNamespace(wait=lambda **k:0,terminate=lambda:None);calls=[]
   def popen(args,**kwargs):
    calls.append(args)
    if len(calls)==1:return sampler
    raise InjectedCrash('CPU stop at exact model Popen, no child created')
   sock=Mock();sock.connect_ex.return_value=1
   with patch.object(stage,'REV',revision),patch.object(stage,'ROOT',groot),patch.object(stage,'resource_guard',return_value={'CPU_mock':True}),patch.object(stage,'environment_snapshot',return_value={'dependency_lock_sha256':m['engine_dependency_lock_sha256']}),patch.object(stage.tempfile,'mkdtemp',return_value=str(ipc)),patch.object(stage.subprocess,'check_output',return_value=''),patch.object(stage.subprocess,'Popen',side_effect=popen),patch.object(stage.socket,'socket',return_value=sock):
    with self.assertRaisesRegex(InjectedCrash,'exact model Popen'):stage.main(approval,'formal-CPU-approved-entry')
   self.assertEqual(len(calls),2);self.assertEqual(calls[1],server['argv']);self.assertEqual(read(REV/'experiment-manifest.json')['execution_authorized'],False)
 def test_P1_old_wrong_wrapper_rejects_even_approved_before_Popen(self):
  import gformal_stage as stage
  with tempfile.TemporaryDirectory(dir=OUT) as p:
   revision,groot,m,approval,server=self.approved_fixture(p,True)
   with patch.object(stage,'REV',revision),patch.object(stage,'ROOT',groot),patch.object(stage,'resource_guard',return_value={'CPU_mock':True}),patch.object(stage.subprocess,'Popen') as proc:
    with self.assertRaisesRegex(ValueError,'engine argv differs'):stage.main(approval,'formal-CPU-bad-wrapper')
    proc.assert_not_called()
if __name__=='__main__':
 result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Fixes));save(OUT/'v2-targeted-result.json',dict(passed=result.wasSuccessful(),tests_run=result.testsRun,failures=len(result.failures),errors=len(result.errors),durable_fault_cases=8,conflict_targets=3,approved_entry_actual_subprocesses=0,GPU_requests=0));raise SystemExit(0 if result.wasSuccessful() else 1)

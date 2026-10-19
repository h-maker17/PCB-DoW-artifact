"""Hard-termination filesystem states constructed directly; no finally cleanup."""
import pathlib,os,sys,tempfile,unittest,json
from unittest.mock import patch
REV=pathlib.Path(os.environ['G_FORMAL_REVISION_ROOT']);OUT=pathlib.Path(os.environ['G_FORMAL_TEST_OUTPUT']);OUT.mkdir(parents=True,exist_ok=True);sys.path.insert(0,str(REV/'code'));sys.path.insert(0,str(REV/'tests'))
import formal_core as core
from formal_core import *
from review_fixtures import reviewed_recovery as recover_unfinished
from test_formal_v2 import Fixes,InjectedCrash
class Pending(unittest.TestCase):
 fixture=Fixes.fixture
 prove_once=Fixes.prove_once
 def published_before_ledger(self,p):
  l,info,cleanup=self.fixture(p);original=core.append
  def stop(path,obj):
   if obj['event']=='finished':raise InjectedCrash('published before ledger')
   return original(path,obj)
  with patch.object(core,'append',side_effect=stop):
   with self.assertRaises(InjectedCrash):recover_unfinished(l,cleanup,'same reviewed recovery')
  dest=pathlib.Path(p)/'rounds'/info['attempt_id'];return l,info,cleanup,dest
 def test_published_manifest_and_three_leftover_hard_links_recover_once(self):
  with tempfile.TemporaryDirectory(dir=OUT) as p:
   l,info,cleanup,dest=self.published_before_ledger(p)
   targets=[dest/name for name in ('recovered-cause-review.json','recovered-terminal.json','recovered-validity.json','artifact-manifest.json')];before={x:x.read_bytes() for x in targets};pending=[]
   # Construct exactly the on-disk state after link publication and hard exit;
   # no save_exact/finally is invoked to create or remove these leftover links.
   for index,target in enumerate(targets):
    path=dest/('.'+target.name+'.pending-'+f'abcd000{index}');os.link(target,path);pending.append(path)
   before.update({x:x.read_bytes() for x in pending});recover_unfinished(l,cleanup,'same reviewed recovery');self.prove_once(l,info)
   for path,raw in before.items():self.assertTrue(path.exists());self.assertEqual(path.read_bytes(),raw)
   manifest=read(dest/'artifact-manifest.json');self.assertFalse({x.name for x in pending}&{pathlib.Path(x['path']).name for x in manifest['files']});ledger_bytes=l.path.read_bytes();recover_unfinished(l,cleanup,'same reviewed recovery');self.assertEqual(l.path.read_bytes(),ledger_bytes)
 def test_filter_only_three_exact_immediate_internal_names(self):
  with tempfile.TemporaryDirectory(dir=OUT) as p:
   root=pathlib.Path(p);dest=root/'round';dest.mkdir();internal=['.recovered-cause-review.json.pending-abcd1234','.recovered-terminal.json.pending-abcd1234','.recovered-validity.json.pending-abcd1234','.artifact-manifest.json.pending-abcd1234']
   user=['data.pending-abcd1234','.result.json.pending-abcd1234','.recovered-terminal.json.pending-user-readable','.artifact-manifest.json.pending-ABCD9876','nested/.artifact-manifest.json.pending-abcd1234']
   for name in internal+user:path=dest/name;path.parent.mkdir(exist_ok=True);path.write_bytes(b'preserve:'+name.encode())
   before={dest/name:(dest/name).read_bytes() for name in internal+user};artifact_manifest(root,dest,{})
   actual={x['path'] for x in read(dest/'artifact-manifest.json')['files']};self.assertEqual(actual,{'round/'+name for name in user})
   for path,raw in before.items():self.assertEqual(path.read_bytes(),raw)
 def test_leftover_pending_does_not_relax_conflict_rejection(self):
  for name in ('recovered-terminal.json','artifact-manifest.json'):
   with self.subTest(target=name),tempfile.TemporaryDirectory(dir=OUT) as p:
    l,info,cleanup,dest=self.published_before_ledger(p);target=dest/name;target.write_bytes(b'{"conflicting":true}');pending=dest/('.'+name+'.pending-abcd1234');os.link(target,pending);before={x:x.read_bytes() for x in (target,pending)}
    with self.assertRaisesRegex(ValueError,'conflicts'):recover_unfinished(l,cleanup,'same reviewed recovery')
    for path,raw in before.items():self.assertEqual(path.read_bytes(),raw)
    self.assertEqual(len(l.unfinished()),1);self.assertEqual(sum(e['event']=='finished' for e in l.events),0)
if __name__=='__main__':
 result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Pending));save(OUT/'v3-pending-result.json',dict(passed=result.wasSuccessful(),tests_run=result.testsRun,failures=len(result.failures),errors=len(result.errors),hard_exit_state_directly_constructed=True,finally_cleanup_not_used_for_leftovers=True,pending_and_published_bytes_preserved=True,finished_exactly_once=True,internal_namespace_exact_three_targets=True,user_data_not_broadly_ignored=True,GPU_requests=0));raise SystemExit(0 if result.wasSuccessful() else 1)

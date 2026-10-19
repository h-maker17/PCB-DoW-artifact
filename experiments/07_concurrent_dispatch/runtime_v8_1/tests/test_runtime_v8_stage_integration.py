"""Actual frozen stage directory creation and explicit archived abort lineage."""
import pathlib,os,sys,json,tempfile,unittest,types,hashlib
from unittest.mock import patch,Mock
sys.path.insert(0,str(pathlib.Path(os.environ['G_RUNTIME_EXTENSION_REVISION'])/'code'))
import runtime_v8_entry as entry
import formal_core as core
import gformal_stage as stage
from test_formal_v2 import Fixes,InjectedCrash
from test_runtime_v8 import ExtensionEntry
EXT=pathlib.Path(os.environ['G_RUNTIME_EXTENSION_REVISION'])
class FrozenStageIntegration(unittest.TestCase):
    def fixture(self,p):
        formal,groot,m,approval,server=Fixes().approved_fixture(p)
        import shutil
        extension=pathlib.Path(p)/'extension';shutil.copytree(EXT,extension)
        em=entry.read(extension/'extension-manifest.json')
        em.update(formal_revision=formal.name,formal_manifest_sha256=core.sha(formal/'experiment-manifest.json'),formal_source_bundle_sha256=core.verify_source(formal),original_monitor_sha256=core.sha(formal/'code/gformal_monitor.py'),experiment_id=m['experiment_id'])
        (extension/'extension-manifest.json').write_text(json.dumps(em))
        a=core.read(approval);a.update(execution_extension_authorized=True,extension_manifest_sha256=core.sha(extension/'extension-manifest.json'),extension_source_sha256=entry.read(extension/'source-seal.json')['source_bundle_sha256'],human_extension_release_reference='CPU synthetic original frozen stage integration only')
        approval.write_text(json.dumps(a));return formal,groot,m,approval,server,extension
    def test_actual_frozen_stage_creates_folder_then_reaches_exact_model_Popen_mock(self):
        with tempfile.TemporaryDirectory() as p:
            formal,groot,m,approval,server,extension=self.fixture(p)
            ipc=pathlib.Path(p)/'ipc';ipc.mkdir();sampler=types.SimpleNamespace(wait=lambda **k:0,terminate=lambda:None);calls=[]
            def popen(args,**kwargs):
                calls.append(args)
                if len(calls)==1:return sampler
                raise InjectedCrash('CPU exact server Popen boundary, no child created')
            sock=Mock();sock.connect_ex.return_value=1
            with patch.object(entry,'formal_modules',return_value=(core,stage)),patch.object(stage,'REV',formal),patch.object(stage,'ROOT',groot),patch.object(stage,'resource_guard',return_value={'CPU_mock':True}),patch.object(stage,'environment_snapshot',return_value={'dependency_lock_sha256':m['engine_dependency_lock_sha256']}),patch.object(stage.tempfile,'mkdtemp',return_value=str(ipc)),patch.object(stage.subprocess,'check_output',return_value=''),patch.object(stage.subprocess,'Popen',side_effect=popen),patch.object(stage.socket,'socket',return_value=sock):
                with self.assertRaisesRegex(InjectedCrash,'exact server Popen'):entry.run(approval,'formal-CPU-stage-mkdir',extension,formal)
            self.assertEqual(len(calls),2);self.assertEqual(calls[1],server['argv'])
            folder=groot/'formal'/m['experiment_id']/'segments/formal-CPU-stage-mkdir'
            proof=core.read(folder/'execution-extension.json')
            intent=groot/'formal'/m['experiment_id']/'protocol/execution-extensions'/proof['execution_extension_source_sha256']/'segment-intents/formal-CPU-stage-mkdir.json'
            self.assertEqual(intent.read_bytes(),(folder/'execution-extension.json').read_bytes())
            self.assertEqual(core.read(folder/'execution-extension-outcome.json')['error_type'],'InjectedCrash')
    def test_actual_preflight_denial_records_audit_only_after_stage_attempt_no_Popen(self):
        with tempfile.TemporaryDirectory() as p:
            formal,groot,m,approval,server,extension=self.fixture(p)
            with patch.object(entry,'formal_modules',return_value=(core,stage)),patch.object(stage,'REV',formal),patch.object(stage,'ROOT',groot),patch.object(stage,'resource_guard',side_effect=RuntimeError('CPU preflight deny')),patch.object(stage.subprocess,'Popen') as proc:
                with self.assertRaisesRegex(RuntimeError,'preflight deny'):entry.run(approval,'formal-CPU-preflight-deny',extension,formal)
                proc.assert_not_called()
            folder=groot/'formal'/m['experiment_id']/'segments/formal-CPU-preflight-deny'
            self.assertTrue((folder/'execution-extension.json').exists());self.assertFalse((folder/'server.log').exists())
            with self.assertRaisesRegex(ValueError,'signed abort review'):entry.verify_archived_extension(groot/'formal'/m['experiment_id'],'formal-CPU-preflight-deny')

class ArchivedAbort(unittest.TestCase):
    def fixture(self):
        f=ExtensionEntry();f.setUp();self.addCleanup(f.doCleanups)
        c,s,m,l,original=f.fake();s.main=Mock(side_effect=FileExistsError('CPU predecessor abort before service'))
        with patch.object(entry,'formal_modules',return_value=(c,s)),patch.object(c,'gate',return_value=m),patch.object(c,'verify_source',return_value=entry.read(f.f/'source-seal.json')['source_bundle_sha256']),patch.object(c,'Ledger',return_value=l),patch.object(c,'read',side_effect=lambda p: [] if pathlib.Path(p).name=='schedule.json' else entry.read(p)),patch.object(c,'verify_resume'):
            with self.assertRaises(FileExistsError):entry.run(f.a,'formal-CPU-abort',f.e,f.f)
        root=s.ROOT/'formal'/m['experiment_id'];folder=root/'segments/formal-CPU-abort'
        log=root/'protocol/CPU-abort.stage.log';log.write_text('CPU synthetic source-bound predecessor trace')
        proof=dict(pre_service_abort_verified=True,effect_inspected=False,segment='formal-CPU-abort',execution_extension_source_sha256=f.seal['source_bundle_sha256'],extension_outcome_sha256=entry.sha(folder/'execution-extension-outcome.json'),segment_provenance_sha256=entry.sha(folder/'execution-extension.json'),actual_model_requests=0,new_formal_attempts=0,stage_log_path=log.relative_to(root).as_posix(),stage_log_sha256=entry.sha(log))
        p=root/'protocol/CPU-abort-review.json';p.write_text(json.dumps(proof))
        binding={'formal-CPU-abort':dict(evidence_path=p.relative_to(root).as_posix(),evidence_sha256=entry.sha(p))}
        return root,folder,p,binding
    def test_pre_service_predecessor_is_verified_only_with_signed_hash_bound_review(self):
        root,folder,p,binding=self.fixture()
        with self.assertRaisesRegex(ValueError,'signed abort review'):entry.verify_archived_extension(root,'formal-CPU-abort')
        self.assertTrue(entry.verify_archived_extension(root,'formal-CPU-abort',expected_abort_reviews=binding)['pre_service_abort_verified'])
    def test_modified_abort_review_is_rejected(self):
        root,folder,p,binding=self.fixture();p.write_text('{}')
        with self.assertRaisesRegex(ValueError,'review differs'):entry.verify_archived_extension(root,'formal-CPU-abort',expected_abort_reviews=binding)
    def test_driver_artifact_disallows_claim_of_pre_service_abort(self):
        root,folder,p,binding=self.fixture();(folder/'driver.log').write_text('CPU contradicts no-driver claim')
        with self.assertRaisesRegex(ValueError,'service/driver'):entry.verify_archived_extension(root,'formal-CPU-abort',expected_abort_reviews=binding)

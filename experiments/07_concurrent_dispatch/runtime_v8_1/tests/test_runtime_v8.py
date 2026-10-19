import pathlib,json,sys,os,unittest,tempfile,errno,shutil,types
from unittest.mock import patch,Mock
v7=pathlib.Path(os.environ['G_FORMAL_BASE_REVISION'])
ext=pathlib.Path(os.environ['G_RUNTIME_EXTENSION_REVISION'])
if 'formal_core' not in sys.modules:sys.path.insert(0,str(v7/'code'))
sys.path.insert(0,str(ext/'code'))
import runtime_v8_monitor as mon
import runtime_v8_entry as entry
def missing():return ProcessLookupError(errno.ESRCH,'CPU reaped process')
owner={'pid':100,'pgid':100,'session':100,'pid_namespace':'pid:[cpu]','starttime_ticks':10,'state':'S','PPid':1}
child=dict(owner,pid=101,starttime_ticks=11,PPid=100)
def missing():return ProcessLookupError(errno.ESRCH,'CPU reaped process')
class Races(unittest.TestCase):
    def test_exited_during_group_hint_is_absent(self):
        def hint(pid):
            if pid==101:raise missing()
            return 'S',100,100
        with patch.object(pathlib.Path,'iterdir',return_value=[pathlib.Path('/proc/101'),pathlib.Path('/proc/100')]),patch.object(mon,'proc_group_hint',side_effect=hint),patch.object(mon,'proc_identity',return_value=owner):
            self.assertEqual(mon.local_group(owner),[owner])
    def test_exited_during_full_member_identity_is_absent(self):
        def identity(pid):
            if pid==101:raise missing()
            return owner
        with patch.object(pathlib.Path,'iterdir',return_value=[pathlib.Path('/proc/101'),pathlib.Path('/proc/100')]),patch.object(mon,'proc_group_hint',return_value=('S',100,100)),patch.object(mon,'proc_identity',side_effect=identity):
            self.assertEqual(mon.local_group(owner),[owner])
    def monitor(self,folder):
        m=mon.ResourceMonitor.__new__(mon.ResourceMonitor);m.owner=owner;m.cleanup_pins={100:owner.copy(),101:child.copy()};m.folder=pathlib.Path(folder);m.trace_pins={};m.sequence=0;m.last_healthy=None;m.candidate_baseline=None
        m.m=json.loads((v7/'experiment-manifest.json').read_text(encoding='utf-8'))
        return m
    def test_reaped_pin_does_not_remove_surviving_anchor(self):
        with tempfile.TemporaryDirectory() as d:
            m=self.monitor(d)
            def identity(pid):
                if pid==101:raise missing()
                return owner
            with patch.object(mon,'proc_identity',side_effect=identity),patch.object(mon,'local_group',return_value=[owner]):self.assertEqual(m.cleanup_members(),[owner])
    def test_no_live_anchor_still_refuses_group(self):
        with tempfile.TemporaryDirectory() as d:
            m=self.monitor(d)
            with patch.object(mon,'proc_identity',side_effect=missing()),patch.object(mon,'local_group',return_value=[child]):
                with self.assertRaisesRegex(mon.MonitorPause,'Unanchored'):m.cleanup_members()
    def test_possible_member_permission_error_still_fails(self):
        with patch.object(pathlib.Path,'iterdir',return_value=[pathlib.Path('/proc/101')]),patch.object(mon,'proc_group_hint',return_value=('S',100,100)),patch.object(mon,'proc_identity',side_effect=PermissionError('CPU denied')):
            with self.assertRaises(PermissionError):mon.local_group(owner)
    def test_reused_pinned_pid_still_refuses(self):
        with tempfile.TemporaryDirectory() as d:
            m=self.monitor(d)
            with patch.object(mon,'proc_identity',return_value=dict(owner,starttime_ticks=999)):
                with self.assertRaisesRegex(mon.MonitorPause,'identity changed'):m.cleanup_members()
    def test_owner_disappearance_still_pauses_with_errno(self):
        with tempfile.TemporaryDirectory() as d:
            m=self.monitor(d)
            with patch.object(mon,'proc_identity',side_effect=missing()):
                with self.assertRaises(mon.MonitorPause):m.check('running')
            evidence=json.loads((m.folder/'resource-monitor-pause.json').read_text())
            self.assertEqual(evidence['exception_errno'],errno.ESRCH)
            self.assertTrue(evidence['exception_frames']);self.assertEqual(evidence['action'],'pause_for_review')
    def test_trace_process_disappearance_still_pauses(self):
        with tempfile.TemporaryDirectory() as d:
            m=self.monitor(d)
            gpu={'devices':[{'uuid':m.m['GPU_UUID']}],'compute_candidates':[{'pid':123}]}
            with patch.object(mon,'proc_identity',return_value=owner),patch.object(m,'cleanup_members',return_value=[owner]),patch.object(mon,'query_gpu',return_value=gpu),patch.object(m,'trace_identity',side_effect=missing()):
                with self.assertRaises(mon.MonitorPause):m.check('running')
            self.assertEqual(json.loads((m.folder/'resource-monitor-pause.json').read_text())['action'],'pause_for_review')

class ExtensionEntry(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.base=pathlib.Path(self.tmp.name);self.e=self.base/'extension';self.f=self.base/v7.name
        shutil.copytree(ext,self.e);self.f.mkdir();(self.f/'code').mkdir()
        for name in ('experiment-manifest.json','source-seal.json'):
            shutil.copy2(v7/name,self.f/name)
        shutil.copy2(v7/'code/gformal_monitor.py',self.f/'code/gformal_monitor.py')
        self.a=self.base/'approval.json';self.seal=json.loads((self.e/'source-seal.json').read_text())
        self.approval=dict(execution_extension_authorized=True,extension_manifest_sha256=entry.sha(self.e/'extension-manifest.json'),extension_source_sha256=self.seal['source_bundle_sha256'],human_extension_release_reference='CPU synthetic release, not production authority')
        self.write_approval()
    def write_approval(self):self.a.write_text(json.dumps(self.approval))
    def fake(self):
        import formal_core as core
        fm=json.loads((self.f/'experiment-manifest.json').read_text())
        stage=types.SimpleNamespace(ROOT=self.base/'G',REV=self.f,ResourceMonitor=object())
        ledger=Mock();ledger.unfinished.return_value=[]
        def main(approval,segment):
            self.assertIsNot(stage.ResourceMonitor,original)
            self.assertEqual(stage.ResourceMonitor.__module__,'approved_runtime_v8_monitor')
            folder=stage.ROOT/'formal'/fm['experiment_id']/'segments'/segment
            self.assertFalse(folder.exists(),'Wrapper must not precreate frozen stage directory')
            folder.mkdir(parents=True,exist_ok=False)
            (folder/'resource-monitor.jsonl').write_text(json.dumps({'execution_extension_source_sha256':self.seal['source_bundle_sha256']})+'\n')
            return {'synthetic_CPU_fixture':True}
        original=stage.ResourceMonitor;stage.main=Mock(side_effect=main)
        return core,stage,fm,ledger,original
    def test_exact_extension_release_and_source_required(self):
        self.approval['execution_extension_authorized']=False;self.write_approval()
        with patch.object(entry,'formal_modules') as modules:
            with self.assertRaisesRegex(ValueError,'not been released'):entry.run(self.a,'formal-CPU',self.e,self.f)
            modules.assert_not_called()
    def test_changed_extension_code_rejected_before_stage(self):
        with (self.e/'code/runtime_v8_monitor.py').open('a') as f:f.write('\n# CPU tamper\n')
        with patch.object(entry,'formal_modules') as modules:
            with self.assertRaisesRegex(ValueError,'seal differs'):entry.run(self.a,'formal-CPU',self.e,self.f)
            modules.assert_not_called()
    def test_wrong_formal_manifest_rejected(self):
        p=self.f/'experiment-manifest.json';m=json.loads(p.read_text());m['R']=2;p.write_text(json.dumps(m))
        with self.assertRaisesRegex(ValueError,'identity/scope'):entry.verify_extension(self.e,self.f,self.a)
    def test_extension_approval_cannot_replace_v7_gate(self):
        core,stage,fm,ledger,original=self.fake()
        with patch.object(entry,'formal_modules',return_value=(core,stage)),patch.object(core,'gate',side_effect=ValueError('CPU formal gate denied')):
            with self.assertRaisesRegex(ValueError,'formal gate denied'):entry.run(self.a,'formal-CPU',self.e,self.f)
        stage.main.assert_not_called();self.assertIs(stage.ResourceMonitor,original)
    def test_unfinished_history_rejected_without_stage(self):
        core,stage,fm,ledger,original=self.fake();ledger.unfinished.return_value=[{'CPU':True}]
        with patch.object(entry,'formal_modules',return_value=(core,stage)),patch.object(core,'gate',return_value=fm),patch.object(core,'verify_source',return_value=json.loads((self.f/'source-seal.json').read_text())['source_bundle_sha256']),patch.object(core,'Ledger',return_value=ledger),patch.object(core,'read',side_effect=lambda p: [] if pathlib.Path(p).name=='schedule.json' else entry.read(p)):
            with self.assertRaisesRegex(ValueError,'Unfinished attempt'):entry.run(self.a,'formal-CPU',self.e,self.f)
        stage.main.assert_not_called()
    def test_unresolved_pause_rejected_without_stage(self):
        core,stage,fm,ledger,original=self.fake()
        with patch.object(entry,'formal_modules',return_value=(core,stage)),patch.object(core,'gate',return_value=fm),patch.object(core,'verify_source',return_value=json.loads((self.f/'source-seal.json').read_text())['source_bundle_sha256']),patch.object(core,'Ledger',return_value=ledger),patch.object(core,'read',side_effect=lambda p: [] if pathlib.Path(p).name=='schedule.json' else entry.read(p)),patch.object(core,'verify_resume',side_effect=ValueError('CPU unresolved pause')):
            with self.assertRaisesRegex(ValueError,'unresolved pause'):entry.run(self.a,'formal-CPU',self.e,self.f)
        stage.main.assert_not_called()
    def run_fake(self):
        core,stage,fm,ledger,original=self.fake()
        with patch.object(entry,'formal_modules',return_value=(core,stage)),patch.object(core,'gate',return_value=fm),patch.object(core,'verify_source',return_value=json.loads((self.f/'source-seal.json').read_text())['source_bundle_sha256']),patch.object(core,'Ledger',return_value=ledger),patch.object(core,'read',side_effect=lambda p: [] if pathlib.Path(p).name=='schedule.json' else entry.read(p)),patch.object(core,'verify_resume'):
            value=entry.run(self.a,'formal-CPU-provenance',self.e,self.f)
        self.assertIs(stage.ResourceMonitor,original);self.assertTrue(value['synthetic_CPU_fixture'])
        return stage.ROOT/'formal'/fm['experiment_id']
    def test_success_archives_both_sources_without_relabelling(self):
        root=self.run_fake();proof=entry.verify_archived_extension(root,'formal-CPU-provenance')
        self.assertTrue(proof['passed'])
        self.assertEqual(proof['formal_source_bundle_sha256'],json.loads((self.f/'source-seal.json').read_text())['source_bundle_sha256'])
    def test_archived_provenance_tamper_rejected(self):
        root=self.run_fake();p=root/'segments/formal-CPU-provenance/resource-monitor.jsonl';p.write_text(json.dumps({'execution_extension_source_sha256':'bad'})+'\n')
        with self.assertRaisesRegex(ValueError,'provenance'):entry.verify_archived_extension(root,'formal-CPU-provenance')

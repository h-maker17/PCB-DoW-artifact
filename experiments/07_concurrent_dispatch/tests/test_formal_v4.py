"""Focused CPU-only exclusive-window monitoring and driver ordering tests."""
import pathlib,os,sys,tempfile,unittest,json,copy,types
from unittest.mock import patch,Mock
REV=pathlib.Path(os.environ['G_FORMAL_REVISION_ROOT']);OUT=pathlib.Path(os.environ['G_FORMAL_TEST_OUTPUT']);OUT.mkdir(parents=True,exist_ok=True)
sys.path.insert(0,str(REV/'code'));sys.path.insert(0,str(REV/'tests'))
import gformal_monitor as mon
import gformal_stage as stage
from formal_core import read,save,sha,verify_source
import test_formal_v2 as prior

def identity(pid,ppid,start,namespace='pid:[CPU-TEST]',group=100):
 return dict(pid=pid,PPid=ppid,starttime_ticks=start,pid_namespace=namespace,pgid=group,session=group,NSpid=str(pid),name='CPU-test',cgroup=['0::/'])

class MonitorTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=OUT);self.addCleanup(self.tmp.cleanup);self.folder=pathlib.Path(self.tmp.name);(self.folder/'traces').mkdir()
  self.m=read(REV/'experiment-manifest.json');self.procs={100:identity(100,50,10),200:identity(200,100,20)}
  self.pidpatch=patch.object(mon,'proc_identity',side_effect=lambda pid:copy.deepcopy(self.procs[pid]));self.pidpatch.start();self.addCleanup(self.pidpatch.stop)
  self.grouppatch=patch.object(mon,'local_group',side_effect=lambda owner:[copy.deepcopy(x) for x in self.procs.values()]);self.grouppatch.start();self.addCleanup(self.grouppatch.stop)
  self.gpu=dict(devices=[dict(uuid=self.m['GPU_UUID'],name='NVIDIA H100',memory_used='77559 MiB',utilization='0 %')],compute_candidates=[dict(pid=736035,process_name='[Not Found]',used_memory='77550 MiB',attribution='unknown',reason='No trusted mapping')],raw_gpu='CPU MOCK',raw_compute_apps='CPU MOCK')
  self.gpupatch=patch.object(mon,'query_gpu',side_effect=lambda:copy.deepcopy(self.gpu));self.gpupatch.start();self.addCleanup(self.gpupatch.stop)
  self.trace=self.folder/'traces/events-200.jsonl';self.trace.write_text(json.dumps(dict(pid=200,seq=1,kind='cache_layout'))+'\n')
  self.monitor=mon.ResourceMonitor(self.m,self.folder,100)
 def paused(self,reason):
  with self.assertRaisesRegex(mon.MonitorPause,reason):self.monitor.check('running')
  p=read(self.folder/'resource-monitor-pause.json');self.assertTrue(p['no_automatic_external_classification']);self.assertFalse(p['external_occupancy_confirmed'])
 def test_unmapped_PID_is_unknown_and_admitted_in_approved_window(self):
  row=self.monitor.check('pre_driver');self.assertEqual(row['action'],'continue_in_approved_window');self.assertEqual(row['GPU']['compute_candidates'][0]['attribution'],'unknown');self.assertFalse(row['external_occupancy_confirmed']);self.assertFalse((self.folder/'resource-monitor-pause.json').exists())
 def test_process_name_memory_or_matching_local_number_do_not_prove_owned(self):
  self.gpu['compute_candidates'][0].update(pid=200,process_name='VLLM::EngineCore',used_memory='77550 MiB')
  self.assertEqual(self.monitor.check('pre_driver')['GPU']['compute_candidates'][0]['attribution'],'unknown')
 def test_extra_compute_candidate_pauses_without_external_inference(self):
  self.monitor.check('pre_driver');self.gpu['compute_candidates'].append(dict(pid=888888,attribution='unknown'));self.paused('Additional GPU')
 def test_GPU_candidate_PID_change_pauses(self):
  self.monitor.check('pre_driver');self.gpu['compute_candidates'][0]['pid']=736036;self.paused('candidate identity changed')
 def test_GPU_UUID_change_pauses(self):
  self.gpu['devices'][0]['uuid']='another-GPU';self.paused('GPU UUID/count changed')
 def test_same_PID_reused_for_owner_pauses(self):
  self.procs[100]['starttime_ticks']=30;self.paused('Owned G service identity changed')
 def test_owner_namespace_change_pauses(self):
  self.procs[100]['pid_namespace']='pid:[other]';self.paused('Owned G service identity changed')
 def test_native_trace_process_PID_reuse_pauses(self):
  self.monitor.check('pre_driver');self.procs[200]['starttime_ticks']=21;self.paused('Pinned cleanup PID identity changed')
 def test_native_trace_namespace_change_pauses(self):
  self.procs[200]['pid_namespace']='pid:[other]';self.paused('no longer belongs')
 def test_trace_first_record_changed_pauses(self):
  self.monitor.check('pre_driver');self.trace.write_text(json.dumps(dict(pid=200,seq=1,kind='different'))+'\n');self.paused('Native trace/service process identity changed')
 def test_trace_disappearance_pauses(self):
  self.monitor.check('pre_driver');self.trace.unlink();self.paused('Native trace file disappeared')
 def test_GPU_query_failure_is_recorded_and_pauses(self):
  with patch.object(mon,'query_gpu',side_effect=OSError('query unavailable')):self.paused('query unavailable')
 def test_ready_missing_native_trace_is_concrete_pause(self):
  self.trace.unlink();self.paused('lacks GPU/native trace identity evidence')
 def test_empty_GPU_allowed_only_during_startup_before_first_candidate(self):
  self.gpu['compute_candidates']=[];self.assertEqual(self.monitor.check('starting')['action'],'continue_in_approved_window');self.paused('lacks GPU/native trace identity evidence')
 def test_missing_or_wrong_window_rejects(self):
  for change in ['exclusive_to_G','instance','experiment_id','statement_ids']:
   m=copy.deepcopy(self.m);m['exclusive_runtime_window'][change]=None
   with self.assertRaises(ValueError):mon.verify_window(m)

class DriverOrdering(unittest.TestCase):
 def simulate(self,p,fail=None,wrong_model=False):
  revision,groot,m,approval,server=prior.Fixes.approved_fixture(self,p);segment='formal-CPU-v4-order';folder=groot/'formal'/m['experiment_id']/'segments'/segment
  ipc=pathlib.Path(p)/'g-formal-ipc';ipc.mkdir();calls=[];events=[]
  class Proc:
   def __init__(self,pid,driver=False):self.pid=pid;self.returncode=None;self.driver=driver;self.polls=0;self.terminated=False
   def poll(self):
    if self.driver and self.returncode is None:
     self.polls+=1
     if self.polls>1 and fail!='running':self.returncode=0
    return self.returncode
   def terminate(self):self.terminated=True;self.returncode=-15
   def kill(self):self.returncode=-9
   def wait(self,**kwargs):
    if self.pid==99:events.append('sampler-wait')
    self.returncode=0 if self.returncode is None else self.returncode;return self.returncode
  sampler=Proc(99);service=Proc(100);driver=Proc(101,True)
  def popen(argv,**kwargs):
   calls.append(argv);events.append('Popen:'+('sampler' if len(calls)==1 else 'server' if len(calls)==2 else 'driver'))
   if len(calls)==1:return sampler
   if len(calls)==2:return service
   self.assertEqual(events[-2],'monitor:pre_driver');save(folder/'completion.json',dict(CPU_mock=True));return driver
  fake=types.SimpleNamespace(owner=identity(100,50,10))
  def check(phase):
   events.append('monitor:'+phase)
   if fail=='service_final' and phase=='post_driver':service.returncode=1
   if fail==phase:
    save(folder/'resource-monitor-pause.json',dict(CPU_mock=True,attribution='unknown',external_occupancy_confirmed=False));raise mon.MonitorPause('CPU concrete anomaly')
   if fail=='log' and phase=='pre_driver':raise OSError('CPU log persistence failed')
   return dict(action='continue_in_approved_window',attribution='unknown')
  fake.check=check;sock=Mock();sock.connect_ex.return_value=1
  def stop(proc,monitor):
   events.append('stop-own');self.assertIs(proc,service)
   if fail=='cleanup':raise OSError('CPU cleanup failure')
   service.returncode=-15;return dict(all_owned_members_gone=True)
  resp=Mock();resp.status=200;resp.__enter__=Mock(return_value=resp);resp.__exit__=Mock(return_value=False)
  with patch.object(stage,'REV',revision),patch.object(stage,'ROOT',groot),patch.object(stage,'resource_guard',return_value={'CPU_mock':True}),patch.object(stage,'ResourceMonitor',return_value=fake),patch.object(stage,'environment_snapshot',return_value={'dependency_lock_sha256':m['engine_dependency_lock_sha256']}),patch.object(stage.tempfile,'mkdtemp',return_value=str(ipc)),patch.object(stage.subprocess,'Popen',side_effect=popen),patch.object(stage.subprocess,'check_output',return_value=''),patch.object(stage.socket,'socket',return_value=sock),patch.object(stage.urllib.request,'urlopen',return_value=resp),patch.object(stage.json,'load',return_value={'data':[{'id':'other' if wrong_model else 'G-llama'}]}),patch.object(stage,'stop_owned',side_effect=stop),patch.object(stage.time,'sleep'):
   if fail or wrong_model:
    with self.assertRaises((RuntimeError,OSError,ValueError)):stage.main(approval,segment)
   else:stage.main(approval,segment)
  if fail=='cleanup':
   self.assertIsNone(service.returncode);self.assertFalse(read(folder/'cleanup-cost.json')['G_service_stopped'])
  else:self.assertEqual(service.returncode,-15);self.assertEqual(read(folder/'cleanup-cost.json')['G_service_stopped'],True)
  return calls,events,folder,driver
 def test_unknown_passes_checks_before_driver_and_monitoring_continues(self):
  with tempfile.TemporaryDirectory(dir=OUT) as p:
   calls,events,folder,driver=self.simulate(p);self.assertEqual(len(calls),3);self.assertIn('monitor:running',events);self.assertFalse((folder/'stage-review-pause.json').exists())
 def test_pre_driver_anomaly_creates_no_driver(self):
  with tempfile.TemporaryDirectory(dir=OUT) as p:
   calls,events,folder,driver=self.simulate(p,fail='pre_driver');self.assertEqual(len(calls),2);self.assertFalse((folder/'driver.log').exists());self.assertTrue(read(folder/'stage-review-pause.json')['no_automatic_resume'])
 def test_log_failure_before_driver_creates_no_driver(self):
  with tempfile.TemporaryDirectory(dir=OUT) as p:
   calls,events,folder,driver=self.simulate(p,fail='log');self.assertEqual(len(calls),2)
 def test_wrong_service_rejects_before_driver(self):
  with tempfile.TemporaryDirectory(dir=OUT) as p:
   calls,events,folder,driver=self.simulate(p,wrong_model=True);self.assertEqual(len(calls),2)
 def test_runtime_anomaly_stops_only_own_driver_and_service_without_restart(self):
  with tempfile.TemporaryDirectory(dir=OUT) as p:
   calls,events,folder,driver=self.simulate(p,fail='running');self.assertEqual(len(calls),3);self.assertTrue(driver.terminated);self.assertTrue(read(folder/'stage-review-pause.json')['external_occupancy_not_inferred'])

if __name__=='__main__':unittest.main(verbosity=2)

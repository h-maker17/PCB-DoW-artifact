"""CPU counterexamples for each v4 blocker; actual model/signals forbidden."""
import asyncio,copy,hashlib,json,os,pathlib,sys,tempfile,types,unittest
from unittest.mock import patch,Mock
REV=pathlib.Path(os.environ['G_FORMAL_REVISION_ROOT']);OUT=pathlib.Path(os.environ['G_FORMAL_TEST_OUTPUT']);sys.path[:0]=[str(REV/'code'),str(REV/'tests')]
import formal_core as core
import gformal_adjudication as adj
import gformal_stage as stage
import gformal_monitor as mon
from test_formal import FakeBackend,CELLS,DOCS
from test_formal_v4 import identity
import test_formal_v4 as v4
from review_fixtures import cause_review
from gformal_archive import archive,rebuild

class Adjudication(unittest.IsolatedAsyncioTestCase):
 def temp(self):return tempfile.TemporaryDirectory(dir=OUT)
 async def fixture(self,p):
  m=core.read(REV/'experiment-manifest.json');source=core.verify_source(REV)
  r=core.Runner(pathlib.Path(p)/'raw',m,CELLS,source,FakeBackend(),lambda:None)
  ids={d['id'] for d in DOCS[:2]};r.cells=[c for c in CELLS if c['document_id'] in ids and c['N']==20 and c['schedule']=='C' and c['arm'] in ('A','D')]
  await r.run('formal-CPU-resource');r.cells=CELLS
  folder=r.root/'segments/formal-CPU-resource'
  core.save(folder/'resource-monitor-pause.json',dict(reason='CPU additional candidate',external_occupancy_confirmed=False))
  core.save(folder/'resource-monitor.jsonl',dict(synthetic=True))
  core.save(folder/'cleanup-cost.json',dict(synthetic=True,G_service_stopped=True))
  return r
 def decisions(self,r,reason='retain'):
  return {e['attempt_id']:dict(reason=reason,evidence_reference='CPU effect-blind preserved record',external_occupancy_confirmed=reason=='external_resource_conflict') for e in r.ledger.events if e['event']=='finished'}
 def publish(self,r,d=None):return adj.publish(r.ledger,r.root/'protocol/resource-adjudications/review1.json',self.decisions(r) if d is None else d,'CPU effect-blind adjudication')
 async def test_unresolved_pause_blocks_selection_lock_analysis_archive_resume(self):
  with self.temp() as p:
   r=await self.fixture(p)
   for action in (r.ledger.selected,lambda:core.lock_validity(r.ledger,r.root/'protocol/lock.json','generic'),lambda:core.analysis(r.ledger,r.root/'protocol/missing-lock.json',DOCS),lambda:archive(r.root,REV,pathlib.Path(p)/'raw.zip'),lambda:adj.verify_resume(r.ledger,dict(ledger_sha256=core.sha(r.ledger.path),review_reference='ledger only'))):
    with self.assertRaises((ValueError,FileNotFoundError)):action()
   with self.assertRaisesRegex(ValueError,'Unresolved'):await r.run('formal-CPU-restart')
 async def test_retain_unknown_keeps_all_pairs_and_rebuilds_exactly(self):
  with self.temp() as p:
   r=await self.fixture(p);raw=core.sha(r.ledger.path);self.publish(r)
   lock=r.root/'protocol/validity-lock.json';core.lock_validity(r.ledger,lock,'CPU before effects');x=core.analysis(r.ledger,lock,DOCS)
   self.assertEqual(x['primary']['CAF']['n_valid'],2);self.assertEqual(core.sha(r.ledger.path),raw)
   z=pathlib.Path(p)/'frozen.zip';archive(r.root,REV,z);got=rebuild(z,pathlib.Path(p)/'rebuilt');self.assertEqual(got['primary'],x['primary']);self.assertEqual(got['counts'],x['counts'])
 async def test_confirmed_external_excludes_one_pair_and_requires_bound_single_retry(self):
  with self.temp() as p:
   r=await self.fixture(p);ends=[e for e in r.ledger.events if e['event']=='finished'];e=ends[0];before={x:core.sha(x) for x in (r.ledger.path,r.root/e['result_path'],r.root/e['validity_path'])}
   decisions=self.decisions(r);decisions[e['attempt_id']].update(reason='external_resource_conflict',external_occupancy_confirmed=True);s=self.publish(r,decisions)
   rows,verdicts=r.ledger.selected();v=next(v for v in verdicts if v['attempt_id']==e['attempt_id']);self.assertFalse(v['cost_eligible']);self.assertTrue(v['raw_cost_eligible'])
   lock=r.root/'protocol/validity-lock.json';core.lock_validity(r.ledger,lock,'CPU before effect');x=core.analysis(r.ledger,lock,DOCS);self.assertEqual(x['primary']['CAF']['n_valid'],1);self.assertEqual(x['counts']['first']['invalid_or_failed'],1)
   from gformal_reports import reports
   table=reports(r.ledger,rows,verdicts,DOCS)['per_cell_plan_failure_raw_metrics'];self.assertEqual(sum(t['first']['reasons'].get('external_resource_conflict',0) for t in table),1)
   self.assertEqual(before,{p:core.sha(p) for p in before})
   cell=r.ledger.cells[e['logical_id']];d=dict(approved=True,reason=v['reason'],retry_of=e['attempt_id'],source_revision=r.source,review_reference='CPU exact retry',result_sha256=e['result_sha256'])
   with self.assertRaisesRegex(ValueError,'adjudication'):r.ledger.start(cell,'formal-CPU-retry',d)
   d.update(adjudication_sha256=v['adjudication_sha256'],resource_state_sha256=s['binding_sha256']);second=r.ledger.start(cell,'formal-CPU-retry',d);self.assertEqual(second['attempt_index'],2)
   # The immutable original prefix still verifies after append.
   adj.state(r.ledger);r.ledger.reload()
   with self.assertRaises(ValueError):r.ledger.start(cell,'formal-CPU-third',d)
 async def test_incomplete_and_unconfirmed_adjudication_rejected(self):
  with self.temp() as p:
   r=await self.fixture(p);d=self.decisions(r);d.pop(next(iter(d)))
   with self.assertRaisesRegex(ValueError,'Every'):self.publish(r,d)
   d=self.decisions(r);d[next(iter(d))].update(reason='external_resource_conflict',external_occupancy_confirmed=False)
   with self.assertRaisesRegex(ValueError,'Unknown attribution'):self.publish(r,d)
 async def test_changed_pause_or_monitor_invalidates_review_and_resume(self):
  for name in ('resource-monitor-pause.json','resource-monitor.jsonl'):
   with self.subTest(evidence=name),self.temp() as p:
    r=await self.fixture(p);s=self.publish(r);review=dict(ledger_sha256=core.sha(r.ledger.path),review_reference='CPU resume',resource_state_sha256=s['binding_sha256']);adj.verify_resume(r.ledger,review)
    path=r.root/'segments/formal-CPU-resource'/name;path.write_bytes(path.read_bytes()+b' ')
    with self.assertRaisesRegex(ValueError,'evidence changed'):adj.verify_resume(r.ledger,review)
 async def test_later_pause_or_final_evidence_invalidates_existing_lock(self):
  for name in ('stage-review-pause.json','resource-final-check.json'):
   with self.subTest(file=name),self.temp() as p:
    r=await self.fixture(p);self.publish(r);lock=r.root/'protocol/validity-lock.json';core.lock_validity(r.ledger,lock,'CPU preeffect')
    core.save(r.root/'segments/formal-CPU-later'/name,dict(passed=False,synthetic=True))
    with self.assertRaises(ValueError):core.analysis(r.ledger,lock,DOCS)
 async def test_handcrafted_review_cannot_omit_cleanup_evidence(self):
  with self.temp() as p:
   r=await self.fixture(p);self.publish(r);path=r.root/'protocol/resource-adjudications/review1.json';record=core.read(path);record['evidence_files'].pop('segments/formal-CPU-resource/cleanup-cost.json');path.write_text(json.dumps(record),encoding='utf-8')
   with self.assertRaisesRegex(ValueError,'all preserved'):r.ledger.selected()

class Recovery(unittest.TestCase):
 def fixture(self,p):
  l=core.Ledger(p,'G_FORMAL_CPU_RECOVERY',CELLS[:2],'a'*64);l.plan();info=l.start(CELLS[0],'formal-CPU-interrupted')
  clean=dict(experiment_id=l.experiment,source_revision=l.source,G_service_stopped=True,own_port_closed=True,owned_GPU_idle=True)
  return l,info,clean
 def test_unknown_without_review_stays_unfinished(self):
  with tempfile.TemporaryDirectory(dir=OUT) as p:
   l,i,c=self.fixture(p);before=l.path.read_bytes()
   with self.assertRaisesRegex(ValueError,'cause review'):core.recover_unfinished(l,c,'generic')
   self.assertEqual(before,l.path.read_bytes());self.assertEqual(len(l.unfinished()),1)
 def test_capacity_timeout_engine_preserve_unknown_sends_and_deny_retry(self):
  for reason in core.CAPACITY:
   with self.subTest(reason=reason),tempfile.TemporaryDirectory(dir=OUT) as p:
    l,i,c=self.fixture(p);review=cause_review(l,i,reason);core.recover_unfinished(l,c,'CPU cause',{i['attempt_id']:review});e=next(e for e in l.events if e['event']=='finished');self.assertEqual(e['reason'],reason);self.assertFalse(e['sent_count_known']);self.assertIsNone(e['sent_requests'])
    d=dict(approved=True,reason=reason,retry_of=i['attempt_id'],source_revision=l.source,review_reference='CPU',result_sha256=e['result_sha256'])
    with self.assertRaisesRegex(ValueError,'condition-related'):l.start(CELLS[0],'formal-CPU-retry',d)
 def test_known_capacity_evidence_cannot_be_labeled_log_corruption(self):
  with tempfile.TemporaryDirectory(dir=OUT) as p:
   l,i,c=self.fixture(p);r=cause_review(l,i,'capacity_failure');r['reason']='log_corruption'
   with self.assertRaisesRegex(ValueError,'Condition-related'):core.recover_unfinished(l,c,'CPU',{i['attempt_id']:r})
 def test_technical_cause_allows_only_reviewed_retry_and_reentry(self):
  with tempfile.TemporaryDirectory(dir=OUT) as p:
   l,i,c=self.fixture(p);r=cause_review(l,i);core.recover_unfinished(l,c,'CPU',{i['attempt_id']:r});raw=l.path.read_bytes();core.recover_unfinished(l,c,'CPU',{i['attempt_id']:r});self.assertEqual(raw,l.path.read_bytes());e=next(e for e in l.events if e['event']=='finished')
   with self.assertRaises(ValueError):l.start(CELLS[0],'formal-CPU-retry')
   d=dict(approved=True,reason='log_corruption',retry_of=i['attempt_id'],source_revision=l.source,review_reference='CPU',result_sha256=e['result_sha256']);self.assertEqual(l.start(CELLS[0],'formal-CPU-retry',d)['attempt_index'],2)
 def test_two_unfinished_use_same_prefix_without_hash_drift(self):
  with tempfile.TemporaryDirectory(dir=OUT) as p:
   l,i,c=self.fixture(p);j=l.start(CELLS[1],'formal-CPU-interrupted');reviews={x['attempt_id']:cause_review(l,x) for x in (i,j)};core.recover_unfinished(l,c,'CPU',reviews);self.assertFalse(l.unfinished());self.assertEqual(l.counts()['first']['attempts'],2)
 def test_changed_evidence_on_durable_terminal_reentry_rejected(self):
  with tempfile.TemporaryDirectory(dir=OUT) as p:
   l,i,c=self.fixture(p);review=cause_review(l,i);original=core.save_exact
   def crash(path,obj):
    if pathlib.Path(path).name=='recovered-validity.json':raise OSError('CPU interrupted publication')
    return original(path,obj)
   with patch.object(core,'save_exact',side_effect=crash):
    with self.assertRaises(OSError):core.recover_unfinished(l,c,'CPU',{i['attempt_id']:review})
   path=l.root/next(iter(review['evidence_files']));path.write_bytes(path.read_bytes()+b' ')
   with self.assertRaisesRegex(ValueError,'evidence changed'):core.recover_unfinished(l,c,'CPU',{i['attempt_id']:review})
 def test_cleanup_failure_blocks_recovery(self):
  with tempfile.TemporaryDirectory(dir=OUT) as p:
   l,i,c=self.fixture(p);c['cleanup_errors']=[dict(stage='service')]
   with self.assertRaisesRegex(ValueError,'cleanup'):core.recover_unfinished(l,c,'CPU',{i['attempt_id']:cause_review(l,i)})

class Cleanup(unittest.TestCase):
 def fixture(self,p):
  folder=pathlib.Path(p);(folder/'traces').mkdir();procs={100:identity(100,50,10),200:identity(200,100,20)}
  def identity_read(pid):
   if pid not in procs:raise FileNotFoundError(str(pid))
   return copy.deepcopy(procs[pid])
  self.enterContext(patch.object(mon,'proc_identity',side_effect=identity_read));self.enterContext(patch.object(mon,'local_group',side_effect=lambda owner:[copy.deepcopy(x) for x in procs.values() if x['pgid']==100 and x['session']==100]))
  self.enterContext(patch.object(mon.os,'pidfd_open',side_effect=lambda pid,flags:pid+1000,create=True));self.enterContext(patch.object(mon.os,'close'))
  self.enterContext(patch.object(mon.signal,'pidfd_send_signal',side_effect=AssertionError('Unmocked CPU signal forbidden'),create=True))
  monitor=mon.ResourceMonitor(core.read(REV/'experiment-manifest.json'),folder,100);monitor.cleanup_members();proc=Mock(pid=100);proc.poll.return_value=1;return procs,monitor,proc
 def test_dead_leader_live_worker_is_signalled_and_verified_gone(self):
  with tempfile.TemporaryDirectory(dir=OUT) as p:
   procs,monitor,proc=self.fixture(p);procs.pop(100);calls=[]
   def kill(fd,sig):pid=fd-1000;calls.append(pid);procs.pop(pid)
   with patch.object(mon.signal,'pidfd_send_signal',side_effect=kill):result=stage.stop_owned(proc,monitor)
   self.assertEqual(calls,[200]);self.assertTrue(result['all_owned_members_gone'])
 def test_all_gone_cleanup_idempotent_without_signals(self):
  with tempfile.TemporaryDirectory(dir=OUT) as p:
   procs,monitor,proc=self.fixture(p);procs.clear()
   with patch.object(mon.signal,'pidfd_send_signal') as kill:stage.stop_owned(proc,monitor);stage.stop_owned(proc,monitor);kill.assert_not_called()
 def test_reused_PID_or_namespace_has_no_signal(self):
  for field,value in (('starttime_ticks',999),('pid_namespace','pid:[other]'),('pgid',777)):
   with self.subTest(field=field),tempfile.TemporaryDirectory(dir=OUT) as p:
    procs,monitor,proc=self.fixture(p);procs.pop(100);procs[200][field]=value
    with patch.object(mon.signal,'pidfd_send_signal') as kill:
     with self.assertRaises(mon.MonitorPause):stage.stop_owned(proc,monitor)
     kill.assert_not_called()
 def test_unanchored_reused_group_and_unrelated_group_not_signalled(self):
  with tempfile.TemporaryDirectory(dir=OUT) as p:
   procs,monitor,proc=self.fixture(p);procs.clear();procs[300]=identity(300,1,300)
   with patch.object(mon.signal,'pidfd_send_signal') as kill:
    with self.assertRaisesRegex(mon.MonitorPause,'Unanchored'):stage.stop_owned(proc,monitor)
    kill.assert_not_called()
 def test_identity_change_between_enumeration_and_signal_rejected(self):
  with tempfile.TemporaryDirectory(dir=OUT) as p:
   procs,monitor,proc=self.fixture(p);procs.pop(100);original=monitor.cleanup_members
   def changed():rows=original();procs[200]['starttime_ticks']=999;return rows
   with patch.object(monitor,'cleanup_members',side_effect=changed),patch.object(mon.signal,'pidfd_send_signal') as kill:
    with self.assertRaisesRegex(mon.MonitorPause,'before signal'):monitor.signal_owned(15)
    kill.assert_not_called()
 def test_group_reuse_during_enumeration_is_unanchored_and_never_signalled(self):
  with tempfile.TemporaryDirectory(dir=OUT) as p:
   procs,monitor,proc=self.fixture(p)
   def reused(owner):procs.clear();procs[300]=identity(300,1,999);return [copy.deepcopy(procs[300])]
   with patch.object(mon,'local_group',side_effect=reused),patch.object(mon.signal,'pidfd_send_signal') as send:
    with self.assertRaisesRegex(mon.MonitorPause,'anchor disappeared'):stage.stop_owned(proc,monitor)
    send.assert_not_called()
 def test_PID_reused_when_handle_opened_fails_identity_recheck(self):
  with tempfile.TemporaryDirectory(dir=OUT) as p:
   procs,monitor,proc=self.fixture(p);procs.pop(100)
   def opened(pid,flags):procs[pid]['starttime_ticks']=999;return pid+1000
   with patch.object(mon.os,'pidfd_open',side_effect=opened),patch.object(mon.signal,'pidfd_send_signal') as send:
    with self.assertRaisesRegex(mon.MonitorPause,'before signal'):monitor.signal_owned(15)
    send.assert_not_called()

class FinalCheck(unittest.TestCase):
 simulate=v4.DriverOrdering.simulate
 def test_final_interval_anomaly_prevents_completion(self):
  with tempfile.TemporaryDirectory(dir=OUT) as p:
   calls,events,folder,driver=self.simulate(p,fail='post_driver');self.assertIn('monitor:post_driver',events);self.assertFalse(core.read(folder/'resource-final-check.json')['passed']);self.assertFalse((folder/'stage-completed.json').exists());self.assertTrue((folder/'stage-review-pause.json').exists())
 def test_normal_completion_binds_final_check(self):
  with tempfile.TemporaryDirectory(dir=OUT) as p:
   calls,events,folder,driver=self.simulate(p);self.assertTrue(core.read(folder/'resource-final-check.json')['passed']);self.assertEqual(core.read(folder/'stage-completed.json')['final_check_sha256'],core.sha(folder/'resource-final-check.json'))
 def test_service_exits_at_final_check_prevents_completion(self):
  with tempfile.TemporaryDirectory(dir=OUT) as p:
   calls,events,folder,driver=self.simulate(p,fail='service_final');self.assertFalse(core.read(folder/'resource-final-check.json')['passed']);self.assertFalse((folder/'stage-completed.json').exists())
 def test_cleanup_failure_still_stops_sampler_and_preserves_pause(self):
  with tempfile.TemporaryDirectory(dir=OUT) as p:
   calls,events,folder,driver=self.simulate(p,fail='cleanup');self.assertIn('sampler-wait',events);self.assertTrue(core.read(folder/'cleanup-cost.json')['cleanup_errors']);self.assertTrue((folder/'cleanup-review-pause.json').exists())
 def test_completed_stage_still_requires_resource_resume_review(self):
  import test_formal_v2 as prior
  with tempfile.TemporaryDirectory(dir=OUT) as p:
   rev,groot,m,approval,server=prior.Fixes.approved_fixture(self,p);ledger=Mock();ledger.unfinished.return_value=[];ledger.counts.return_value={'unattempted_original_rounds':0}
   with patch.object(stage,'REV',rev),patch.object(stage,'ROOT',groot),patch.object(stage,'Ledger',return_value=ledger),patch.object(stage,'verify_resume',side_effect=ValueError('CPU unresolved resource pause')) as resume,patch.object(stage.subprocess,'Popen') as popen:
    with self.assertRaisesRegex(ValueError,'unresolved resource'):stage.main(approval,'formal-CPU-noop-paused')
    resume.assert_called_once();popen.assert_not_called()

if __name__=='__main__':unittest.main(verbosity=2)

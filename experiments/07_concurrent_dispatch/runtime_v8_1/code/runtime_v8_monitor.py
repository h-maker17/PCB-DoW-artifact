"""G application monitoring inside an approved exclusive instance window.

NVML PID attribution stays unknown without a trusted namespace mapping. This
module never signals a GPU-reported PID or changes platform isolation.
"""
import pathlib, os, json, subprocess, hashlib, time, signal, traceback
from formal_core import append, save, utc

INSTANCE='<instance-id>'
GPU_UUID='GPU-15654ab8-354e-54e9-17ee-08dd799f7027'
POLICY={
 'admission':'user_approved_exclusive_instance_window',
 'unmapped_PID_attribution':'unknown',
 'unknown_alone_is_failure':False,
 'expected_compute_candidates_for_single_engine':1,
 'monitor_period_seconds':1,
 'concrete_anomalies':'pause_for_review_without_automatic_external_classification',
 'confirmed_external_failure_rule':'existing_external_resource_conflict',
 'cleanup':'own_G_process_group_only',
 'platform_isolation_guarantee':False,
}

EXECUTION_EXTENSION_SOURCE = None

class MonitorPause(RuntimeError):pass

def verify_window(m):
 window=m.get('exclusive_runtime_window',{})
 if (m.get('host')!=INSTANCE or m.get('GPU_UUID')!=GPU_UUID or
     window.get('instance')!=INSTANCE or window.get('GPU_UUID')!=GPU_UUID or
     window.get('experiment_id')!=m['experiment_id'] or
     window.get('exclusive_to_G') is not True or
     window.get('scope')!='this_G_runtime_window' or
     window.get('statement_ids')!=['human-new-instance-exclusive-20261012'] or
     m.get('resource_monitor_policy')!=POLICY):
  raise ValueError('Frozen exclusive G window/monitoring declaration missing or different')
 return window

def query_gpu():
 def run(fields):
  return subprocess.check_output(['nvidia-smi',fields,'--format=csv,noheader'],text=True,timeout=10).strip()
 gpu=run('--query-gpu=uuid,name,memory.used,utilization.gpu')
 apps=run('--query-compute-apps=pid,process_name,used_memory')
 devices=[dict(zip(('uuid','name','memory_used','utilization'),[x.strip() for x in line.split(',')])) for line in gpu.splitlines()]
 candidates=[]
 for line in apps.splitlines():
  x=[v.strip() for v in line.split(',')]
  if len(x)!=3:raise ValueError('Malformed GPU compute process record')
  candidates.append(dict(pid=int(x[0]),process_name=x[1],used_memory=x[2],attribution='unknown',reason='No trusted NVML-to-local PID namespace mapping'))
 if len({x['pid'] for x in candidates})!=len(candidates):raise ValueError('Duplicate GPU compute PID')
 return dict(devices=devices,compute_candidates=candidates,raw_gpu=gpu,raw_compute_apps=apps)

def proc_identity(pid):
 path=pathlib.Path('/proc',str(pid));stat=(path/'stat').read_text();tail=stat[stat.rfind(')')+2:].split()
 fields={x.split(':',1)[0]:x.split(':',1)[1].strip() for x in (path/'status').read_text().splitlines() if x.split(':',1)[0] in ('Name','Pid','PPid','NSpid')}
 namespace=os.readlink(path/'ns/pid');cgroup=(path/'cgroup').read_text().splitlines()
 again=(path/'stat').read_text();check=again[again.rfind(')')+2:].split()
 if (int(check[19]),int(check[2]),int(check[3]),os.readlink(path/'ns/pid'))!=(int(tail[19]),int(tail[2]),int(tail[3]),namespace):raise MonitorPause('Process changed during identity read')
 return dict(pid=int(pid),starttime_ticks=int(tail[19]),pid_namespace=namespace,pgid=int(tail[2]),session=int(tail[3]),PPid=int(fields['PPid']),NSpid=fields.get('NSpid'),name=fields['Name'],cgroup=cgroup,state=tail[0])

def identity_key(row):return (row['pid'],row['starttime_ticks'],row['pid_namespace'])

def proc_group_hint(pid):
 raw=pathlib.Path('/proc',str(pid),'stat').read_text();tail=raw[raw.rfind(')')+2:].split()
 return tail[0],int(tail[2]),int(tail[3])

def local_group(owner):
 """Enumerate local /proc only. NVML candidates never establish ownership."""
 rows=[]
 for p in pathlib.Path('/proc').iterdir():
  if not p.name.isdigit():continue
  try:
   # Unrelated container init processes can deny namespace reads. Only a
   # stat-confirmed member of this dedicated group/session needs full identity.
   # Any error for a possible member still fails closed; no blanket skip.
   state,pgid,session=proc_group_hint(int(p.name))
   if state=='Z' or (pgid,session)!=(owner['pgid'],owner['session']):continue
   row=proc_identity(int(p.name))
  except (FileNotFoundError,ProcessLookupError):continue
  if row.get('state')!='Z' and (row['pid_namespace'],row['pgid'],row['session'])==(owner['pid_namespace'],owner['pgid'],owner['session']):rows.append(row)
 return rows

class ResourceMonitor:
 def __init__(self,m,folder,owned_server_pid):
  verify_window(m);self.m=m;self.folder=pathlib.Path(folder);self.owner=proc_identity(owned_server_pid)
  if (self.owner['pgid'],self.owner['session'])!=(owned_server_pid,owned_server_pid):raise MonitorPause('Owned service is not its dedicated new process group/session')
  self.candidate_baseline=None;self.trace_pins={};self.sequence=0;self.last_healthy=None
  self.cleanup_pins={self.owner['pid']:self.owner}

 def cleanup_members(self):
  """A live pinned leader/worker anchors the original dedicated session.

  If no anchor survives, an occupied group is ambiguous and blocks cleanup.
  A reused pinned PID never receives a signal. Survivors are pinned before use.
  """
  anchors=[]
  for pid,pin in self.cleanup_pins.items():
   try:row=proc_identity(pid)
   except (FileNotFoundError,ProcessLookupError):continue
   if identity_key(row)!=identity_key(pin) or (row['pgid'],row['session'])!=(pin['pgid'],pin['session']):
    raise MonitorPause('Pinned cleanup PID identity changed; no signal')
   anchors.append(row)
  members=local_group(self.owner)
  if members and not anchors:raise MonitorPause('Unanchored remaining service group; no signal')
  if members:
   member_keys={identity_key(row) for row in members};anchored=False
   for anchor in anchors:
    if identity_key(anchor) not in member_keys:continue
    try:current=proc_identity(anchor['pid'])
    except (FileNotFoundError,ProcessLookupError):continue
    if identity_key(current)==identity_key(anchor) and (current['pgid'],current['session'])==(self.owner['pgid'],self.owner['session']):anchored=True;break
   if not anchored:raise MonitorPause('Service group anchor disappeared during enumeration; no signal')
  for row in members:
   if row['starttime_ticks']<self.owner['starttime_ticks']:raise MonitorPause('Service session predates pinned owner')
   pin=self.cleanup_pins.get(row['pid'])
   if pin and identity_key(pin)!=identity_key(row):raise MonitorPause('Cleanup PID reused')
   self.cleanup_pins[row['pid']]=row
  return members

 def signal_owned(self,sig):
  members=self.cleanup_members();signalled=[]
  for pin in members:
   # Pin a kernel process handle before rechecking identity. PID reuse cannot
   # retarget this signal to a different process after the check.
   if not hasattr(os,'pidfd_open') or not hasattr(signal,'pidfd_send_signal'):raise MonitorPause('Linux pidfd signalling unavailable; no unsafe numeric fallback')
   try:fd=os.pidfd_open(pin['pid'],0)
   except ProcessLookupError:continue
   try:
    try:row=proc_identity(pin['pid'])
    except (FileNotFoundError,ProcessLookupError):continue
    if identity_key(row)!=identity_key(pin) or (row['pgid'],row['session'])!=(pin['pgid'],pin['session']):raise MonitorPause('Cleanup identity changed before signal')
    append(self.folder/'cleanup-signals.jsonl',dict(action='before_signal',identity=row,signal=int(sig),utc=utc(),pidfd_bound=True))
    try:signal.pidfd_send_signal(fd,sig)
    except ProcessLookupError:continue
    signalled.append(row)
    append(self.folder/'cleanup-signals.jsonl',dict(action='signal_sent',identity=row,signal=int(sig),utc=utc(),pidfd_bound=True))
   finally:os.close(fd)
  return signalled

 def trace_identity(self,owner):
  records=[];paths={p.name:p for p in (self.folder/'traces').glob('events-*.jsonl')}
  if set(self.trace_pins)-set(paths):raise MonitorPause('Native trace file disappeared')
  for name,p in sorted(paths.items()):
   if p.is_symlink():raise MonitorPause('Native trace became a symlink')
   with p.open('rb') as f:first=f.readline(1024*1024)
   if not first:continue
   if not first.endswith(b'\n'):raise MonitorPause('Native trace first record incomplete')
   row=json.loads(first);pid=row['pid']
   if type(pid) is not int or name!=f'events-{pid}.jsonl':raise MonitorPause('Native trace PID binding differs')
   current=proc_identity(pid);node=current;seen=set()
   for _ in range(64):
    if identity_key(node)==identity_key(owner):break
    if node['pid']<=1 or node['pid'] in seen or node['pid_namespace']!=owner['pid_namespace']:raise MonitorPause('Native trace no longer belongs to this local G service')
    seen.add(node['pid']);parent=proc_identity(node['PPid'])
    if parent['starttime_ticks']>node['starttime_ticks']:raise MonitorPause('Local trace ancestry changed or PID reused')
    node=parent
   else:raise MonitorPause('Local trace ancestry cannot be verified')
   st=p.stat();pin=dict(identity=list(identity_key(current)),device=st.st_dev,inode=st.st_ino,first_record_sha256=hashlib.sha256(first).hexdigest())
   if name in self.trace_pins and self.trace_pins[name]!=pin:raise MonitorPause('Native trace/service process identity changed')
   self.trace_pins[name]=pin;records.append(dict(path=name,bytes=st.st_size,pin=pin,local_identity=current,first_record_kind=row.get('kind')))
  return records

 def check(self,phase):
  self.sequence+=1;row=dict(execution_extension_source_sha256=EXECUTION_EXTENSION_SOURCE,utc=utc(),monotonic=time.monotonic(),sequence=self.sequence,phase=phase,admission='user_approved_exclusive_instance_window',PID_mapping_status='unverified',external_occupancy_confirmed=False,previous_healthy_checkpoint=self.last_healthy)
  reason=None
  try:
   verify_window(self.m);owner=proc_identity(self.owner['pid']);row['owned_service']=owner
   if identity_key(owner)!=identity_key(self.owner) or (owner['pgid'],owner['session'])!=(self.owner['pgid'],self.owner['session']):raise MonitorPause('Owned G service identity changed')
   row['owned_group']=self.cleanup_members()
   gpu=query_gpu();row['GPU']=gpu
   if [d['uuid'] for d in gpu['devices']]!=[self.m['GPU_UUID']]:raise MonitorPause('GPU UUID/count changed')
   pids=sorted(x['pid'] for x in gpu['compute_candidates'])
   if len(pids)>POLICY['expected_compute_candidates_for_single_engine']:raise MonitorPause('Additional GPU compute candidates; attribution remains unknown, review required')
   if pids and self.candidate_baseline is None:self.candidate_baseline=pids
   if self.candidate_baseline is not None and pids!=self.candidate_baseline:raise MonitorPause('GPU compute candidate identity changed/disappeared; review required')
   row['candidate_baseline']=self.candidate_baseline
   row['native_trace_identity']=self.trace_identity(owner)
   if phase in ('pre_driver','running','post_driver') and (not pids or not row['native_trace_identity']):raise MonitorPause('Ready service lacks GPU/native trace identity evidence')
  except Exception as exc:
   reason=str(exc);row.update(error_type=type(exc).__name__,reason=reason,exception_errno=getattr(exc,'errno',None),exception_frames=[dict(file=pathlib.Path(f.filename).name,function=f.name,line=f.lineno) for f in traceback.extract_tb(exc.__traceback__)])
  row['action']='pause_for_review' if reason else 'continue_in_approved_window'
  row['observation_end_monotonic']=time.monotonic();row['observation_end_utc']=utc()
  row['uncertain_window_start_monotonic']=self.last_healthy['observation_start_monotonic'] if self.last_healthy else row['monotonic']
  append(self.folder/'resource-monitor.jsonl',row)
  if reason:
   pause=self.folder/'resource-monitor-pause.json'
   if not pause.exists():save(pause,dict(row,no_automatic_resume=True,no_automatic_external_classification=True,unknown_alone_is_failure=False))
   raise MonitorPause(reason)
  self.last_healthy=dict(sequence=self.sequence,observation_start_monotonic=row['monotonic'],observation_end_monotonic=row['observation_end_monotonic'],utc=row['utc'])
  return row

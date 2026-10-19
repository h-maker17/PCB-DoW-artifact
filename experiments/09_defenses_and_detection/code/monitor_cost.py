from common import *
import hashlib
def main():
 ds=read(R/'datasets/H2-dataset.json');subset=[]
 for task in ['hotpotqa','triviaqa','gov_report']:subset.extend([d for d in ds if d['task']==task and d['split']=='test' and d['category']=='legal-tail-diversity'][:3])
 rows=[]
 for d in subset:
  root=Path(d['run_directory']);texts=[]
  for p in sorted(root.glob('*.request.json')):
   q=read(p);u=q['messages'][1]['content'];assert u.startswith('<CONTEXT>\n');tool=u[len('<CONTEXT>\n'):].split('\n</CONTEXT>',1)[0];texts.append(tool)
  assert [digest(t) for t in texts]==d['tool_returns_sha256']
  buffers=[t.encode('utf-8') for t in texts];cpu_byte=[];cpu_health=[]
  for _ in range(100):
   start=time.process_time_ns();hashes=[hashlib.sha256(b).digest() for b in buffers];flag=len(set(hashes))>1;cpu_byte.append(time.process_time_ns()-start)
   start=time.process_time_ns();f=features(d['prompt'],d['cached']);cpu_health.append(time.process_time_ns()-start)
  minimal=json.dumps({'prompt':d['prompt'],'cached':d['cached']},separators=(',',':')).encode()
  rows.append({'task':d['task'],'group':d['group'],'N':d['N'],'tool_return_log_bytes':sum(len(b) for b in buffers),'minimal_counter_log_bytes':len(minimal),'byte_comparison_cpu_median_us':statistics.median(cpu_byte)/1000,'health_statistics_cpu_median_us':statistics.median(cpu_health)/1000,'byte_divergence_detected':flag,'timing_scope':'SHA256/equality and four-feature CPU logic, excludes log I/O/collection/network; repeated fixed buffers','iterations':100})
 save(R/'data/ancillary/monitor-cost.json',rows);print('MONITOR COST COMPLETE',len(rows))
if __name__=='__main__':main()

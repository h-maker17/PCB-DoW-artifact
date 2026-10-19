"""Prespecified descriptive schedule, raw metric and per-cell failure tables."""
import math,statistics,collections
from formal_core import bootstrap,cellkey
def reports(ledger,rows,decisions,docs):
 valid={d['attempt_id']:d['cost_eligible'] for d in decisions};ix={cellkey(r):r for r in rows};ids=sorted(d['id'] for d in docs);contrasts=[]
 for n in (5,20):
  for arm in ('D','A','B'):
   for f in (.1,.25,.5):
    for top,bottom in (('P','S'),('C','P')):
     included=[];excluded=[]
     for doc in ids:
      values=[]
      for k in (1,2,3):
       x=ix.get((doc,k,n,top,arm));y=ix.get((doc,k,n,bottom,arm))
       if not x or not y or not valid[x['attempt_id']] or not valid[y['attempt_id']]:break
       a=x['P']-(1-f)*x['H'];b=y['P']-(1-f)*y['H']
       if not math.isfinite(a) or not math.isfinite(b) or min(a,b)<=0:raise ValueError('Invalid descriptive cost')
       values.append(math.log(a/b))
      if len(values)==3:included.append(dict(document_id=doc,repeat_log_ratios=values,document_mean_log=statistics.mean(values)))
      else:excluded.append(dict(document_id=doc,reason='requires_all3of3_same_arm_schedule_pairs'))
     contrasts.append(dict(N=n,arm=arm,f=f,comparison=top+'/'+bottom,measure='same-arm input cost ratio across schedules',documents=included,excluded=excluded,ratio=bootstrap([d['document_mean_log'] for d in included],True),descriptive_uncorrected=True))
 table=[];terminal_events=ledger.terminal_events()
 def attempt_counts(xs):
  known=[e for e in xs if e['sent_count_known']];sent=sum(e['sent_requests'] for e in known);failed=sum(e.get('failed_requests') or 0 for e in known);unknown=sum(e.get('failed_requests') is None for e in known)
  return dict(attempts=len(xs),complete_cost_eligible=sum(e['cost_eligible'] for e in xs),invalid_or_failed=sum(not e['cost_eligible'] for e in xs),round_failure_rate=sum(not e['cost_eligible'] for e in xs)/len(xs) if xs else None,known_actual_sent=sent,known_request_failures=failed,request_failure_rate=failed/sent if sent and not unknown and len(known)==len(xs) else None,request_failure_rate_missing_reason='unknown request classification or actual sent denominator' if unknown or len(known)!=len(xs) else None,unknown_sent_attempts=len(xs)-len(known),unknown_request_failure_classification_attempts=unknown,planned_not_sent=sum(e.get('not_sent_requests') or 0 for e in known),reasons=dict(collections.Counter(e['reason'] for e in xs)))
 for n in (5,20):
  for s in ('S','P','C'):
   for a in ('D','A','B'):
    cells=[c for c in ledger.cells.values() if (c['N'],c['schedule'],c['arm'])==(n,s,a)];events=[e for e in terminal_events if (e['N'],e['schedule'],e['arm'])==(n,s,a)];started={e['logical_id'] for e in ledger.events if e['event']=='started' and (e['N'],e['schedule'],e['arm'])==(n,s,a)}
    selected=[r for r in rows if (r['N'],r['schedule'],r['arm'])==(n,s,a)];raw=[{k:r.get(k) for k in ('document_id','repeat_id','attempt_id','complete','P','H','U','O','partial_observed_P','partial_observed_H','round_latency','request_latencies','completed_request_throughput','completed_input_throughput','output_throughput')} for r in selected]
    table.append(dict(N=n,schedule=s,arm=a,planned_rounds=len(cells),attempted_original_rounds=len(started),unattempted_original_rounds=len(cells)-len(started),first=attempt_counts([e for e in events if e['attempt_index']==1]),retry=attempt_counts([e for e in events if e['attempt_index']==2]),selected_raw_rounds=raw,TTFT=None,TTFT_missing_reason='non-stream core path',latency_resource_scope='descriptive deployment sequencing; no saturation capacity or cache-only latency claim'))
 return dict(schedule_cost_comparisons=contrasts,per_cell_plan_failure_raw_metrics=table,multiple_comparison_policy='all secondary descriptive, uncorrected95CI',repeat_resampling=False)

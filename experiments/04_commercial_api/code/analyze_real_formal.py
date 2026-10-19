"""Read-only real evidence analysis; no execution permission or credential access."""
from pathlib import Path
from datetime import datetime,timezone
from decimal import Decimal as D
import json,sqlite3,sys,hashlib
B=Path(__file__).resolve().parent;R=B.parents[1];sys.dont_write_bytecode=True
sys.path.insert(0,str(R/'code'))
import formal_v4 as m
import formal_v4_statistics as stats
def read(p):return json.loads(p.read_text(encoding='utf-8'))
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
    rule=read(R/'config/runtime_policy/formal_v4_ds700_only_20261013.json')
    for n,h in rule['source_hashes'].items():assert sha(R/'code'/n)==h
    plan=read(R/rule['plan_path']);assert m.core.sha(plan)==rule['plan_hash']
    contract=read(R/rule['contract_path']);baseline=read(R/rule['baseline_path'])
    events={}
    for name in baseline['databases']:
        db=sqlite3.connect((R/'logs'/name).as_uri()+'?mode=ro',uri=True)
        try:
            rows=m.chain(db);m.c.prefix(rows,baseline['databases'][name]['rows'])
            assert m.core.canonical(db.execute('SELECT * FROM meta ORDER BY id').fetchall())==m.core.canonical(baseline['databases'][name]['meta_rows'])
            events[name]=rows
        finally:db.close()
    for rel,h in baseline['anchor_hashes'].items():assert sha(R/rel)==h
    attempts={};totals={p:{'external_reconciled':D(0),'usage_priced_estimate':D(0),'unknown_or_unsettled_reserve':D(0)} for p in ('deepseek','openai')};peaks={p:D(0) for p in totals};trace=[]
    for seq,k,t,prev,h in events['project_cash.sqlite']:
        d=json.loads(t)
        if k=='RESERVE':attempts[d['attempt']]={**d,'status':'RESERVED'}
        elif k=='TRANSPORT_START':attempts[d['attempt']]['transport_consumed']=True
        elif k=='UNKNOWN':attempts[d['attempt']].update(status='UNKNOWN',reason=d.get('reason'))
        elif k in ('SETTLE','EXTERNAL_BILL_RECONCILIATION'):attempts[d['attempt']].update(d,status='SETTLED' if k=='SETTLE' else 'EXTERNALLY_RECONCILED')
        for p in totals:
            v={x:D(0) for x in totals[p]}
            for a in attempts.values():
                if a['provider']!=p:continue
                bucket='external_reconciled' if a['status']=='EXTERNALLY_RECONCILED' else 'usage_priced_estimate' if a['status']=='SETTLED' else 'unknown_or_unsettled_reserve'
                v[bucket]+=D(a['cash'] if bucket!='unknown_or_unsettled_reserve' else a['reserve'])
            totals[p]=v;exposure=sum(v.values(),D(0));peaks[p]=max(peaks[p],exposure)
        trace.append({'seq':seq,'kind':k,'hash':h,'attempt':d.get('attempt'),'CNY_exposure':str(sum(totals['deepseek'].values(),D(0))),'USD_exposure':str(sum(totals['openai'].values(),D(0)))})
    obs={};receipts={};stop=None;complete=False
    for seq,k,t,prev,h in events['runtime_workflow.sqlite'][len(baseline['databases']['runtime_workflow.sqlite']['rows']):]:
        d=json.loads(t)
        if k=='FORMAL_V4_OBSERVATION':obs[d['request_id']]=d
        elif k=='FORMAL_V4_RECEIPT':receipts[d['request_id']]=d
        elif k=='FORMAL_V4_STOP':stop=d['reason']
        elif k=='FORMAL_V4_INTERRUPTION' and not d['recoverable']:stop=d['reason']
        elif k=='FORMAL_V4_COMPLETE':complete=True
    status=[];measurements={};raw_index=[];session=read(R/rule['config_path'])['session_id'];pm={r['request_id']:r for r in plan}
    for rid in rule['request_ids']:
        a=attempts.get(session+'|'+rid);o=obs.get(rid);rec=receipts.get(rid);error=None;v=None
        if o:
            p=R/o['response_path'];assert sha(p)==o['raw_sha256']
            raw_index.append({'request_id':rid,'path':o['response_path'],'sha256':sha(p)})
            try:v=m.pricing.value_response(m.c.raw_json(p),'deepseek',pm[rid]['body']['model'],contract,o['timing'])
            except (m.EvidenceError,ValueError,KeyError,TypeError) as exc:error=str(exc) if isinstance(exc,m.EvidenceError) else type(exc).__name__
            if rec:assert v==rec['value'] and rec['technical_accepted'] is True
        if v and a and a['status']=='SETTLED':
            assert all(a[k]==v['lower'][k] for k in ('cash','input_cost','output_cost'))
            q=v['parsed'];measurements[rid]={'input_cost':a['input_cost'],'output_cost':a['output_cost'],'input_tokens':q['input_tokens'],'hit_tokens':q['buckets']['hit'],'miss_tokens':q['buckets']['miss'],'uniform_peak_input_cost':m.pricing.cost_at(q,contract['deepseek']['peak_rates'],contract['deepseek']['surcharge_upper'])['input_cost'],'technical_accepted':v['technical_accepted']}
        state='NOT_SENT' if not a else 'SUCCESS' if rec else 'FAILED_OR_INCOMPLETE'
        fallback=('UNRESOLVED_RESERVED_NO_REPLAY' if a['status']=='RESERVED' else 'SETTLED_WITHOUT_RECEIPT' if a['status']=='SETTLED' else 'UNKNOWN_NO_REPLAY') if a and not rec else 'NOT_YET_EXECUTED'
        status.append({'request_id':rid,'state':state,'attempt_id':a.get('attempt') if a else None,'transport_consumed':a.get('transport_consumed',False) if a else False,'cash_status':a.get('status') if a else None,'reason':None if rec else error or (a.get('reason') if a else None) or stop or fallback,'observation':o,'value':v})
    result=stats.analyze(plan,measurements,read(R/rule['statistics_path']))
    result.update(kind='RAW_GENERATED_DEEPSEEK_FORMAL_V4_RESULT',generated_utc=datetime.now(timezone.utc).isoformat(),assigned=700,successful=sum(s['state']=='SUCCESS' for s in status),consumed=sum(s['attempt_id'] is not None for s in status),not_sent=sum(s['state']=='NOT_SENT' for s in status),stop_reason=stop,completion_event=complete,completion_verified=complete and len(receipts)==700 and result['all700_technical_accepted'],costs={p:{**{k:str(v) for k,v in b.items()},'budget_exposure':str(sum(b.values(),D(0))),'dispatch_remaining':str(D(27)-sum(b.values(),D(0))),'hard_cap_remaining':str(D(30)-sum(b.values(),D(0))),'historical_full_trace_peak':str(peaks[p])} for p,b in totals.items()},source_hashes=rule['source_hashes'],plan_hash=rule['plan_hash'],raw_index=raw_index,all700_status=status,cash_trace=trace,ledger_counts={n:len(e) for n,e in events.items()},invoice_verified=False,local_owner_admin_can_modify=True)
    out=R/'reports/deepseek_formal_v4_20261013';out.mkdir(exist_ok=True)
    target=out/('REAL_RESULT_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json');target.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'result_path':str(target),'sha256':sha(target),'successful':result['successful'],'consumed':result['consumed'],'primary_estimable':result['primary_estimable'],'completion_verified':result['completion_verified'],'stop_reason':stop}))
if __name__=='__main__':main()

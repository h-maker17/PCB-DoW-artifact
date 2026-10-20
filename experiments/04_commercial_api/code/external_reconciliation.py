"""One existing failed-clock observation, matched external bill, no new replacement."""
from pathlib import Path
import os
from datetime import datetime,timezone
from decimal import Decimal
import copy,hashlib,json,math
import protocol_core as core
import bootstrap_policy as bootstrap
import http_adapters as http
import calibration_recovery as prior
from offline_coordinator import build_schedule

KIND='HISTORY_ONLY_EXTERNAL_BILL_ACCEPTANCE'
RULE_KIND='ONE_EXISTING_CLOCK_FAILED_HISTORY_ONLY_RECONCILIATION'
SCOPE='EXISTING_EXTERNAL_BILL_AND_TECHNICAL_COVERAGE_ONLY'
OPERATIONS=('SOURCE_MIGRATION','EXTERNAL_BILL_RECONCILIATION','EXTERNAL_BILL_EVIDENCE_ACCEPT','RECEIPT','EXTERNAL_BILL_TECHNICAL_COVERAGE')
AUTH_ANSWER='OPERATOR_AUTHORIZED_HISTORY_ONLY_RECONCILIATION'
def fail(code):raise core.GateError(code)
def read(relative,folder):return prior.read(relative,folder)
def fixed_evidence(rule,config):
    required={'vendor_export','attribution','human_scope','public_docs'}
    manifest=rule.get('evidence',{})
    if set(manifest)!=required:fail('EXTERNAL_EVIDENCE_COVERAGE')
    values={}
    for name,item in manifest.items():
        value,digest,_=read(item.get('path',''),'config/runtime_policy_evidence')
        if digest!=item.get('sha256'):fail('EXTERNAL_EVIDENCE_DRIFT')
        values[name]=value
    auth=values['human_scope'];attr=values['attribution'];export=values['vendor_export']
    if auth.get('kind')!='USER_APPROVAL_OF_HISTORY_ONLY_ISOLATED_CANDIDATE' or auth.get('user_answer_verbatim')!=AUTH_ANSWER or auth.get('scope')!=SCOPE or type(auth.get('new_request_allowance'))is not int or auth['new_request_allowance']!=0 or auth.get('additional_replacement_allowance')!=0 or auth.get('clock_tolerance_change')is not False:fail('EXTERNAL_HUMAN_SCOPE_REQUIRED')
    if attr.get('same_user_research_account')is not True or attr.get('only_this_replacement_request_in_hour')is not True or attr.get('user_answer_verbatim')!='OPERATOR_CONFIRMED_SAME_ACCOUNT_SINGLE_REPLACEMENT':fail('EXTERNAL_ACCOUNT_ATTRIBUTION')
    rows=export.get('projected_amount_rows');costs=export.get('projected_cost_rows')
    if not isinstance(rows,list) or not isinstance(costs,list) or len(rows)!=3 or len(costs)!=1:fail('EXTERNAL_EXPORT_SHAPE')
    allowed={'start_time_iso','end_time_iso','model','type','price','amount'}
    if any(set(r)!=allowed for r in rows) or set(costs[0])!={'start_time_iso','end_time_iso','model','wallet_type','cost','currency'}:fail('EXTERNAL_EXPORT_FIELDS')
    bytype={r['type']:r for r in rows};bill=costs[0]
    if len(bytype)!=3 or set(bytype)!={'request_count','input_cache_miss_tokens','output_tokens'}:fail('EXTERNAL_EXPORT_COMPONENTS')
    group=lambda r:(r['start_time_iso'],r['end_time_iso'],r['model'])
    if any(group(r)!=group(bill) for r in rows) or group(bill)!=('2026-10-13T14:00:00+08:00','2026-10-13T15:00:00+08:00','deepseek-v4-pro'):fail('EXTERNAL_EXPORT_GROUP')
    if bill['wallet_type']!='Paid' or bill['currency']!='CNY' or Decimal(bytype['request_count']['amount'])!=1 or bytype['request_count']['price']!='':fail('EXTERNAL_EXPORT_CURRENCY_OR_COUNT')
    miss=bytype['input_cache_miss_tokens'];out=bytype['output_tokens']
    if (Decimal(miss['amount']),Decimal(miss['price']),Decimal(out['amount']),Decimal(out['price']))!=(Decimal(2219),Decimal('.000009'),Decimal(1),Decimal('.000027')):fail('EXTERNAL_EXPORT_TOKENS_OR_RATES')
    if Decimal(bill['cost'])!=Decimal('.019998') or Decimal(miss['amount'])*Decimal(miss['price'])+Decimal(out['amount'])*Decimal(out['price'])!=Decimal(bill['cost']):fail('EXTERNAL_EXPORT_AMOUNT')
    if config['providers']['deepseek']['price_per_million_peak']!={'hit':.3,'miss':9,'output':27}:fail('EXTERNAL_FROZEN_PRICES')
    docs=values['public_docs']
    prices=[x for x in docs.get('facts',[]) if x.get('url')=='https://api-docs.deepseek.com/zh-cn/quick_start/pricing/']
    if len(prices)!=1 or prices[0].get('prices_per_million',{}).get('peak')!={'hit':'0.30','miss':'9.0','output':'27.0'}:fail('EXTERNAL_PUBLIC_PRICE_EVIDENCE')
    return values

def validate_review(path,config,plan,hashes,urls):
    record,rh,rp=read(path,'review');fixture=config.get('runtime_mode')=='OFFLINE_FIXTURE'
    if record.get('kind')!=KIND or record.get('schema_version')!=2 or record.get('approved')is not True or record.get('fixture')is not fixture or record.get('scope')!=SCOPE:fail('HISTORY_ONLY_REVIEW_REQUIRED')
    if type(record.get('new_request_allowance'))is not int or record['new_request_allowance']!=0 or record.get('permitted_operations')!=list(OPERATIONS) or record.get('required_entry')!='HISTORY_ONLY_EXIT' or record.get('paid_continuation_approved')is not False:fail('HISTORY_ONLY_REVIEW_PERMISSIONS')
    rule,dh,dp=read(record.get('rule_path',''),'config/runtime_policy')
    if record.get('rule_sha256')!=dh or record.get('source_hashes')!=hashes or record.get('config_hash')!=core.sha(config) or record.get('plan_hash')!=core.sha(plan):fail('EXTERNAL_REVIEW_BINDING')
    if rule.get('kind')!=RULE_KIND or rule.get('schema_version')!=2 or rule.get('scope')!=SCOPE or rule.get('fixture')is not fixture or rule.get('scientific')!=prior.SCIENTIFIC or rule.get('historical_clock_criterion')!='FAILED':fail('HISTORY_ONLY_RULE_SCOPE')
    if type(rule.get('new_request_allowance'))is not int or rule['new_request_allowance']!=0 or rule.get('remaining_request_ids')!=[] or rule.get('permitted_operations')!=list(OPERATIONS):fail('HISTORY_ONLY_RULE_PERMISSIONS')
    if rule.get('session_id')!=config['session_id'] or rule.get('config_hash')!=core.sha(config) or rule.get('plan_hash')!=core.sha(plan):fail('EXTERNAL_ORIGINAL_BINDING')
    if Path(rule.get('canonical_root','')).resolve()!=core.ROOT.resolve():fail('EXTERNAL_MAIN_ROOT')
    if fixture:
        if not core.ROOT.resolve().is_relative_to(Path(__file__).resolve().parents[1]/'fixtures'):fail('EXTERNAL_FIXTURE_ROOT')
    elif core.ROOT.resolve()!=Path(os.environ.get('PCB_COMMERCIAL_ROOT',Path(__file__).resolve().parents[1])).resolve():fail('EXTERNAL_MAIN_ROOT')
    ids=list(build_schedule(config,plan))[:11];cal=[r for r in plan if r['phase']=='calibration']
    if len(cal)!=11 or rule.get('calibration_request_ids')!=ids or rule.get('authorized_historical_request_ids')!=ids[:1]:fail('HISTORY_ONLY_REQUEST_SCOPE')
    target=ids[0];row=next(r for r in plan if r['request_id']==target);aid=config['session_id']+'|'+target
    if row['provider']!='deepseek' or rule.get('target_request_id')!=target or rule.get('payload_hash')!=row['payload_hash'] or rule.get('legacy_attempt_id')!=aid or rule.get('replacement_attempt_id')!=aid+'|technical-replacement-1':fail('EXTERNAL_ATTEMPT_SCOPE')
    if rule.get('additional_replacement_allowance')!=0 or rule.get('retained_original_UNKNOWN_CNY')!='9.438912' or rule.get('external_consumption_CNY')!='0.019998' or rule.get('future_clock_tolerance_seconds')!=0:fail('EXTERNAL_ALLOWANCE_OR_CASH')
    if config['providers']['deepseek'].get('billing_calendar',{}).get('clock_tolerance_seconds',0)!=0:fail('EXTERNAL_CLOCK_RULE_DRIFT')
    if [x[0] for x in rule.get('original_cash_prefix',[])]!=['RESERVE','TRANSPORT_START','UNKNOWN','PAUSE']*2 or [x[0] for x in rule.get('original_workflow_prefix',[])]!=['STOP','SOURCE_MIGRATION','BOOTSTRAP_OBSERVATION','ADAPTER_DIAGNOSTIC','STOP']:fail('EXTERNAL_HISTORY_SHAPE')
    origin=rule.get('origin_workflow_binding',{});previous=rule.get('prior_execution_binding',{})
    if rule.get('cash_binding')!={k:origin.get(k) for k in ('config','plan','session_id','authority_id')} or any(previous.get(k)!=origin.get(k) for k in ('config','plan','session_id','authority_id')) or record.get('authority_id')!=rule['cash_binding']['authority_id']:fail('EXTERNAL_AUTHORITY_BINDING')
    old,oh,_=read(rule.get('prior_review_path',''),'review')
    if oh!=rule.get('prior_review_sha256') or old.get('kind')!='TECHNICAL_CALIBRATION_RECOVERY_ACCEPTANCE' or old.get('approved')is not True or old.get('fixture')is not fixture or old.get('source_hashes')!=previous.get('source_hashes') or previous.get('recovery_review_sha256')!=oh:fail('EXTERNAL_PRIOR_SOURCE_EPOCH')
    oldrule,odh,_=read(old.get('rule_path',''),'config/runtime_policy')
    if odh!=old.get('rule_sha256') or previous.get('recovery_rule_sha256')!=odh or oldrule.get('origin_workflow_binding')!=origin or oldrule.get('original_cash_prefix')!=rule['original_cash_prefix'][:4] or oldrule.get('original_workflow_prefix')!=rule['original_workflow_prefix'][:1]:fail('EXTERNAL_PRIOR_RULE_BINDING')
    evidence=fixed_evidence(rule,config)
    policy=bootstrap.validate(config,plan,origin.get('source_hashes'),urls)
    return Capability(rule,dh,dp,record,rh,rp,evidence),policy

class Capability:
    history_only=True
    new_requests_allowed=False
    mapping_kind='EXTERNAL_BILL_TECHNICAL_COVERAGE'
    def __init__(self,rule,rule_hash,rule_path,record,record_hash,record_path,evidence):
        self.rule=copy.deepcopy(rule);self.rule_hash=rule_hash;self.rule_path=rule_path
        self.record=copy.deepcopy(record);self.record_hash=record_hash;self.record_path=record_path
        self.evidence=copy.deepcopy(evidence);self.ledger=None;self.active=False;self.observation=None
    def immutable(self,check_runtime=True):
        # Recheck the current approved sources, input bytes, anchors and meta
        # between the separate durable commits; no dispatch occurs here.
        if check_runtime:self.ledger._runtime_gate._check_binding()
        rule_raw=Path(self.rule_path).read_bytes();record_raw=Path(self.record_path).read_bytes()
        if hashlib.sha256(rule_raw).hexdigest()!=self.rule_hash or hashlib.sha256(record_raw).hexdigest()!=self.record_hash or json.loads(rule_raw)!=self.rule or json.loads(record_raw)!=self.record:fail('EXTERNAL_IMMUTABLE_RULE_OR_REVIEW')
        if fixed_evidence(self.rule,self.ledger.config)!=self.evidence:fail('EXTERNAL_EVIDENCE_DRIFT')
    def cash_record(self):
        return {'attempt':self.rule['replacement_attempt_id'],'basis':'VENDOR_EXPORT_MATCHED_ARCHIVED_RESPONSE',
            'clock_criterion':'FAILED','cash':'0.019998','input_cost':'0.019971','output_cost':'0.000027',
            'input_tokens':2219,'output_tokens':1,'bound_breach':False,'rule_sha256':self.rule_hash,
            'review_sha256':self.record_hash,'evidence_digest':core.sha(self.rule['evidence'])}
    def check_ledger(self,ledger):
        if ledger.binding!=self.rule['cash_binding']:fail('EXTERNAL_CASH_BINDING')
        rows=prior.cash_rows(ledger);n=len(self.rule['original_cash_prefix'])
        if [[r[1],r[3]] for r in rows[:n]]!=self.rule['original_cash_prefix']:fail('EXTERNAL_CASH_PREFIX_DRIFT')
        attempts=ledger.snapshot()['attempts'];old=attempts.get(self.rule['legacy_attempt_id']);replacement=attempts.get(self.rule['replacement_attempt_id'])
        if not old or old['status']!='UNKNOWN' or old['reserve']!='9.438912' or old['payload_hash']!=self.rule['payload_hash']:fail('EXTERNAL_OLD_LIABILITY_CHANGED')
        if not replacement or replacement['status'] not in ('UNKNOWN','EXTERNALLY_RECONCILED') or replacement['reserve']!='9.438912' or replacement['payload_hash']!=self.rule['payload_hash']:fail('EXTERNAL_REPLACEMENT_STATE')
        reconciliations=[r for r in rows if r[1]=='EXTERNAL_BILL_RECONCILIATION']
        if reconciliations:
            data={k:v for k,v in reconciliations[0][2].items() if k!='at_utc'}
            if len(reconciliations)!=1 or rows[n]!=reconciliations[0] or data!=self.cash_record() or replacement['status']!='EXTERNALLY_RECONCILED':fail('EXTERNAL_RECONCILIATION_REPLAY')
        elif len(rows)!=n or replacement['status']!='UNKNOWN':fail('EXTERNAL_UNEXPECTED_PRE_RECONCILIATION')
        if len(rows) not in (n,n+1):fail('HISTORY_ONLY_UNEXPECTED_CASH_TAIL')
        return rows
    def migration_data(self,binding):
        return {'kind':'APPEND_ONLY_EXTERNAL_BILL_SOURCE_EPOCH','origin_binding':self.rule['origin_workflow_binding'],
            'prior_execution_binding':self.rule['prior_execution_binding'],'execution_binding':binding,
            'rule_sha256':self.rule_hash,'review_sha256':self.record_hash,
            'historical_clock_criterion':'FAILED','evidence_digest':core.sha(self.rule['evidence']),
            'authorization_scope':SCOPE,'new_request_allowance':0}
    def validate_history(self,events):
        n=len(self.rule['original_workflow_prefix'])
        if [[e[1],e[3]] for e in events[:n]]!=self.rule['original_workflow_prefix']:fail('EXTERNAL_WORKFLOW_PREFIX_DRIFT')
        first=events[1][2]
        if first.get('execution_binding')!=self.rule['prior_execution_binding'] or first.get('origin_binding')!=self.rule['origin_workflow_binding'] or first.get('review_sha256')!=self.rule['prior_review_sha256']:fail('EXTERNAL_HISTORY_SOURCE_EPOCH')
        observed=events[2][2];diagnostic=events[3][2]
        if observed.get('request_id')!=self.rule['target_request_id'] or observed.get('payload_hash')!=self.rule['payload_hash'] or observed.get('binding_digest')!=core.sha(self.rule['prior_execution_binding']):fail('EXTERNAL_ARCHIVED_OBSERVATION_BINDING')
        o=observed['observation'];d=http.validate_diagnostic(diagnostic['diagnostic'])
        if diagnostic.get('cash_status_at_failure')!='UNKNOWN' or diagnostic.get('binding_digest')!=observed['binding_digest'] or d['stage']!='NORMALIZE' or d['safe_error_code']!='PRICE_CLOCK_MISMATCH':fail('EXTERNAL_ORIGINAL_DIAGNOSTIC')
        u=o.get('usage',{})
        if o.get('model')!='deepseek-v4-pro' or o.get('created')!=1791872607 or any(type(u.get(k))is not int or u[k]!=v for k,v in {'prompt_tokens':2219,'prompt_cache_hit_tokens':0,'prompt_cache_miss_tokens':2219,'completion_tokens':1,'total_tokens':2220}.items()) or o.get('finish_reason') not in ('stop','length'):fail('EXTERNAL_ARCHIVED_USAGE')
        if set(u)-{'prompt_tokens','prompt_cache_hit_tokens','prompt_cache_miss_tokens','completion_tokens','total_tokens','completion_tokens_details'}:fail('EXTERNAL_USAGE_FIELDS')
        if 'completion_tokens_details' in u and u['completion_tokens_details']!={'reasoning_tokens':0}:fail('EXTERNAL_REASONING_USAGE')
        start=datetime.fromisoformat(o['request_started_utc']);end=datetime.fromisoformat(o['response_received_utc']);created=datetime.fromtimestamp(o['created'],timezone.utc)
        if start.utcoffset()!=timezone.utc.utcoffset(start) or end.utcoffset()!=timezone.utc.utcoffset(end) or start<=created<=end or not datetime(2026,10,13,6,tzinfo=timezone.utc)<=start<=end<datetime(2026,10,13,7,tzinfo=timezone.utc):fail('EXTERNAL_HISTORICAL_CLOCK_FACTS')
        elapsed=o.get('elapsed_monotonic_seconds')
        if type(elapsed)not in (float,int) or not math.isfinite(elapsed) or not 0<=elapsed<=120 or abs((end-start).total_seconds()-elapsed)>1:fail('EXTERNAL_CLIENT_DURATION')
        self.observation=copy.deepcopy(o)
    def evidence_acceptance(self):
        return {'attempt':self.rule['replacement_attempt_id'],'cash_record_digest':core.sha(self.cash_record()),
            'export_consumption_CNY':'0.019998','evidence_digest':core.sha(self.rule['evidence']),
            'original_clock_criterion':'FAILED','not_a_claim_of_request_level_signed_invoice':True}
    def covered_receipt(self,rid,response=None):
        if rid!=self.rule['target_request_id'] or self.observation is None:fail('EXTERNAL_NOT_THE_HISTORICAL_TARGET')
        o=self.observation
        clean={'model':o['model'],'usage':{k:o['usage'][k] for k in ('prompt_tokens','prompt_cache_hit_tokens','prompt_cache_miss_tokens','completion_tokens')},
            'api_status':'completed','truncated':o['finish_reason']=='length','price_regime':'peak',
            **{k:o[k] for k in ('request_started_utc','response_received_utc','elapsed_monotonic_seconds')}}
        if 'completion_tokens_details' in o['usage']:clean['usage']['completion_tokens_details']=copy.deepcopy(o['usage']['completion_tokens_details'])
        if response is not None and response!=clean:fail('EXTERNAL_OBSERVATION_MISMATCH')
        return {'request_id':rid,'provider':'deepseek','phase':'calibration','payload_hash':self.rule['payload_hash'],
            'response':clean,'cash':'0.019998','input_cost':'0.019971','output_cost':'0.000027',
            **self.receipt_metadata(rid),'observation_kind':'EXTERNAL_BILL_VERIFIED_TECHNICAL_OBSERVATION_WITH_FAILED_CLOCK_CRITERION',
            'original_clock_criterion':'FAILED','original_created_utc':datetime.fromtimestamp(o['created'],timezone.utc).isoformat(),
            'billing_basis':'USER_PROVIDED_VENDOR_EXPORT_AND_CONFIRMED_SINGLE_REQUEST_HOUR',
            'evidence_digest':core.sha(self.rule['evidence'])}
    def mapping(self,receipt,binding):
        return {'request_id':self.rule['target_request_id'],'replacement_attempt_id':self.rule['replacement_attempt_id'],
            'legacy_attempt_id':self.rule['legacy_attempt_id'],'receipt_digest':core.sha(receipt),
            'binding_digest':core.sha(binding),'original_clock_criterion':'FAILED',**prior.SCIENTIFIC}
    def migrate(self,ledger,journal,binding):
        self.ledger=ledger;self.immutable();self.check_ledger(ledger)
        journal.db.execute('BEGIN IMMEDIATE')
        try:
            events=journal.events();self.validate_history(events);n=len(self.rule['original_workflow_prefix'])
            if len(events)>n:
                if events[n][1]!='SOURCE_MIGRATION' or events[n][2]!=self.migration_data(binding):fail('EXTERNAL_MIGRATION_REPLAY')
            else:
                prev=events[-1][3];data=self.migration_data(binding);h=core.sha({'kind':'SOURCE_MIGRATION','data':data,'prev':prev})
                journal.db.execute('INSERT INTO events(kind,data,prev,hash) VALUES(?,?,?,?)',('SOURCE_MIGRATION',core.canonical(data),prev,h))
            journal.db.execute('COMMIT')
        except BaseException:journal.db.execute('ROLLBACK');raise
        self.immutable();ledger.db.execute('BEGIN IMMEDIATE')
        try:
            rows=self.check_ledger(ledger)
            if len(rows)==len(self.rule['original_cash_prefix']):ledger._append('EXTERNAL_BILL_RECONCILIATION',self.cash_record())
            ledger.db.execute('COMMIT')
        except BaseException:ledger.db.execute('ROLLBACK');raise
        self.immutable();events=journal.events();tail=events[len(self.rule['original_workflow_prefix'])+1:]
        expected=[('EXTERNAL_BILL_EVIDENCE_ACCEPT',self.evidence_acceptance()),('RECEIPT',self.covered_receipt(self.rule['target_request_id'])),
                  (self.mapping_kind,self.mapping(self.covered_receipt(self.rule['target_request_id']),binding))]
        if not tail:journal.append_many(expected)
        elif [(e[1],e[2]) for e in tail[:3]]!=expected:fail('EXTERNAL_COVERAGE_REPLAY')
        self.active=True;ledger._recovery=self
    def validate_migration(self,events,binding):
        # RuntimeGate.state() has just checked runtime binding before entering.
        self.immutable(check_runtime=False);self.validate_history(events);self.check_ledger(self.ledger)
        n=len(self.rule['original_workflow_prefix']);found=[e for e in events if e[1]=='SOURCE_MIGRATION']
        if len(found)!=2 or events[n]!=found[1] or found[1][2]!=self.migration_data(binding):fail('EXTERNAL_MIGRATION_REPLAY')
        expected=[('EXTERNAL_BILL_EVIDENCE_ACCEPT',self.evidence_acceptance()),('RECEIPT',self.covered_receipt(self.rule['target_request_id'])),
                  (self.mapping_kind,self.mapping(self.covered_receipt(self.rule['target_request_id']),binding))]
        if len(events)!=n+4 or [(e[1],e[2]) for e in events[n+1:n+4]]!=expected:fail('HISTORY_ONLY_COVERAGE_REPLAY')
    def historical(self,seq,kind,digest):
        prefix=self.rule['original_workflow_prefix']
        return 1<=seq<=len(prefix) and prefix[seq-1]==[kind,digest]
    def historical_binding_digest(self):return core.sha(self.rule['prior_execution_binding'])
    def is_external_target(self,rid):return rid==self.rule['target_request_id']
    def can_override(self,provider):
        if not self.active or provider!='deepseek':return False
        rows=self.check_ledger(self.ledger);n=len(self.rule['original_cash_prefix'])
        return not any(e[1] in ('UNKNOWN','PAUSE') or e[1]=='SETTLE' and e[2].get('bound_breach') for e in rows[n:])
    def legacy(self,aid):return self.active and aid==self.rule['legacy_attempt_id']
    def attempt_id(self,rid):
        if not self.active:fail('EXTERNAL_NOT_MIGRATED')
        if rid!=self.rule['target_request_id']:fail('HISTORY_ONLY_NO_NEW_REQUESTS')
        return self.rule['replacement_attempt_id']
    def scope(self,rid):
        fail('HISTORY_ONLY_NO_NEW_REQUESTS')
    def extra_attempt(self,provider,phase):return 0
    def reserve_metadata(self,rid):
        fail('HISTORY_ONLY_NO_NEW_REQUESTS')
    def receipt_metadata(self,rid):
        # During atomic initial coverage, _recovery is intentionally not active yet.
        if rid!=self.rule['target_request_id']:fail('HISTORY_ONLY_NO_NEW_REQUESTS')
        aid=self.rule['replacement_attempt_id']
        return {'attempt_id':aid,**({'replacement_of':self.rule['legacy_attempt_id'],**prior.SCIENTIFIC} if rid==self.rule['target_request_id'] else {})}

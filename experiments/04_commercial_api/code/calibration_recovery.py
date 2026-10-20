"""Exactly one linked technical replacement; no deletion, refund, new session or authority."""
from pathlib import Path
import os
import copy,hashlib,json
import protocol_core as core
import bootstrap_policy as bootstrap
from offline_coordinator import build_schedule
SCIENTIFIC={'purpose':'TECHNICAL_CALIBRATION_ONLY','cache_initial_state':'UNKNOWN',
 'not_cold_or_warm_verification':True,'excluded_from_formal_effect_statistics':True,
 'result_selection_forbidden':True}
def fail(code): raise core.GateError(code)
def read(path,folder):
    p=(core.ROOT/path).resolve();base=(core.ROOT/folder).resolve()
    if not p.is_relative_to(base) or p.suffix!='.json' or any(x in str(p).lower() for x in ('api_key','apikey','secret','credential')):fail('RECOVERY_PATH')
    raw=p.read_bytes();return json.loads(raw),hashlib.sha256(raw).hexdigest(),str(p)
def cash_rows(ledger):
    rows=[];prev='0'*64
    for seq,kind,text,prior,h in ledger.db.execute('SELECT seq,kind,data,prev,hash FROM events ORDER BY seq'):
        data=json.loads(text)
        if prior!=prev or h!=core.sha({'kind':kind,'data':data,'prev':prior}):fail('RECOVERY_CASH_CHAIN')
        rows.append((seq,kind,data,h));prev=h
    return rows
def validate_review(path,config,plan,hashes,urls):
    record,rh,rp=read(path,'review')
    fixture=config.get('runtime_mode')=='OFFLINE_FIXTURE'
    if record.get('kind')!='TECHNICAL_CALIBRATION_RECOVERY_ACCEPTANCE' or record.get('approved')is not True or record.get('fixture')is not fixture:fail('RECOVERY_REVIEW_REQUIRED')
    rule,dh,dp=read(record.get('rule_path',''),'config/runtime_policy')
    if record.get('rule_sha256')!=dh or record.get('source_hashes')!=hashes or record.get('config_hash')!=core.sha(config) or record.get('plan_hash')!=core.sha(plan):fail('RECOVERY_REVIEW_BINDING')
    if rule.get('kind')!='ONE_LINKED_TECHNICAL_CALIBRATION_REPLACEMENT' or rule.get('scientific')!=SCIENTIFIC or rule.get('fixture')is not fixture:fail('RECOVERY_RULE_SCOPE')
    if rule.get('session_id')!=config['session_id'] or rule.get('config_hash')!=core.sha(config) or rule.get('plan_hash')!=core.sha(plan):fail('RECOVERY_ORIGINAL_BINDING')
    if Path(rule.get('canonical_root','')).resolve()!=core.ROOT.resolve():fail('RECOVERY_MAIN_ROOT')
    if not fixture and core.ROOT.resolve()!=Path(os.environ.get('PCB_COMMERCIAL_ROOT',Path(__file__).resolve().parents[1])).resolve():fail('RECOVERY_MAIN_ROOT')
    if fixture and not core.ROOT.resolve().is_relative_to(Path(__file__).resolve().parents[1]/'fixtures'):fail('RECOVERY_FIXTURE_ROOT')
    ids=list(build_schedule(config,plan))[:11];cal=[x for x in plan if x['phase']=='calibration']
    first=cal[0];aid=config['session_id']+'|'+first['request_id']
    if rule.get('calibration_request_ids')!=ids or len(cal)!=11 or first['provider']!='deepseek' or rule.get('target_request_id')!=first['request_id'] or rule.get('payload_hash')!=first['payload_hash']:fail('RECOVERY_REQUEST_SCOPE')
    if rule.get('legacy_attempt_id')!=aid or rule.get('replacement_attempt_id')!=aid+'|technical-replacement-1' or rule.get('replacement_allowance')!=1 or rule.get('retained_reserve_CNY')!='9.438912':fail('RECOVERY_ATTEMPT_SCOPE')
    if [x[0] for x in rule.get('original_cash_prefix',[])]!=['RESERVE','TRANSPORT_START','UNKNOWN','PAUSE'] or [x[0] for x in rule.get('original_workflow_prefix',[])]!=['STOP']:fail('RECOVERY_ORIGINAL_PREFIX_SHAPE')
    if rule.get('original_source_hashes')!=rule.get('origin_workflow_binding',{}).get('source_hashes'):fail('RECOVERY_ORIGINAL_SOURCE_BINDING')
    if rule.get('cash_binding')!={k:rule.get('origin_workflow_binding',{}).get(k) for k in ('config','plan','session_id','authority_id')}:fail('RECOVERY_AUTHORITY_BINDING')
    if record.get('authority_id')!=rule['cash_binding']['authority_id'] or record.get('scope')!='ONLY_ONE_TECHNICAL_REPLACEMENT_AND_ORIGINAL_REMAINING_TEN':fail('RECOVERY_AUTHORITY_BINDING')
    declaration,uh,_=read(rule.get('user_authorization_path',''),'config')
    if uh!=rule.get('user_authorization_sha256') or declaration.get('one_technical_calibration_replacement_approved')is not True or declaration.get('user_message')!='OPERATOR_CONFIRMED':fail('RECOVERY_HUMAN_AUTHORIZATION')
    original,oh,_=read(config['bootstrap_scope']['review_path'],'review')
    if oh!=rule.get('original_paid_review_sha256'):fail('RECOVERY_ORIGINAL_REVIEW_DRIFT')
    policy=bootstrap.validate(config,plan,rule.get('original_source_hashes'),urls)
    return Capability(rule,dh,dp,record,rh,rp),policy
class Capability:
    def __init__(self,rule,rule_hash,rule_path,record,record_hash,record_path):
        self.rule=copy.deepcopy(rule);self.rule_hash=rule_hash;self.rule_path=rule_path
        self.record=copy.deepcopy(record);self.record_hash=record_hash;self.record_path=record_path
        self.ledger=None;self.active=False
    def check_ledger(self,ledger):
        if ledger.binding!=self.rule['cash_binding']:fail('RECOVERY_CASH_BINDING')
        rows=cash_rows(ledger);prefix=self.rule['original_cash_prefix']
        if len(rows)<len(prefix) or [[x[1],x[3]] for x in rows[:len(prefix)]]!=prefix:fail('RECOVERY_CASH_PREFIX_DRIFT')
        a=ledger.snapshot()['attempts'].get(self.rule['legacy_attempt_id'])
        if not a or a['status']!='UNKNOWN' or a['reserve']!='9.438912' or a['request_id']!=self.rule['target_request_id'] or a['payload_hash']!=self.rule['payload_hash']:fail('RECOVERY_OLD_LIABILITY_CHANGED')
        return rows
    def migration_data(self,binding):
        return {'kind':'APPEND_ONLY_EXECUTION_SOURCE_MIGRATION','origin_binding':self.rule['origin_workflow_binding'],
          'execution_binding':binding,'review_sha256':self.record_hash,'rule_sha256':self.rule_hash,
          'legacy_attempt_id':self.rule['legacy_attempt_id'],'replacement_attempt_id':self.rule['replacement_attempt_id'],
          'scientific':SCIENTIFIC}
    def migrate(self,ledger,journal,binding):
        self.check_ledger(ledger);self.ledger=ledger
        journal.db.execute('BEGIN IMMEDIATE')
        try:
            events=journal.events();n=len(self.rule['original_workflow_prefix'])
            if [[x[1],x[3]] for x in events[:n]]!=self.rule['original_workflow_prefix']:fail('RECOVERY_WORKFLOW_PREFIX_DRIFT')
            expected=self.migration_data(binding)
            migrations=[e for e in events if e[1]=='SOURCE_MIGRATION']
            if migrations:
                if len(migrations)!=1 or events[n]!=migrations[0] or migrations[0][2]!=expected:fail('RECOVERY_MIGRATION_REPLAY')
            else:
                if len(events)!=n:fail('RECOVERY_UNEXPECTED_OLD_WORKFLOW')
                prev=events[-1][3] if events else '0'*64
                h=core.sha({'kind':'SOURCE_MIGRATION','data':expected,'prev':prev})
                journal.db.execute('INSERT INTO events(kind,data,prev,hash) VALUES(?,?,?,?)',('SOURCE_MIGRATION',core.canonical(expected),prev,h))
            journal.db.execute('COMMIT')
        except BaseException:
            journal.db.execute('ROLLBACK');raise
        self.active=True;ledger._recovery=self
    def validate_migration(self,events,binding):
        if hashlib.sha256(Path(self.rule_path).read_bytes()).hexdigest()!=self.rule_hash or hashlib.sha256(Path(self.record_path).read_bytes()).hexdigest()!=self.record_hash:fail('RECOVERY_IMMUTABLE_REVIEW_OR_RULE')
        n=len(self.rule['original_workflow_prefix'])
        if [[e[1],e[3]] for e in events[:n]]!=self.rule['original_workflow_prefix']:fail('RECOVERY_WORKFLOW_PREFIX_DRIFT')
        found=[e for e in events if e[1]=='SOURCE_MIGRATION']
        if len(found)!=1 or events[n]!=found[0] or found[0][2]!=self.migration_data(binding):fail('RECOVERY_MIGRATION_REPLAY')
        self.check_ledger(self.ledger)
    def can_override(self,provider):
        if not self.active or provider!='deepseek':return False
        rows=self.check_ledger(self.ledger)
        return not any(e[1] in ('UNKNOWN','PAUSE') or e[1]=='SETTLE' and e[2].get('bound_breach') for e in rows[len(self.rule['original_cash_prefix']):])
    def legacy(self,aid):
        return self.active and aid==self.rule['legacy_attempt_id']
    def attempt_id(self,rid):
        if not self.active:fail('RECOVERY_NOT_MIGRATED')
        if rid==self.rule['target_request_id']:return self.rule['replacement_attempt_id']
        return self.rule['session_id']+'|'+rid
    def scope(self,rid):
        if rid not in self.rule['calibration_request_ids']:fail('RECOVERY_TECHNICAL_SCOPE_END')
        if not self.can_override('deepseek'):fail('RECOVERY_NEW_FAILURE_STOP')
    def extra_attempt(self,provider,phase):
        return 1 if self.active and provider=='deepseek' and phase=='calibration' else 0
    def reserve_metadata(self,rid):
        if rid!=self.rule['target_request_id']:return {}
        return {'replacement_of':self.rule['legacy_attempt_id'],'attempt_kind':'TECHNICAL_CALIBRATION_REPLACEMENT_ONLY',**SCIENTIFIC}
    def receipt_metadata(self,rid):
        return {'attempt_id':self.attempt_id(rid),**(self.reserve_metadata(rid) if rid==self.rule['target_request_id'] else {})}
    def mapping(self,receipt,binding):
        return {'request_id':self.rule['target_request_id'],'replacement_attempt_id':self.rule['replacement_attempt_id'],
          'legacy_attempt_id':self.rule['legacy_attempt_id'],'receipt_digest':core.sha(receipt),
          'binding_digest':core.sha(binding),**SCIENTIFIC}

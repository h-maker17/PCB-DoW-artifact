"""Explicit, append-only ten-ID calibration continuation; no later-stage API."""
from pathlib import Path
from datetime import datetime,timezone
from decimal import Decimal as D
import argparse,copy,getpass,hashlib,json,os,sqlite3,ssl,sys,time,uuid,warnings,types
import urllib.request,urllib.error
# Compile the unchanged protocol helpers directly from their bound source;
# do not trust an older production __pycache__ for ledger replay or hashing.
for _name in ('offline_prepare','protocol_core'):
    _path=Path(__file__).resolve().parent/(_name+'.py')
    _module=types.ModuleType(_name);_module.__file__=str(_path);sys.modules[_name]=_module
    exec(compile(_path.read_text(encoding='utf-8'),str(_path),'exec'),_module.__dict__)
core=sys.modules['protocol_core']
from calibration_v2_pricing import EvidenceError,worst_reserve,preflight_price,value_response,validate_raw

ROOT=Path(__file__).resolve().parents[1]
PRODUCTION=Path(os.environ.get('PCB_COMMERCIAL_ROOT',Path(__file__).resolve().parents[1])).resolve()
URLS={'deepseek':'https://api.deepseek.com/chat/completions'}
SCOPE='TEN_ORIGINAL_UNSENT_CALIBRATION_METHOD_V2_ONLY'
KIND='CALIBRATION_V2_PRODUCTION_APPROVAL'
SOURCE_NAMES=('protocol_core.py','http_adapters.py','user_entry.py','runtime_gate.py','offline_coordinator.py','offline_prepare.py','bootstrap_policy.py','calibration_recovery.py','external_reconciliation.py','calibration_v2.py','calibration_v2_pricing.py','calibration_v2_capture.py')
class Stop(core.GateError):pass
def utc():return datetime.now(timezone.utc).isoformat()
def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def raw_json(path):
    def pairs(items):
        result={}
        for key,value in items:
            if key in result:raise Stop('DUPLICATE_RAW_RESPONSE_FIELDS')
            result[key]=value
        return result
    def constant(value):raise Stop('NONFINITE_RAW_RESPONSE_FIELDS')
    return json.loads(Path(path).read_text(encoding='utf-8'),object_pairs_hook=pairs,parse_constant=constant)
def write_new(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('xb') as f:f.write(data);f.flush();os.fsync(f.fileno())
def save(path,obj):write_new(path,(core.canonical(obj)+'\n').encode('utf-8'))
def safe_path(root,relative,folder):
    path=(root/relative).resolve();base=(root/folder).resolve()
    if not path.is_relative_to(base) or any(x in path.name.lower() for x in ('api_key','apikey','secret','credential')):raise Stop('UNAPPROVED_ARTIFACT_PATH')
    return path
def read(root,relative,folder):
    path=safe_path(root,relative,folder)
    return json.loads(path.read_text(encoding='utf-8')),digest(path)
def chain(db):
    rows=db.execute('SELECT seq,kind,data,prev,hash FROM events ORDER BY seq').fetchall();prev='0'*64
    for _,kind,text,prior,h in rows:
        if prior!=prev or core.sha({'kind':kind,'data':json.loads(text),'prev':prior})!=h:raise Stop('EVENT_CHAIN_INVALID')
        prev=h
    return rows
def prefix(rows,expected):
    if len(rows)<len(expected) or core.canonical(rows[:len(expected)])!=core.canonical(expected):raise Stop('HISTORICAL_PREFIX_CHANGED')
def append(db,kind,data):
    db.execute('BEGIN IMMEDIATE')
    try:
        old=db.execute('SELECT hash FROM events ORDER BY seq DESC LIMIT 1').fetchone()
        prev=old[0] if old else '0'*64;data={'at_utc':utc(),**data}
        h=core.sha({'kind':kind,'data':data,'prev':prev})
        db.execute('INSERT INTO events(kind,data,prev,hash) VALUES(?,?,?,?)',(kind,core.canonical(data),prev,h))
        db.execute('COMMIT');return h
    except BaseException:db.execute('ROLLBACK');raise

class Authority:
    def __init__(self,root,review_path,*,fixture=False):
        self.root=Path(root).resolve();self.fixture=fixture;self.cash=None;self.workflow=None;self.lock=None
        if fixture:
            if not self.root.is_relative_to(PRODUCTION/'staging/calibration_method_v2_20261013/fixtures'):raise Stop('FIXTURE_ROOT_ONLY')
        elif self.root!=PRODUCTION:raise Stop('PRODUCTION_ROOT_REQUIRED')
        self.review,self.review_hash=read(self.root,review_path,'review')
        r=self.review
        if r.get('kind')!=KIND or r.get('approved')is not True or r.get('fixture')is not fixture or r.get('scope')!=SCOPE or r.get('new_request_allowance')!=10 or r.get('paid_later_stages_allowed')is not False:raise Stop('NO_EXACT_CALIBRATION_PERMISSION')
        if not fixture and (r.get('reviewer')!='/root/calibration_method_independent_review' or r.get('production_review_after_exact_install')is not True):raise Stop('INDEPENDENT_PRODUCTION_REVIEW_REQUIRED')
        self.review_path=safe_path(self.root,review_path,'review')
        self.rule,self.rule_hash=read(self.root,r['rule_path'],'config/runtime_policy')
        self.baseline,self.baseline_hash=read(self.root,self.rule['baseline_path'],'config/runtime_policy_evidence')
        self.contract,self.contract_hash=read(self.root,self.rule['contract_path'],'config/runtime_policy_evidence')
        self.method,self.method_hash=read(self.root,self.rule['method_path'],'config/runtime_policy_evidence')
        self.authorization,self.authorization_hash=read(self.root,self.rule['authorization_path'],'config/runtime_policy_evidence')
        if r.get('rule_sha256')!=self.rule_hash or r.get('baseline_sha256')!=self.baseline_hash or r.get('contract_sha256')!=self.contract_hash or r.get('method_sha256')!=self.method_hash or r.get('authorization_sha256')!=self.authorization_hash:raise Stop('REVIEW_EVIDENCE_BINDING')
        self.config,self.config_raw_hash=read(self.root,self.rule['config_path'],'config')
        plan,self.plan_raw_hash=read(self.root,self.rule['plan_path'],'config');self.plan={p['request_id']:p for p in plan}
        if core.sha(self.config)!=self.rule['config_hash'] or core.sha(plan)!=self.rule['plan_hash'] or len(self.plan)!=len(plan):raise Stop('FROZEN_PLAN_CHANGED')
        cash_binding=self.baseline['databases']['project_cash.sqlite']['meta']
        if cash_binding!={'authority_id':self.rule['authority_id'],'config':self.rule['config_hash'],'plan':self.rule['plan_hash'],'session_id':self.config['session_id']}:raise Stop('ORIGINAL_AUTHORITY_SESSION_BINDING')
        self.ids=tuple(self.rule['request_ids'])
        expected=[p['request_id'] for provider in ('deepseek','openai') for p in plan if p['provider']==provider and p['phase']=='calibration'][1:]
        if len(self.ids)!=10 or list(self.ids)!=expected or self.ids[0]!='deepseek-calibration-formal-d2-initial-K1400':raise Stop('EXACT_TEN_ID_SCOPE_REQUIRED')
        if self.plan[self.ids[0]]['payload_hash']!='7b0fb4095267eec34267fa69fa6efeb26c4999db658bad908766c269ebb0fdad' and not fixture:raise Stop('FIRST_D2_HASH_MISMATCH')
        if self.rule.get('new_request_allowance')!=10 or self.rule.get('scope')!=SCOPE or self.rule.get('output_upper')!=64 or self.rule.get('input_acceptance_upper')!=2500 or self.method.get('formal_enabled')is not False:raise Stop('RULE_METHOD_SCOPE')
        for p in plan:
            if core.sha(p['body'])!=p['payload_hash']:raise Stop('PAYLOAD_HASH_CHANGED')
        for rid in self.ids:
            p=self.plan[rid]
            if self.rule['request_payloads'].get(rid)!=p['payload_hash'] or p['phase']!='calibration' or p['body']['model']!=self.config['providers'][p['provider']]['candidate_model'] or p['body'].get('max_tokens',p['body'].get('max_output_tokens'))!=64:raise Stop('CALIBRATION_ITEM_CONTRACT')
        for provider,currency in (('deepseek','CNY'),('openai','USD')):
            p=self.config['providers'][provider]
            if p['currency']!=currency or D(str(p['dispatch_stop']))!=27 or D(str(p['hard_cap']))!=30 or p['fee_surcharge_multiplier']!=1 or self.contract[provider]['surcharge_upper']!='1':raise Stop('NATIVE_BUDGET_OR_FEE_CHANGED')
        if r.get('source_hashes')!=self.rule['source_hashes']:raise Stop('SOURCE_REVIEW_BINDING')
        if set(self.rule['source_hashes'])!=set(SOURCE_NAMES):raise Stop('EXACT_SOURCE_SET_REQUIRED')
        if self.contract.get('public_contract_validated')is not True or self.contract.get('invoice_time_source_proven')is not False:raise Stop('PUBLIC_PRICE_CONTRACT_NOT_REVIEWED')
        self.source_hashes=self.rule['source_hashes'];self.check_files()
        if not fixture:
            acl,acl_hash=read(self.root,'review/CALIBRATION_V2_RAW_DIRECTORY_ACL_20261013.json','review')
            archive=self.root/'analysis/calibration_v2_runs'
            if r.get('raw_directory_acl_record_sha256')!=acl_hash or acl.get('restricted_local_acl_verified')is not True or acl.get('path')!=str(archive.resolve()) or digest(archive/'ACL_VERIFICATION.json')!=acl.get('verification_sha256'):raise Stop('RESTRICTED_RAW_DIRECTORY_BINDING')
        try:
            for name in ('project_cash.sqlite','runtime_workflow.sqlite'):
                path=self.root/'logs'/name
                if not path.is_file() or not path.stat().st_size:raise Stop('EXISTING_AUTHORITY_REQUIRED')
            self.lock=(self.root/'logs/calibration_v2.execution.lock').open('a+b')
            if self.lock.tell()==0:self.lock.write(b'0');self.lock.flush()
            self.lock.seek(0)
            try:
                if sys.platform=='win32':
                    import msvcrt
                    msvcrt.locking(self.lock.fileno(),msvcrt.LK_NBLCK,1)
                else:
                    import fcntl
                    fcntl.flock(self.lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            except OSError:raise Stop('OTHER_CALIBRATION_PROCESS_ACTIVE') from None
            self.cash=sqlite3.connect(self.root/'logs/project_cash.sqlite',isolation_level=None,timeout=5)
            self.workflow=sqlite3.connect(self.root/'logs/runtime_workflow.sqlite',isolation_level=None,timeout=5)
            for db in (self.cash,self.workflow):db.execute('PRAGMA synchronous=FULL')
            self.check_storage()
            self.state() # No migrations, credentials or reservations in constructor.
        except BaseException:self.close();raise
    def check_files(self):
        if digest(self.review_path)!=self.review_hash:raise Stop('APPROVAL_CHANGED')
        for name,h in self.source_hashes.items():
            if digest(self.root/'code'/name)!=h:raise Stop('SOURCE_CHANGED')
        files={self.review['rule_path']:self.rule_hash,self.rule['baseline_path']:self.baseline_hash,self.rule['contract_path']:self.contract_hash,self.rule['method_path']:self.method_hash,self.rule['authorization_path']:self.authorization_hash,self.rule['config_path']:self.config_raw_hash,self.rule['plan_path']:self.plan_raw_hash}
        for rel,h in files.items():
            if digest(self.root/rel)!=h:raise Stop('BOUND_FILE_CHANGED')
        for rel,h in self.rule['evidence_source_hashes'].items():
            if digest(safe_path(self.root,rel,'config/runtime_policy_evidence'))!=h:raise Stop('PUBLIC_EVIDENCE_CHANGED')
    def check_storage(self):
        for name,db in (('project_cash.sqlite',self.cash),('runtime_workflow.sqlite',self.workflow)):
            prefix(chain(db),self.baseline['databases'][name]['rows'])
            if core.canonical(db.execute('SELECT * FROM meta ORDER BY id').fetchall())!=core.canonical(self.baseline['databases'][name]['meta_rows']):raise Stop('METADATA_CHANGED')
        for rel,h in self.baseline['anchor_hashes'].items():
            if digest(self.root/rel)!=h:raise Stop('ANCHOR_CHANGED')
    def attempts(self):
        proxy=object.__new__(core.Ledger);proxy.db=self.cash
        return proxy.snapshot()['attempts']
    def totals(self,provider):
        estimated=confirmed=pending=D(0)
        for a in self.attempts().values():
            if a['provider']!=provider:continue
            if a['status']=='EXTERNALLY_RECONCILED':confirmed+=D(a['cash'])
            elif a['status']=='SETTLED':estimated+=D(a['cash'])
            else:pending+=D(a['reserve'])
        return {'external_reconciled':str(confirmed),'usage_priced_estimate':str(estimated),'unknown_or_unsettled_reserve':str(pending),'budget_exposure':str(confirmed+estimated+pending)}
    def adoption(self):
        obs=self.baseline['historical_observation'];u=obs['observation']['usage'];row=self.plan[self.rule['historical_d1_id']]
        workflow_rows=self.baseline['databases']['runtime_workflow.sqlite']['rows']
        matches=[json.loads(text) for _,kind,text,_,h in workflow_rows if kind=='BOOTSTRAP_OBSERVATION' and h==self.baseline['historical_observation_event_hash']]
        if matches!=[obs] or obs['request_id']!=row['request_id'] or obs['payload_hash']!=row['payload_hash']:raise Stop('HISTORICAL_OBSERVATION_LINK')
        bills=[json.loads(text) for _,kind,text,_,h in self.baseline['databases']['project_cash.sqlite']['rows'] if kind=='EXTERNAL_BILL_RECONCILIATION' and h==self.baseline['external_cash_event_hash']]
        if len(bills)!=1 or bills[0]['cash']!='0.019998' or bills[0]['clock_criterion']!='FAILED':raise Stop('HISTORICAL_EXTERNAL_BILL_LINK')
        receipts=[json.loads(text) for _,kind,text,_,h in workflow_rows if kind=='RECEIPT' and h==self.baseline['historical_receipt_event_hash']]
        if len(receipts)!=1 or receipts[0]['request_id']!=row['request_id'] or receipts[0]['payload_hash']!=row['payload_hash']:raise Stop('HISTORICAL_RECEIPT_LINK')
        # The old safe projection deliberately omitted response text/structure.
        # Validate only the available historical fields, never manufacture a
        # message or pretend full raw structure was archived.
        from calibration_v2_pricing import count
        if obs['observation']['model']!=row['body']['model'] or obs['observation']['finish_reason'] not in ('stop','length'):raise Stop('HISTORICAL_LIMITED_EVIDENCE_INSUFFICIENT')
        total=count(u.get('prompt_tokens'));hit=count(u.get('prompt_cache_hit_tokens'));miss=count(u.get('prompt_cache_miss_tokens'));output=count(u.get('completion_tokens'))
        if total==0 or hit+miss!=total or count(u.get('total_tokens'))!=total+output:raise Stop('HISTORICAL_USAGE_CONSERVATION')
        parsed={'input_tokens':total,'output_tokens':output,'buckets':{'hit':hit,'miss':miss},'output_structure':None,'output_structure_missing_reason':'HISTORICAL_SAFE_PROJECTION_DID_NOT_RETAIN_MESSAGE','reasoning_tokens':None,'reasoning_tokens_missing_reason':'HISTORICAL_NOT_ITEMIZED'}
        if parsed['input_tokens']>2500 or parsed['output_tokens']>64:raise Stop('HISTORICAL_LIMITED_EVIDENCE_INSUFFICIENT')
        return {'request_id':row['request_id'],'observation_event_hash':self.baseline['historical_observation_event_hash'],'receipt_event_hash':self.baseline['historical_receipt_event_hash'],'external_reconciliation_event_hash':self.baseline['external_cash_event_hash'],'raw_usage':u,'parsed':parsed,'adopted_conditions':['MODEL_ALIAS','REAL_USAGE_FIELDS','USAGE_CONSERVATION','PAYLOAD_LINKED_MEASURED_LENGTH','OUTPUT_BOUND','EXTERNAL_BILL_MATCH'],'historical_clock_criterion':'FAILED','cache_initial_state':'UNKNOWN','not_adopted':['ZERO_SKEW_TIME_PASS','COLD_CACHE_STATE','ALL_PAYLOAD_SERIALIZER_BOUND','FULL_OUTPUT_STRUCTURE'],'historical_full_raw_body_available':False,'historical_safe_projection_only':True,'new_observation':False,'new_charge':False,'formal_enabled':False,'method_sha256':self.method_hash,'review_sha256':self.review_hash}
    def initialize(self):
        s=self.state()
        if not s['migrated']:
            append(self.workflow,'SOURCE_MIGRATION',{'kind':'APPEND_ONLY_CALIBRATION_V2_TEN_ONLY_SOURCE_EPOCH','review_sha256':self.review_hash,'rule_sha256':self.rule_hash,'source_hashes':self.source_hashes,'origin_workflow_tail':self.baseline['databases']['runtime_workflow.sqlite']['rows'][-1][4],'cash_prefix_tail':self.baseline['databases']['project_cash.sqlite']['rows'][-1][4],'method_sha256':self.method_hash,'scope':SCOPE})
        s=self.state()
        if not s['adopted']:append(self.workflow,'HISTORICAL_LIMITED_ADOPTION',self.adoption())
        return self.state()
    def state(self):
        self.check_files();self.check_storage();rows=chain(self.workflow);base=len(self.baseline['databases']['runtime_workflow.sqlite']['rows'])
        migrated=adopted=complete=False;stopped=None;observations={};receipts={}
        for _,kind,text,_,h in rows[base:]:
            d=json.loads(text)
            if kind=='SOURCE_MIGRATION':
                if migrated or d.get('review_sha256')!=self.review_hash or d.get('scope')!=SCOPE or d.get('source_hashes')!=self.source_hashes:raise Stop('CONTINUATION_MIGRATION_CHANGED')
                migrated=True
            elif kind=='HISTORICAL_LIMITED_ADOPTION':
                clone=dict(d);clone.pop('at_utc',None)
                if not migrated or adopted or clone!=self.adoption():raise Stop('HISTORICAL_ADOPTION_CHANGED')
                adopted=True
            elif kind=='CALIBRATION_V2_OBSERVATION':
                rid=d['request_id']
                if not adopted or rid not in self.ids or rid in observations or rid!=self.ids[len(receipts)]:raise Stop('OBSERVATION_ORDER_OR_REPLAY')
                observations[rid]=d
            elif kind=='CALIBRATION_V2_RECEIPT':
                rid=d['request_id']
                if rid in receipts or rid not in observations or rid!=self.ids[len(receipts)] or d.get('technical_accepted')is not True:raise Stop('RECEIPT_ORDER_OR_REPLAY')
                self.verify_receipt(d,observations[rid]);receipts[rid]=d
            elif kind=='CALIBRATION_V2_STOP':stopped=d['reason']
            elif kind=='CALIBRATION_V2_PREFLIGHT_BLOCK':pass
            elif kind=='CALIBRATION_V2_COMPLETE':
                if len(receipts)!=10 or not adopted or stopped or complete or d.get('formal_enabled')is not False or d.get('receipt_digest')!=core.sha([receipts[rid] for rid in self.ids]):raise Stop('COMPLETION_INVARIANT')
                complete=True
            else:raise Stop('UNAUTHORIZED_WORKFLOW_EVENT')
        existing=self.attempts();legacy=self.rule['legacy_unknown_attempt_id']
        if existing.get(legacy,{}).get('status')!='UNKNOWN' or existing[legacy]['reserve']!='9.438912':raise Stop('OLD_UNKNOWN_NOT_RETAINED')
        base_cash=len(self.baseline['databases']['project_cash.sqlite']['rows'])
        for _,kind,text,_,h in chain(self.cash)[base_cash:]:
            d=json.loads(text)
            if kind not in ('RESERVE','TRANSPORT_START','SETTLE','UNKNOWN','PAUSE'):raise Stop('UNAUTHORIZED_CASH_EVENT')
            if kind=='PAUSE':stopped=stopped or d.get('reason')
            elif d.get('attempt') not in {self.attempt_id(rid) for rid in self.ids}:raise Stop('UNAUTHORIZED_ATTEMPT')
        next_id=self.ids[len(receipts)] if len(receipts)<10 else None
        for rid in self.ids:
            a=existing.get(self.attempt_id(rid))
            if a is None:continue
            if a['status']=='UNKNOWN':stopped=stopped or 'NEW_UNKNOWN'
            elif a['status']=='SETTLED' and rid not in receipts:stopped=stopped or 'SETTLED_WITHOUT_RECEIPT'
            elif a['status']=='RESERVED':stopped=stopped or 'UNRESOLVED_ATTEMPT_NO_REPLAY'
        return {'phase':'STOPPED' if stopped else 'CALIBRATION_V2_COMPLETE' if complete else 'CALIBRATION_V2_OBSERVED_PENDING_COMPLETION' if len(receipts)==10 else 'CALIBRATION_V2_READY','migrated':migrated,'adopted':adopted,'receipts':receipts,'observations':observations,'stopped_reason':stopped,'next_id':None if stopped or complete else next_id,'new_request_allowance_remaining':10-len([rid for rid in self.ids if self.attempt_id(rid) in existing]),'technical_calibration_completed':complete,'formal_enabled':False}
    def verify_receipt(self,receipt,observation):
        rid=receipt['request_id'];path=safe_path(self.root,observation['response_path'],'analysis/calibration_v2_runs')
        if digest(path)!=observation['raw_sha256']:raise Stop('RAW_RESPONSE_CHANGED')
        raw=raw_json(path)
        value=value_response(raw,self.plan[rid]['provider'],self.plan[rid]['body']['model'],self.contract,observation['timing'])
        if not value['technical_accepted'] or receipt['value']!=value or receipt['payload_hash']!=self.plan[rid]['payload_hash']:raise Stop('RECEIPT_ANALYSIS_CHANGED')
        a=self.attempts().get(self.attempt_id(rid))
        if not a or a['status']!='SETTLED' or a.get('valuation_status')!='USAGE_PRICED_ESTIMATE' or any(a[k]!=value['lower'][k] for k in ('cash','input_cost','output_cost')):raise Stop('RECEIPT_CASH_CHANGED')
    def attempt_id(self,rid):return self.config['session_id']+'|'+rid
    def preflight(self,rid,now=None):
        s=self.state()
        if s['stopped_reason'] or s['next_id']!=rid or rid not in self.ids:raise Stop('SCOPE_CURSOR_OR_STOP')
        row=self.plan[rid]
        if row['phase']!='calibration':raise Stop('ONLY_CALIBRATION')
        if self.attempt_id(rid) in self.attempts():raise Stop('CONSUMED_ID_NO_REPLAY')
        reserve=worst_reserve(self.contract,row['provider']);total=self.totals(row['provider'])
        if D(total['budget_exposure'])+reserve>D(27):raise Stop('NEXT_RESERVE_EXCEEDS_STOP')
        preflight_price(self.contract,row['provider'],now or datetime.now(timezone.utc))
        return reserve,row
    def reserve(self,rid,run_id,now=None):
        reserve,row=self.preflight(rid,now)
        if not self.state()['adopted']:raise Stop('HISTORICAL_ADOPTION_REQUIRED')
        # Rechecked immediately before the durable single-use reservation.
        append(self.cash,'RESERVE',{'attempt':self.attempt_id(rid),'request_id':rid,'session_id':self.config['session_id'],'provider':row['provider'],'currency':self.config['providers'][row['provider']]['currency'],'phase':'calibration','payload_hash':row['payload_hash'],'reserve':str(reserve),'run_id':run_id,'scope':SCOPE,'review_sha256':self.review_hash})
        return reserve
    def transport_start(self,rid):
        self.check_files();self.check_storage();a=self.attempts().get(self.attempt_id(rid))
        if rid not in self.ids or not a or a['status']!='RESERVED' or a.get('transport_consumed'):raise Stop('TRANSPORT_SINGLE_USE_DENIED')
        append(self.cash,'TRANSPORT_START',{'attempt':self.attempt_id(rid),'review_sha256':self.review_hash})
    def unknown(self,rid,reason):
        a=self.attempts().get(self.attempt_id(rid))
        if a and a['status']=='RESERVED':append(self.cash,'UNKNOWN',{'attempt':self.attempt_id(rid),'reason':reason})
        append(self.cash,'PAUSE',{'provider':self.plan[rid]['provider'],'reason':reason})
        append(self.workflow,'CALIBRATION_V2_STOP',{'request_id':rid,'reason':reason,'formal_enabled':False})
    def complete_observation(self,rid,raw_path,timing):
        self.check_files();self.check_storage();a=self.attempts().get(self.attempt_id(rid))
        if rid not in self.ids or not a or a['status']!='RESERVED' or not a.get('transport_consumed'):raise Stop('NO_DURABLE_SENT_ATTEMPT')
        raw_path=Path(raw_path).resolve()
        if not raw_path.is_relative_to((self.root/'analysis/calibration_v2_runs').resolve()):raise Stop('RAW_ARCHIVE_PATH')
        raw=raw_json(raw_path);row=self.plan[rid]
        observation={'request_id':rid,'attempt_id':self.attempt_id(rid),'payload_hash':row['payload_hash'],'response_path':str(raw_path.relative_to(self.root)),'raw_sha256':digest(raw_path),'timing':timing,'provider_response_id':raw.get('id'),'provider_created':raw.get('created',raw.get('created_at')),'model':raw.get('model'),'service_tier':raw.get('service_tier'),'raw_usage':raw.get('usage'),'api_status':raw.get('status'),'measurement_fields_not_rewritten':True}
        append(self.workflow,'CALIBRATION_V2_OBSERVATION',observation)
        try:value=value_response(raw,row['provider'],row['body']['model'],self.contract,timing)
        except (EvidenceError,KeyError,TypeError,ValueError) as exc:
            self.unknown(rid,str(exc) if isinstance(exc,EvidenceError) else 'RESPONSE_ANALYSIS_INVALID');raise Stop('RESPONSE_INVALID_RESERVE_RETAINED') from None
        analysis_path=raw_path.with_name('analysis.json');save(analysis_path,{'raw_sha256':digest(raw_path),'request_id':rid,'value':value})
        if value['valuation_status']!='USAGE_PRICED_ESTIMATE':
            self.unknown(rid,'PRICE_NOT_UNIQUELY_VALUED');raise Stop('PRICE_INTERVAL_RESERVE_RETAINED')
        measured=value['lower'];breach=not value['technical_accepted'] or D(measured['cash'])>D(a['reserve'])
        append(self.cash,'SETTLE',{'attempt':self.attempt_id(rid),**measured,'input_tokens':value['parsed']['input_tokens'],'output_tokens':value['parsed']['output_tokens'],'bound_breach':breach,'valuation_status':'USAGE_PRICED_ESTIMATE','invoice_verified':False,'price_basis':value['price_basis'],'raw_sha256':digest(raw_path),'method_sha256':self.method_hash})
        if breach:
            append(self.cash,'PAUSE',{'provider':row['provider'],'reason':'CALIBRATION_ACCEPTANCE_OR_RESERVE_BREACH'})
            append(self.workflow,'CALIBRATION_V2_STOP',{'request_id':rid,'reason':'CALIBRATION_ACCEPTANCE_OR_RESERVE_BREACH','measured_value':value,'formal_enabled':False})
            raise Stop('MEASUREMENT_BREACH_ACCOUNTED_STOP')
        append(self.workflow,'CALIBRATION_V2_RECEIPT',{'request_id':rid,'attempt_id':self.attempt_id(rid),'payload_hash':row['payload_hash'],'raw_sha256':digest(raw_path),'value':value,'technical_accepted':True,'formal_effect_observation':False})
        s=self.state()
        if len(s['receipts'])==10:
            append(self.workflow,'CALIBRATION_V2_COMPLETE',{'method_sha256':self.method_hash,'historical_d1_limited_adoption':True,'new_accepted_observations':10,'logical_technical_evidence':11,'all_original_clock_criteria_passed':False,'formal_enabled':False,'receipt_digest':core.sha([s['receipts'][key] for key in self.ids])})
        return value
    def close(self):
        for db in (self.cash,self.workflow):
            if db is not None:db.close()
        self.cash=self.workflow=None
        if self.lock is not None:self.lock.close();self.lock=None

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*a,**k):return None

def trusted_tty():
    if not sys.stdin.isatty():return False
    if sys.platform=='win32':
        import ctypes
        from ctypes import wintypes
        api=ctypes.WinDLL('kernel32',use_last_error=True)
        api.GetStdHandle.restype=wintypes.HANDLE
        api.GetConsoleMode.argtypes=(wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD))
        mode=wintypes.DWORD()
        return bool(api.GetConsoleMode(api.GetStdHandle(-10),ctypes.byref(mode)))
    return sys.stderr.isatty()

def secure_prompt(provider):
    if not trusted_tty():raise Stop('TRUSTED_LOCAL_TTY_REQUIRED')
    with warnings.catch_warnings():
        warnings.simplefilter('error',getpass.GetPassWarning)
        try:value=getpass.getpass('Enter '+provider+' temporary research key locally (no echo; never saved): ')
        except BaseException:raise Stop('SECURE_INPUT_ABORTED') from None
    if not value:raise Stop('NO_TEMPORARY_CREDENTIAL')
    return value

def exchange(authority,rid,key,run_dir):
    row=authority.plan[rid];directory=run_dir/rid;directory.mkdir(exist_ok=False)
    payload=core.canonical(row['body']).encode();write_new(directory/'payload.json',payload)
    if hashlib.sha256(payload).hexdigest()!=row['payload_hash']:raise Stop('PAYLOAD_BYTES_CHANGED')
    authority.reserve(rid,run_dir.name)
    start=utc();mono=time.monotonic();send=None
    try:
        authority.transport_start(rid)
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect(),urllib.request.HTTPSHandler(context=ssl.create_default_context()))
        request=urllib.request.Request(URLS[row['provider']],data=payload,headers={'Content-Type':'application/json','Authorization':'Bearer '+key},method='POST')
        authority.check_files();authority.check_storage()
        preflight_price(authority.contract,row['provider'],datetime.now(timezone.utc))
        send=utc()
        with opener.open(request,timeout=120) as response:
            status=response.status;data=response.read(2_000_001)
        end=utc();elapsed=time.monotonic()-mono
        if not 200<=status<300 or len(data)>2_000_000:raise Stop('HTTP_OR_BODY_INVALID')
        # No headers are archived. If a server echoes the transient credential,
        # reject without persisting it rather than rewriting measurement fields.
        if key.encode() in data:raise Stop('SENSITIVE_RESPONSE_NOT_ARCHIVED')
        raw_path=directory/'response.json';write_new(raw_path,data)
        timing={'client_start_utc':start,'physical_open_started_utc':send,'response_received_utc':end,'elapsed_monotonic_seconds':elapsed,'http_status':status}
        save(directory/'transport.json',timing)
        return authority.complete_observation(rid,raw_path,timing)
    except BaseException as exc:
        failure_path=directory/'transport_failure.json'
        if not failure_path.exists():
            save(failure_path,{'request_id':rid,'client_start_utc':start,'physical_open_started_utc':send,'failure_observed_utc':utc(),'elapsed_monotonic_seconds':time.monotonic()-mono,'http_status':exc.code if isinstance(exc,urllib.error.HTTPError) and type(exc.code)is int else None,'error_type':type(exc).__name__,'exception_message_retained':False})
        a=authority.attempts().get(authority.attempt_id(rid))
        if a and a['status']=='RESERVED':authority.unknown(rid,'TRANSPORT_ARCHIVE_OR_STORAGE_FAILURE')
        raise

def main(argv=None):
    parser=argparse.ArgumentParser(description='Exact reviewed ten original calibration IDs; no --live or later stages.')
    parser.add_argument('--calibration-review',required=True)
    parser.add_argument('--execute',action='store_true')
    parser.add_argument('--capture-run-id',default=None)
    args=parser.parse_args(argv);authority=None;keys={};run_dir=None;exit_code=2;start=utc()
    try:
        authority=Authority(ROOT,args.calibration_review)
        s=authority.state()
        if s['technical_calibration_completed']:
            print(core.canonical({'phase':s['phase'],'already_complete':True,'new_requests':0,'formal_enabled':False}));return 0
        if not args.execute:
            reserve,row=authority.preflight(s['next_id'])
            print(core.canonical({'phase':s['phase'],'next_id':row['request_id'],'reserve':str(reserve),'totals':{p:authority.totals(p) for p in ('deepseek','openai')},'credentials_loaded':False,'new_requests':0}));return 0
        if not trusted_tty():raise Stop('TRUSTED_LOCAL_TTY_REQUIRED')
        # This directory must be provisioned with reviewed restrictive ACLs by
        # the separately captured local launcher BEFORE this entry is invoked.
        archive=ROOT/'analysis/calibration_v2_runs'
        acl,acl_hash=read(ROOT,'review/CALIBRATION_V2_RAW_DIRECTORY_ACL_20261013.json','review')
        if acl.get('restricted_local_acl_verified')is not True or acl.get('path')!=str(archive.resolve()) or digest(archive/'ACL_VERIFICATION.json')!=acl.get('verification_sha256'):raise Stop('RESTRICTED_RAW_DIRECTORY_REQUIRED')
        run_id=args.capture_run_id
        if run_id is None or not run_id.startswith('CALV2-') or any(ch not in 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-' for ch in run_id):raise Stop('INDEPENDENT_CAPTURE_RUNNER_REQUIRED')
        run_dir=archive/run_id
        if not run_dir.is_dir() or not (run_dir/'capture_launch.json').is_file() or (run_dir/'run_start.json').exists():raise Stop('CAPTURE_LAUNCH_REQUIRED')
        save(run_dir/'run_start.json',{'run_id':run_id,'argv':sys.argv,'pid':os.getpid(),'started_utc':start,'review_sha256':authority.review_hash,'source_hashes':authority.source_hashes,'config_hash':authority.rule['config_hash'],'plan_hash':authority.rule['plan_hash'],'method_sha256':authority.method_hash,'raw_evidence_limits':'Locally writable artifacts and hashes provide correspondence/integrity, not independent authenticity or vendor invoice verification.'})
        authority.initialize()
        while True:
            s=authority.state()
            if s['technical_calibration_completed']:exit_code=0;break
            if s['stopped_reason']:raise Stop(s['stopped_reason'])
            reserve,row=authority.preflight(s['next_id']);provider=row['provider']
            if provider not in keys:keys[provider]=secure_prompt(provider)
            authority.preflight(row['request_id']) # Recheck after human input.
            value=exchange(authority,row['request_id'],keys[provider],run_dir)
            print(core.canonical({'request_id':row['request_id'],'technical_accepted':True,'valuation_status':value['valuation_status'],'cash_estimate':value['lower']['cash'],'input_tokens':value['parsed']['input_tokens'],'output_tokens':value['parsed']['output_tokens'],'formal_enabled':False}),flush=True)
            if not authority.state()['technical_calibration_completed']:time.sleep(60) # Original calibration block cadence; no warming purpose.
        return exit_code
    except (Stop,EvidenceError):
        exit_code=2
        print(core.canonical({'phase':'BLOCKED_OR_STOPPED','error_code':str(sys.exc_info()[1]),'formal_enabled':False}),file=sys.stderr,flush=True);return 2
    except BaseException:
        exit_code=3
        print(core.canonical({'phase':'STOPPED_INTERNAL_OR_INTERRUPTED','details_redacted':True,'formal_enabled':False}),file=sys.stderr,flush=True);return 3
    finally:
        if authority is not None:
            try:
                summary={'ended_utc':utc(),'entry_return_code':exit_code,'totals':{p:authority.totals(p) for p in ('deepseek','openai')},'state':authority.state()}
                if run_dir is not None:save(run_dir/'run_end.json',summary)
            except BaseException:pass
            authority.close()
        for provider in keys:keys[provider]=None
        keys.clear()

if __name__=='__main__':raise SystemExit(main())

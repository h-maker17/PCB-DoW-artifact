"""Explicit original700 DeepSeek formal; OpenAI forbidden."""
from pathlib import Path
from datetime import datetime,timezone
from decimal import Decimal as D
import argparse,json,sqlite3,sys,time,math,ssl,urllib.request,urllib.error,types
# Compile frozen dependencies directly; older cache files cannot define behavior.
for _name in ('calibration_v2_pricing','calibration_v2','pilot_v3_errors'):
    _path=Path(__file__).resolve().parent/(_name+'.py')
    _mod=types.ModuleType(_name);_mod.__file__=str(_path);sys.modules[_name]=_mod
    exec(compile(_path.read_text(encoding='utf-8'),str(_path),'exec'),_mod.__dict__)
c=sys.modules['calibration_v2'];pricing=sys.modules['calibration_v2_pricing'];errors=sys.modules['pilot_v3_errors']
core=c.core;Stop=c.Stop;EvidenceError=pricing.EvidenceError
ROOT=Path(__file__).resolve().parents[1];PRODUCTION=c.PRODUCTION
SCOPE='DEEPSEEK_ORIGINAL700_FORMAL_ONLY';KIND='FORMAL_V4_PRODUCTION_APPROVAL'
SOURCE_NAMES=c.SOURCE_NAMES+('pilot_v3.py','pilot_v3_capture.py','pilot_v3_errors.py','block_statistics.py','formal_v4.py','formal_v4_capture.py','formal_v4_statistics.py')
utc=c.utc;digest=c.digest;save=c.save;read=c.read;chain=c.chain;append=c.append;safe_path=c.safe_path
trusted_tty=c.trusted_tty
class Authority:
    def __init__(self,root,review_path,*,fixture=False):
        self.root=Path(root).resolve();self.fixture=fixture;self.cash=self.workflow=self.lock=None;self.cadence_anchor=None;self.cadence_ready=None
        if fixture:
            if not self.root.is_relative_to(PRODUCTION/'staging/deepseek_formal_v4_20261013/fixtures'):raise Stop('FIXTURE_ROOT_ONLY')
        elif self.root!=PRODUCTION:raise Stop('PRODUCTION_ROOT_REQUIRED')
        self.review,self.review_hash=read(self.root,review_path,'review');self.review_path=safe_path(self.root,review_path,'review');r=self.review
        if r.get('kind')!=KIND or r.get('approved')is not True or r.get('fixture')is not fixture or r.get('scope')!=SCOPE or r.get('new_request_allowance')!=700 or r.get('OpenAI_generation_allowance')!=0 or r.get('paid_later_stages_allowed')is not False:raise Stop('NO_EXACT_FORMAL_PERMISSION')
        if not fixture and (r.get('reviewer')!='/root/calibration_method_independent_review' or r.get('production_review_after_exact_install')is not True):raise Stop('INDEPENDENT_PRODUCTION_REVIEW_REQUIRED')
        self.rule,self.rule_hash=read(self.root,r['rule_path'],'config/runtime_policy')
        for key in ('baseline','contract','method','authorization','statistics','schedule','original_schedule'):
            obj,h=read(self.root,self.rule[key+'_path'],'config/runtime_policy_evidence');setattr(self,key,obj);setattr(self,key+'_hash',h)
            if r.get(key+'_sha256')!=h:raise Stop('REVIEW_EVIDENCE_BINDING')
        if r.get('rule_sha256')!=self.rule_hash:raise Stop('REVIEW_RULE_BINDING')
        self.config,self.config_raw_hash=read(self.root,self.rule['config_path'],'config');plan,self.plan_raw_hash=read(self.root,self.rule['plan_path'],'config')
        self.plan={p['request_id']:p for p in plan};self.ids=tuple(self.rule['request_ids'])
        expected=[p['request_id'] for p in plan if p['provider']=='deepseek' and p['phase']=='formal']
        if len(self.plan)!=len(plan) or len(expected)!=700 or list(self.ids)!=expected or self.schedule['request_ids']!=expected or [rid for rid in self.original_schedule['request_ids'] if self.plan[rid]['provider']=='deepseek' and self.plan[rid]['phase']=='formal']!=expected:raise Stop('EXACT_FORMAL_PLAN_SCOPE')
        if core.sha(self.config)!=self.rule['config_hash'] or core.sha(plan)!=self.rule['plan_hash']:raise Stop('FROZEN_PLAN_CHANGED')
        if self.rule.get('scope')!=SCOPE or self.rule.get('new_request_allowance')!=700 or self.rule.get('OpenAI_generation_allowance')!=0 or self.rule.get('formal_allowance')!=700 or self.rule.get('retries')!=0 or self.rule.get('later_stages_allowed')is not False:raise Stop('RULE_SCOPE')
        if self.method.get('DeepSeek_formal_enabled')is not True or self.method.get('OpenAI_formal_enabled')is not False or self.method.get('technical_calibration_completed')is not False or self.method.get('DS_formal_technical_admission_only')is not True or self.method.get('applies_to')!=list(self.ids) or self.authorization.get('allowed_ids')!=list(self.ids):raise Stop('METHOD_AUTH_SCOPE')
        if self.rule.get('output_upper')!=64 or self.rule.get('input_acceptance_upper')!=2500 or self.statistics.get('bootstrap_resamples')!=10000 or self.statistics.get('seed')!=20261012 or self.statistics.get('primary',{}).get('required_blocks')!=[1,2,3,4,5]:raise Stop('FORMAL_LIMITS_STATISTICS')
        for p in plan:
            if core.sha(p['body'])!=p['payload_hash']:raise Stop('PAYLOAD_HASH_CHANGED')
        for rid in self.ids:
            p=self.plan[rid]
            if self.rule['request_payloads'].get(rid)!=p['payload_hash'] or p['provider']!='deepseek' or p['phase']!='formal' or p['body']['model']!=self.config['providers']['deepseek']['candidate_model'] or p['body'].get('max_tokens')!=64:raise Stop('PILOT_PAYLOAD_CONTRACT')
        for n in (5,10,20):
            for repeat in range(1,6):
                for arm in ('baseline','frontzw','frontmeta','end'):
                    group=[self.plan[rid] for rid in self.ids if self.plan[rid]['N']==n and self.plan[rid]['repeat']==repeat and self.plan[rid]['arm']==arm]
                    if [p['replica'] for p in group]!=list(range(1,n+1)):raise Stop('FORMAL_DESIGN_700_CHANGED')
        for provider,currency in (('deepseek','CNY'),('openai','USD')):
            p=self.config['providers'][provider]
            if p['currency']!=currency or D(str(p['dispatch_stop']))!=27 or D(str(p['hard_cap']))!=30 or p['fee_surcharge_multiplier']!=1:raise Stop('PROJECT_BUDGET_CHANGED')
        expected_cadence={'concurrency':1,'min_seconds_after_response':10,'arm_gap_seconds':30,'block_gap_seconds':60,'timeout_seconds':120,'retries':0}
        if self.config['cadence']!=expected_cadence or self.method['cadence']!=expected_cadence:raise Stop('CADENCE_CHANGED')
        b=self.baseline['databases']['project_cash.sqlite']['meta']
        if b!={'authority_id':self.rule['authority_id'],'config':self.rule['config_hash'],'plan':self.rule['plan_hash'],'session_id':self.config['session_id']}:raise Stop('AUTHORITY_SESSION_BINDING')
        self.source_hashes=self.rule['source_hashes']
        if r.get('source_hashes')!=self.source_hashes or set(self.source_hashes)!=set(SOURCE_NAMES):raise Stop('SOURCE_REVIEW_BINDING')
        if self.contract.get('public_contract_validated')is not True or self.contract.get('invoice_time_source_proven')is not False:raise Stop('PUBLIC_CONTRACT_REQUIRED')
        self.check_files();self.validate_admission()
        if not fixture:
            acl,ah=read(self.root,'review/FORMAL_V4_RAW_DIRECTORY_ACL_20261013.json','review');archive=self.root/'analysis/formal_v4_runs'
            if r.get('raw_directory_acl_record_sha256')!=ah or acl.get('restricted_local_acl_verified')is not True or acl.get('path')!=str(archive.resolve()) or digest(archive/'ACL_VERIFICATION.json')!=acl.get('verification_sha256'):raise Stop('RAW_DIRECTORY_ACL_BINDING')
        try:
            for n in ('project_cash.sqlite','runtime_workflow.sqlite'):
                p=self.root/'logs'/n
                if not p.is_file() or not p.stat().st_size:raise Stop('EXISTING_LEDGER_REQUIRED')
            self.lock=(self.root/'logs/calibration_v2.execution.lock').open('a+b')
            if self.lock.tell()==0:self.lock.write(b'0');self.lock.flush()
            self.lock.seek(0)
            try:
                if sys.platform=='win32':
                    import msvcrt;msvcrt.locking(self.lock.fileno(),msvcrt.LK_NBLCK,1)
                else:
                    import fcntl;fcntl.flock(self.lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            except OSError:raise Stop('OTHER_PAID_PROCESS_ACTIVE') from None
            self.cash=sqlite3.connect(self.root/'logs/project_cash.sqlite',isolation_level=None,timeout=5);self.workflow=sqlite3.connect(self.root/'logs/runtime_workflow.sqlite',isolation_level=None,timeout=5)
            for db in (self.cash,self.workflow):db.execute('PRAGMA synchronous=FULL')
            self.check_storage();self.state()
        except BaseException:self.close();raise
    def check_files(self):
        if digest(self.review_path)!=self.review_hash:raise Stop('APPROVAL_CHANGED')
        for n,h in self.source_hashes.items():
            if digest(self.root/'code'/n)!=h:raise Stop('SOURCE_CHANGED')
        files={self.review['rule_path']:self.rule_hash,self.rule['config_path']:self.config_raw_hash,self.rule['plan_path']:self.plan_raw_hash}
        for k in ('baseline','contract','method','authorization','statistics','schedule','original_schedule'):files[self.rule[k+'_path']]=getattr(self,k+'_hash')
        files.update(self.rule['evidence_source_hashes']);files.update(self.baseline['prior_bound_files'])
        for rel,h in files.items():
            p=(self.root/rel).resolve()
            if not p.is_relative_to(self.root) or digest(p)!=h:raise Stop('BOUND_EVIDENCE_CHANGED')
    def validate_admission(self):
        rs=self.baseline['DS_receipts'];os=self.baseline['DS_observations']
        expected=[p['request_id'] for p in self.plan.values() if p['provider']=='deepseek' and p['phase']=='calibration'][1:]
        if len(rs)!=5 or len(os)!=5 or [r['data']['request_id'] for r in rs]!=expected or [r['data']['request_id'] for r in os]!=expected:raise Stop('DS_TECHNICAL_ADMISSION_MISSING')
        wf=self.baseline['databases']['runtime_workflow.sqlite']['rows']
        for receipt,observation in zip(rs,os):
            if not any(h==receipt['hash'] and json.loads(t)==receipt['data'] for _,k,t,_,h in wf) or not any(h==observation['hash'] and json.loads(t)==observation['data'] for _,k,t,_,h in wf):raise Stop('DS_RECEIPT_BASELINE_LINK')
            o=observation['data'];r=receipt['data'];row=self.plan[o['request_id']];path=safe_path(self.root,o['response_path'],'analysis')
            if digest(path)!=o['raw_sha256'] or o['payload_hash']!=row['payload_hash']:raise Stop('DS_PRIOR_RAW_LINK')
            value=pricing.value_response(c.raw_json(path),'deepseek',row['body']['model'],self.contract,o['timing'])
            if not value['technical_accepted'] or value!=r['value'] or r['technical_accepted']is not True:raise Stop('DS_PRIOR_TECHNICAL_INVALID')
        h=self.baseline['history_limited_adoption'];d=h['data']
        if not any(v==h['hash'] and json.loads(t)==d for _,k,t,_,v in wf) or d.get('historical_clock_criterion')!='FAILED' or d.get('cache_initial_state')!='UNKNOWN' or d.get('historical_full_raw_body_available')is not False or d.get('new_charge')is not False:raise Stop('HISTORICAL_LIMITED_BOUNDARY_CHANGED')
        if self.baseline.get('prior_global_technical_completion')is not False:raise Stop('GLOBAL_COMPLETION_FALSE_REQUIRED')
        ps=self.baseline['pilot_receipts'];po=self.baseline['pilot_observations'];pids=[p['request_id'] for p in self.plan.values() if p['provider']=='deepseek' and p['phase']=='pilot']
        if len(ps)!=20 or len(po)!=20 or [p['data']['request_id'] for p in ps]!=pids or [p['data']['request_id'] for p in po]!=pids:raise Stop('ORIGINAL_PILOT_INCOMPLETE')
        for r,o in zip(ps,po):
            for item in (r,o):
                if not any(h==item['hash'] and json.loads(t)==item['data'] for _,k,t,_,h in wf):raise Stop('PILOT_PREFIX_LINK')
            d=o['data'];path=safe_path(self.root,d['response_path'],'analysis');v=pricing.value_response(c.raw_json(path),'deepseek',self.plan[d['request_id']]['body']['model'],self.contract,d['timing'])
            if digest(path)!=d['raw_sha256'] or not v['technical_accepted'] or r['data']['value']!=v:raise Stop('PILOT_RAW_TECHNICAL_INVALID')
    def check_storage(self):
        for n,db in (('project_cash.sqlite',self.cash),('runtime_workflow.sqlite',self.workflow)):
            c.prefix(chain(db),self.baseline['databases'][n]['rows'])
            if core.canonical(db.execute('SELECT * FROM meta ORDER BY id').fetchall())!=core.canonical(self.baseline['databases'][n]['meta_rows']):raise Stop('META_CHANGED')
        for rel,h in self.baseline['anchor_hashes'].items():
            if digest(self.root/rel)!=h:raise Stop('ANCHOR_CHANGED')
    def attempts(self):
        attempts={}
        for _,kind,text,_,h in chain(self.cash):
            d=json.loads(text)
            if kind=='RESERVE':attempts[d['attempt']]={**d,'status':'RESERVED','transport_consumed':False}
            elif kind=='TRANSPORT_START':attempts[d['attempt']]['transport_consumed']=True
            elif kind=='UNKNOWN':attempts[d['attempt']]['status']='UNKNOWN'
            elif kind in ('SETTLE','EXTERNAL_BILL_RECONCILIATION'):attempts[d['attempt']].update(d);attempts[d['attempt']]['status']='SETTLED' if kind=='SETTLE' else 'EXTERNALLY_RECONCILED'
        return attempts
    def totals(self,provider):
        confirmed=estimated=pending=D(0)
        for a in self.attempts().values():
            if a['provider']!=provider:continue
            if a['status']=='EXTERNALLY_RECONCILED':confirmed+=D(a['cash'])
            elif a['status']=='SETTLED':estimated+=D(a['cash'])
            else:pending+=D(a['reserve'])
        return {'external_reconciled':str(confirmed),'usage_priced_estimate':str(estimated),'unknown_or_unsettled_reserve':str(pending),'budget_exposure':str(confirmed+estimated+pending)}
    def formal_spent(self):return sum((D(a['cash']) for aid,a in self.attempts().items() if aid in {self.attempt_id(rid) for rid in self.ids} and a['status']=='SETTLED'),D(0))
    def state(self):
        self.check_files();self.check_storage();base=len(self.baseline['databases']['runtime_workflow.sqlite']['rows']);migrated=admitted=complete=False;stopped=None;observations={};receipts={}
        existing=self.attempts();interruptions=[]
        for _,kind,text,_,h in chain(self.workflow)[base:]:
            d=json.loads(text)
            if kind=='SOURCE_MIGRATION':
                if migrated or d.get('scope')!=SCOPE or d.get('review_sha256')!=self.review_hash or d.get('source_hashes')!=self.source_hashes:raise Stop('MIGRATION_SCOPE_CHANGED')
                migrated=True
            elif kind=='FORMAL_V4_ADMISSION':
                if not migrated or admitted or d.get('receipt_hashes')!=[r['hash'] for r in self.baseline['DS_receipts']] or d.get('pilot_receipt_hashes')!=[r['hash'] for r in self.baseline['pilot_receipts']] or d.get('historical_limited_hash')!=self.baseline['history_limited_adoption']['hash'] or d.get('global_technical_completed')is not False or d.get('OpenAI_generation_allowance')!=0 or d.get('DeepSeek_formal_enabled')is not True:raise Stop('ADMISSION_CHANGED')
                admitted=True
            elif kind=='FORMAL_V4_OBSERVATION':
                rid=d['request_id']
                if not admitted or rid not in self.ids or rid in observations or rid!=self.ids[len(receipts)]:raise Stop('OBSERVATION_ORDER_REPLAY')
                observations[rid]=d
            elif kind=='FORMAL_V4_RECEIPT':
                rid=d['request_id']
                if rid in receipts or rid not in observations or rid!=self.ids[len(receipts)] or d.get('technical_accepted')is not True:raise Stop('RECEIPT_ORDER_REPLAY')
                self.verify_receipt(d,observations[rid],existing);receipts[rid]=d
            elif kind=='FORMAL_V4_STOP':stopped=d['reason']
            elif kind=='FORMAL_V4_INTERRUPTION':
                if d.get('recoverable')is not True:stopped=d['reason']
                interruptions.append(d)
            elif kind in ('FORMAL_V4_PLANNED_PAUSE','FORMAL_V4_PLANNED_RESUME'):
                if d.get('request_id') not in self.ids or d.get('reason')!='PREDECLARED_PRICE_BOUNDARY':raise Stop('PLANNED_PAUSE_SCOPE')
            elif kind=='FORMAL_V4_COMPLETE':
                if complete or stopped or not admitted or len(receipts)!=700 or d.get('OpenAI_formal_enabled')is not False or d.get('global_technical_completed')is not False or d.get('receipt_digest')!=core.sha([receipts[rid] for rid in self.ids]) or d.get('statistics_sha256')!=self.statistics_hash or d.get('schedule_sha256')!=self.schedule_hash:raise Stop('COMPLETION_INVARIANT')
                complete=True
            else:raise Stop('UNAUTHORIZED_WORKFLOW_EVENT')
        for aid,a in self.baseline['old_attempts'].items():
            if existing.get(aid)!=a:raise Stop('OLD_ATTEMPT_CHANGED')
        for _,kind,text,_,h in chain(self.cash)[len(self.baseline['databases']['project_cash.sqlite']['rows']):]:
            d=json.loads(text)
            if kind not in ('RESERVE','TRANSPORT_START','SETTLE','UNKNOWN','PAUSE'):raise Stop('UNAUTHORIZED_CASH_EVENT')
            if kind=='PAUSE':
                if d.get('provider')!='deepseek':raise Stop('NON_DS_EVENT')
                stopped=stopped or d.get('reason')
            elif d.get('attempt') not in {self.attempt_id(rid) for rid in self.ids}:raise Stop('UNAUTHORIZED_ATTEMPT')
        for rid in self.ids:
            a=existing.get(self.attempt_id(rid))
            if a is None:continue
            if a['status']=='UNKNOWN':stopped=stopped or 'NEW_UNKNOWN'
            elif a['status']=='RESERVED':stopped=stopped or 'UNRESOLVED_ATTEMPT_NO_REPLAY'
            elif a['status']=='SETTLED' and rid not in receipts:stopped=stopped or 'SETTLED_WITHOUT_RECEIPT'
        return {'phase':'STOPPED' if stopped else 'FORMAL_V4_COMPLETE' if complete else 'FORMAL_V4_READY','migrated':migrated,'admitted':admitted,'formal_completed':complete,'technical_calibration_completed':False,'DeepSeek_formal_enabled':not stopped and not complete,'OpenAI_formal_enabled':False,'I_group_complete':False,'OpenAI_generation_allowance':0,'receipts':receipts,'observations':observations,'stopped_reason':stopped,'next_id':None if stopped or complete or len(receipts)==700 else self.ids[len(receipts)],'unconsumed_ids':700-len([rid for rid in self.ids if self.attempt_id(rid) in existing]),'recoverable_interruptions':interruptions}
    def verify_receipt(self,r,o,attempts=None):
        rid=r['request_id'];path=safe_path(self.root,o['response_path'],'analysis/formal_v4_runs')
        if digest(path)!=o['raw_sha256'] or r['payload_hash']!=self.plan[rid]['payload_hash']:raise Stop('RAW_PAYLOAD_CHANGED')
        v=pricing.value_response(c.raw_json(path),'deepseek',self.plan[rid]['body']['model'],self.contract,o['timing']);a=(attempts if attempts is not None else self.attempts()).get(self.attempt_id(rid))
        if not v['technical_accepted'] or r['value']!=v or not a or a['status']!='SETTLED' or a.get('valuation_status')!='USAGE_PRICED_ESTIMATE' or any(a[k]!=v['lower'][k] for k in ('cash','input_cost','output_cost')):raise Stop('RECEIPT_CASH_INVALID')
    def attempt_id(self,rid):return self.config['session_id']+'|'+rid
    def initialize(self):
        s=self.state()
        if not s['migrated']:append(self.workflow,'SOURCE_MIGRATION',{'scope':SCOPE,'review_sha256':self.review_hash,'source_hashes':self.source_hashes,'rule_sha256':self.rule_hash,'method_sha256':self.method_hash,'origin_workflow_tail':self.baseline['databases']['runtime_workflow.sqlite']['rows'][-1][4],'cash_prefix_tail':self.baseline['databases']['project_cash.sqlite']['rows'][-1][4],'old_STOP_NOT_ERASED':True})
        if not self.state()['admitted']:append(self.workflow,'FORMAL_V4_ADMISSION',{'receipt_hashes':[r['hash'] for r in self.baseline['DS_receipts']],'pilot_receipt_hashes':[r['hash'] for r in self.baseline['pilot_receipts']],'historical_limited_hash':self.baseline['history_limited_adoption']['hash'],'method_sha256':self.method_hash,'global_technical_completed':False,'OpenAI_generation_allowance':0,'DeepSeek_formal_enabled':True,'OpenAI_formal_enabled':False})
        self.finalize_checkpoint()
    def finalize_checkpoint(self):
        s=self.state()
        if not s['stopped_reason'] and not s['formal_completed'] and len(s['receipts'])==700:
            append(self.workflow,'FORMAL_V4_COMPLETE',{'new_accepted_observations':700,'global_technical_completed':False,'OpenAI_formal_enabled':False,'OpenAI_generation_allowance':0,'receipt_digest':core.sha([s['receipts'][r] for r in self.ids]),'method_sha256':self.method_hash,'statistics_sha256':self.statistics_hash,'schedule_sha256':self.schedule_hash,'pure_completion_checkpoint':True})
    def preflight(self,rid,now=None):
        if not self.fixture and now is not None:raise Stop('TEST_CLOCK_NOT_PRODUCTION')
        if rid not in self.ids:raise Stop('SCOPE_CURSOR_OR_STOP')
        s=self.state()
        if s['stopped_reason'] or rid not in self.ids or s['next_id']!=rid:raise Stop('SCOPE_CURSOR_OR_STOP')
        row=self.plan[rid]
        if row['provider']!='deepseek' or row['phase']!='formal':raise Stop('ONLY_DS_PILOT')
        if self.attempt_id(rid) in self.attempts():raise Stop('CONSUMED_ID_NO_REPLAY')
        reserve=pricing.worst_reserve(self.contract,'deepseek')
        if D(self.totals('deepseek')['budget_exposure'])+reserve>D(27):raise Stop('NEXT_RESERVE_EXCEEDS_STOP')
        pricing.preflight_price(self.contract,'deepseek',now or datetime.now(timezone.utc))
        return reserve,row
    def wait_cadence(self,rid,sleep=None,mono=None,wall=None):
        if not self.fixture and any(v is not None for v in (sleep,mono,wall)):raise Stop('TEST_CLOCK_NOT_PRODUCTION')
        sleep=sleep or time.sleep;mono=mono or time.monotonic;wall=wall or (lambda:datetime.now(timezone.utc))
        s=self.state();i=len(s['receipts']);row=self.plan[rid];previous=self.plan[self.ids[i-1]] if i else None
        gap=60 if previous is None or previous['block_id']!=row['block_id'] else 10 if previous['arm']==row['arm'] else 30
        before_wall=wall();before_mono=mono();last=self.baseline['last_DS_response_utc'] if i==0 else s['observations'][self.ids[i-1]]['timing']['response_received_utc']
        if before_wall<datetime.fromisoformat(last) or not math.isfinite(before_mono):raise Stop('CADENCE_CLOCK_INVALID')
        if self.cadence_anchor is None:remaining=gap
        else:
            aid,aw,am=self.cadence_anchor;elapsed=before_mono-am;we=(before_wall-aw).total_seconds()
            if aid!=self.ids[i-1] or elapsed<0 or we<0 or abs(we-elapsed)>1:raise Stop('CADENCE_WALL_MONOTONIC_MISMATCH')
            remaining=max(0,gap-elapsed)
        sleep(remaining);after_mono=mono();after_wall=wall();waited=after_mono-before_mono;ww=(after_wall-before_wall).total_seconds()
        if not math.isfinite(waited) or waited<remaining or ww<0 or abs(ww-waited)>1:raise Stop('CADENCE_SLEEP_OR_CLOCK_MISMATCH')
        record={'gap_seconds':gap,'remaining_seconds':remaining,'waited_monotonic_seconds':waited,'started_utc':before_wall.isoformat(),'ended_utc':after_wall.isoformat(),'adaptive':False}
        self.cadence_ready=(rid,record)
        return record
    def reserve(self,rid,run_id,now=None):
        reserve,row=self.preflight(rid,now)
        if not self.state()['admitted']:raise Stop('PILOT_ADMISSION_REQUIRED')
        if self.cadence_ready is None or self.cadence_ready[0]!=rid:raise Stop('CADENCE_WAIT_REQUIRED')
        append(self.cash,'RESERVE',{'attempt':self.attempt_id(rid),'request_id':rid,'session_id':self.config['session_id'],'provider':'deepseek','currency':'CNY','phase':'formal','payload_hash':row['payload_hash'],'reserve':str(reserve),'run_id':run_id,'scope':SCOPE,'review_sha256':self.review_hash})
        self.cadence_ready=None
    def transport_start(self,rid):
        self.check_files();self.check_storage();a=self.attempts().get(self.attempt_id(rid))
        if rid not in self.ids or not a or a['status']!='RESERVED' or a.get('transport_consumed'):raise Stop('TRANSPORT_SINGLE_USE_DENIED')
        append(self.cash,'TRANSPORT_START',{'attempt':self.attempt_id(rid),'review_sha256':self.review_hash})
    def stop(self,reason,rid=None):
        append(self.cash,'PAUSE',{'provider':'deepseek','reason':reason});append(self.workflow,'FORMAL_V4_STOP',{'request_id':rid,'reason':reason,'formal_enabled':False})
    def interrupted(self):
        s=self.state();recoverable=not s['stopped_reason'] and not s['formal_completed']
        append(self.workflow,'FORMAL_V4_INTERRUPTION',{'reason':'ORDERLY_OR_CAPTURE_INTERRUPTION','recoverable':recoverable,'next_unconsumed_id':s['next_id'],'no_attempt_replayed':True})
    def prepare_next(self,rid):
        while True:
            try:return self.preflight(rid)
            except EvidenceError as exc:
                if str(exc)!='PRICE_WINDOW_NOT_UNIQUE':raise
                append(self.workflow,'FORMAL_V4_PLANNED_PAUSE',{'request_id':rid,'reason':'PREDECLARED_PRICE_BOUNDARY','effect_information_used':False})
                start=datetime.now(timezone.utc);mono=time.monotonic()
                time.sleep(30)
                if abs((datetime.now(timezone.utc)-start).total_seconds()-(time.monotonic()-mono))>1:raise Stop('PLANNED_PAUSE_CLOCK_ANOMALY')
                append(self.workflow,'FORMAL_V4_PLANNED_RESUME',{'request_id':rid,'reason':'PREDECLARED_PRICE_BOUNDARY','effect_information_used':False})
    def unknown(self,rid,reason):
        a=self.attempts().get(self.attempt_id(rid))
        if a and a['status']=='RESERVED':append(self.cash,'UNKNOWN',{'attempt':self.attempt_id(rid),'reason':reason})
        self.stop(reason,rid)
    def complete_observation(self,rid,raw_path,timing):
        self.check_files();self.check_storage();a=self.attempts().get(self.attempt_id(rid));row=self.plan[rid]
        if rid not in self.ids or not a or a['status']!='RESERVED' or not a['transport_consumed']:raise Stop('NO_DURABLE_SENT_ATTEMPT')
        path=Path(raw_path).resolve()
        if not path.is_relative_to((self.root/'analysis/formal_v4_runs').resolve()):raise Stop('RAW_ARCHIVE_PATH')
        raw=c.raw_json(path);o={'request_id':rid,'attempt_id':self.attempt_id(rid),'payload_hash':row['payload_hash'],'response_path':str(path.relative_to(self.root)),'raw_sha256':digest(path),'timing':timing,'provider_response_id':raw.get('id'),'provider_created':raw.get('created'),'raw_usage':raw.get('usage'),'model':raw.get('model'),'measurement_fields_not_rewritten':True}
        append(self.workflow,'FORMAL_V4_OBSERVATION',o)
        try:v=pricing.value_response(raw,'deepseek',row['body']['model'],self.contract,timing)
        except (EvidenceError,KeyError,TypeError,ValueError) as exc:self.unknown(rid,str(exc) if isinstance(exc,EvidenceError) else 'RESPONSE_ANALYSIS_INVALID');raise Stop('RESPONSE_INVALID_RESERVE_RETAINED') from None
        save(path.with_name('analysis.json'),{'request_id':rid,'raw_sha256':digest(path),'value':v})
        if v['valuation_status']!='USAGE_PRICED_ESTIMATE':self.unknown(rid,'PRICE_NOT_UNIQUELY_VALUED');raise Stop('PRICE_INTERVAL_RESERVE_RETAINED')
        measured=v['lower'];breach=not v['technical_accepted'] or D(measured['cash'])>D(a['reserve'])
        append(self.cash,'SETTLE',{'attempt':self.attempt_id(rid),**measured,'input_tokens':v['parsed']['input_tokens'],'output_tokens':v['parsed']['output_tokens'],'bound_breach':breach,'valuation_status':'USAGE_PRICED_ESTIMATE','invoice_verified':False,'price_basis':v['price_basis'],'raw_sha256':digest(path),'method_sha256':self.method_hash})
        if breach:self.stop('FORMAL_ACCEPTANCE_OR_RESERVE_BREACH',rid);raise Stop('MEASUREMENT_BREACH_ACCOUNTED_STOP')
        append(self.workflow,'FORMAL_V4_RECEIPT',{'request_id':rid,'attempt_id':self.attempt_id(rid),'payload_hash':row['payload_hash'],'raw_sha256':digest(path),'value':v,'technical_accepted':True,'formal_effect_observation':True})
        self.cadence_anchor=(rid,datetime.now(timezone.utc),time.monotonic())
        s=self.state()
        if len(s['receipts'])==700:self.finalize_checkpoint()
        return v
    def close(self):
        for db in (self.cash,self.workflow):
            if db is not None:db.close()
        self.cash=self.workflow=None
        if self.lock is not None:self.lock.close();self.lock=None

def exchange(authority,rid,key,run_dir,cadence):
    if authority.cadence_ready is None or authority.cadence_ready!=(rid,cadence):raise Stop('CADENCE_RECORD_MISMATCH')
    row=authority.plan[rid];directory=run_dir/rid;directory.mkdir(exist_ok=False);payload=core.canonical(row['body']).encode();c.write_new(directory/'payload.json',payload);save(directory/'cadence.json',cadence)
    if digest(directory/'payload.json')!=row['payload_hash']:raise Stop('PAYLOAD_BYTES_CHANGED')
    authority.reserve(rid,run_dir.name);start=utc();mono=time.monotonic();send=None;phase='TRANSPORT_MARK'
    try:
        authority.transport_start(rid)
        phase='SSL_CONTEXT_SETUP';context=ssl.create_default_context()
        phase='HTTP_OPENER_SETUP';opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),c.NoRedirect(),urllib.request.HTTPSHandler(context=context))
        phase='REQUEST_BUILD'
        request=urllib.request.Request('https://api.deepseek.com/chat/completions',data=payload,headers={'Content-Type':'application/json','Authorization':'Bearer '+key},method='POST')
        phase='PHYSICAL_GATE';authority.check_files();authority.check_storage();pricing.preflight_price(authority.contract,'deepseek',datetime.now(timezone.utc));phase='HTTPS_OPEN';send=utc()
        with opener.open(request,timeout=120) as response:
            status=response.status;phase='BODY_READ';data=response.read(2_000_001)
        end=utc();elapsed=time.monotonic()-mono;phase='RAW_ARCHIVE'
        if not 200<=status<300 or len(data)>2_000_000:raise Stop('HTTP_OR_BODY_INVALID')
        if key.encode() in data:raise Stop('SENSITIVE_RESPONSE_NOT_ARCHIVED')
        raw_path=directory/'response.json';c.write_new(raw_path,data);timing={'client_start_utc':start,'physical_open_started_utc':send,'response_received_utc':end,'elapsed_monotonic_seconds':elapsed,'http_status':status};save(directory/'transport.json',timing)
        phase='RESPONSE_ANALYSIS';return authority.complete_observation(rid,raw_path,timing)
    except BaseException as exc:
        failure=directory/'transport_failure.json'
        if not failure.exists():save(failure,{'request_id':rid,'client_start_utc':start,'physical_open_started_utc':send,'failure_observed_utc':utc(),'elapsed_monotonic_seconds':time.monotonic()-mono,**errors.safe_error(exc,phase)})
        a=authority.attempts().get(authority.attempt_id(rid))
        if a and a['status']=='RESERVED':authority.unknown(rid,'TRANSPORT_ARCHIVE_OR_STORAGE_FAILURE')
        raise
def main(argv=None):
    parser=argparse.ArgumentParser(description='Explicit original700 DeepSeek formal IDs; OpenAI and extra requests denied')
    parser.add_argument('--formal-review',required=True);parser.add_argument('--execute',action='store_true');parser.add_argument('--capture-run-id')
    args=parser.parse_args(argv);authority=None;key=None;run_dir=None;exit_code=2
    try:
        authority=Authority(ROOT,args.formal_review);s=authority.state()
        if s['formal_completed']:print(core.canonical({'phase':s['phase'],'already_complete':True,'new_requests':0,'formal_enabled':False}));return 0
        if not args.execute:
            reserve,row=authority.preflight(s['next_id']);print(core.canonical({'phase':s['phase'],'next_id':row['request_id'],'reserve':str(reserve),'totals':{p:authority.totals(p) for p in ('deepseek','openai')},'new_requests':0,'credential_loaded':False}));return 0
        if not trusted_tty():raise Stop('TRUSTED_LOCAL_TTY_REQUIRED')
        run_id=args.capture_run_id
        if not run_id or not run_id.startswith('FORMALV4-') or any(ch not in 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-' for ch in run_id):raise Stop('CAPTURE_RUNNER_REQUIRED')
        run_dir=ROOT/'analysis/formal_v4_runs'/run_id
        if not run_dir.is_dir() or not (run_dir/'capture_launch.json').exists() or (run_dir/'run_start.json').exists():raise Stop('CAPTURE_LAUNCH_REQUIRED')
        save(run_dir/'run_start.json',{'run_id':run_id,'pid':__import__('os').getpid(),'started_utc':utc(),'argv':sys.argv,'source_hashes':authority.source_hashes,'review_sha256':authority.review_hash,'config_hash':authority.rule['config_hash'],'plan_hash':authority.rule['plan_hash'],'method_sha256':authority.method_hash,'statistics_sha256':authority.statistics_hash,'schedule_sha256':authority.schedule_hash,'input_capture':False})
        authority.initialize()
        while True:
            s=authority.state()
            if s['formal_completed']:exit_code=0;break
            reserve,row=authority.prepare_next(s['next_id']);cadence=authority.wait_cadence(row['request_id'])
            authority.prepare_next(row['request_id'])
            if key is None:key=c.secure_prompt('deepseek')
            authority.prepare_next(row['request_id']);v=exchange(authority,row['request_id'],key,run_dir,cadence)
            print(core.canonical({'request_id':row['request_id'],'technical_accepted':True,'input_tokens':v['parsed']['input_tokens'],'output_tokens':v['parsed']['output_tokens'],'cash_estimate':v['lower']['cash'],'valuation_status':v['valuation_status'],'DeepSeek_formal_only':True,'OpenAI_formal_enabled':False}),flush=True)
        return exit_code
    except BaseException as exc:
        exit_code=2 if isinstance(exc,(Stop,EvidenceError)) else 3
        print(core.canonical({'phase':'STOPPED','known_gate_code':str(exc) if isinstance(exc,(Stop,EvidenceError)) else None,**errors.safe_error(exc,'ENTRY'),'formal_enabled':False}),file=sys.stderr,flush=True)
        if authority is not None and run_dir is not None:
            try:
                if isinstance(exc,KeyboardInterrupt):authority.interrupted()
                elif not authority.state()['stopped_reason']:authority.stop(str(exc) if isinstance(exc,(Stop,EvidenceError)) else 'ENTRY_STORAGE_OR_INTERNAL_EXCEPTION')
            except BaseException:pass
        return exit_code
    finally:
        if authority is not None:
            try:
                if run_dir is not None:save(run_dir/'run_end.json',{'ended_utc':utc(),'entry_return_code':exit_code,'state':authority.state(),'totals':{p:authority.totals(p) for p in ('deepseek','openai')}})
            except BaseException:pass
            authority.close()
        key=None
if __name__=='__main__':raise SystemExit(main())

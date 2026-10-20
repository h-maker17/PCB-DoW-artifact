"""Bound runtime coordinator for the candidate entry and physical HTTP path.
No credentials are read here; commercial evidence remains mandatory, never inferred.
"""
from pathlib import Path
from datetime import datetime,timezone,timedelta
import copy,hashlib,json,sqlite3,math,time
import protocol_core as core
import bootstrap_policy as bootstrap
import http_adapters as http
import calibration_recovery as recovery_module
import external_reconciliation as external_module
from offline_coordinator import build_schedule,derive_technical_stages
class WorkflowError(core.GateError): pass
def _fail(code): raise WorkflowError(code)
SOURCE_DIR=Path(__file__).resolve().parent
SOURCE_NAMES=('protocol_core.py','http_adapters.py','user_entry.py','runtime_gate.py','offline_coordinator.py','offline_prepare.py','bootstrap_policy.py','calibration_recovery.py','external_reconciliation.py')
def _source_hashes():
    return {n:hashlib.sha256((SOURCE_DIR/n).read_bytes()).hexdigest() for n in SOURCE_NAMES}
def safe_json(relative,folder):
    p=(core.ROOT/relative).resolve(); base=(core.ROOT/folder).resolve()
    if not p.is_relative_to(base) or p.suffix!='.json' or any(x in p.name.lower() for x in ('api_key','apikey','secret','credential')): _fail('UNAPPROVED_POLICY_OR_REVIEW_PATH')
    raw=p.read_bytes(); return json.loads(raw),hashlib.sha256(raw).hexdigest()
def validate_design(config,plan):
    if config.get('N')!=[5,10,20] or config.get('repeats')!=5 or config.get('arms')!=['baseline','frontzw','frontmeta','end'] or config.get('primary',{}).get('N')!=20 or config.get('primary',{}).get('attack')!='frontmeta' or config.get('primary',{}).get('estimand')!='all-assigned ratio of sums of chain input costs': _fail('FORMAL_RESEARCH_DESIGN_DRIFT')
    counts={(p,phase):sum(r['provider']==p and r['phase']==phase for r in plan) for p,phase in (('deepseek','formal'),('openai','formal'),('deepseek','calibration'),('openai','calibration'),('deepseek','pilot'),('openai','shape'))}
    if counts!={('deepseek','formal'):700,('openai','formal'):700,('deepseek','calibration'):6,('openai','calibration'):5,('deepseek','pilot'):20,('openai','shape'):8} or len(plan)!=1439: _fail('EXACT_CURRENT_PLAN_INVENTORY_REQUIRED')
    if {p:x['candidate_model'] for p,x in config['providers'].items()}!={'openai':'gpt-6-astra','deepseek':'deepseek-v4-pro'}: _fail('STUDY_MODEL_DRIFT')
def evidence_source_hashes(config):
    if bootstrap.enabled(config): return bootstrap.sources(config)
    return evidence_source_hashes_legacy(config)
def evidence_source_hashes_legacy(config):
    sources=config.get('runtime_evidence_source_hashes')
    if not isinstance(sources,dict) or not sources: _fail('NATIVE_EVIDENCE_SOURCE_MANIFEST_REQUIRED')
    native=config['providers']['deepseek'].get('native_reference_path')
    if native not in sources: _fail('FINAL_DEEPSEEK_NATIVE_REFERENCE_REQUIRED')
    actual={}
    for relative,expected in sources.items():
        path=(core.ROOT/relative).resolve()
        if not any(path.is_relative_to((core.ROOT/base).resolve()) for base in ('analysis','config/token_bounds','config/native_sources')) or any(x in path.name.lower() for x in ('api_key','apikey','secret','credential')): _fail('EVIDENCE_SOURCE_PATH_FORBIDDEN')
        actual[relative]=hashlib.sha256(path.read_bytes()).hexdigest()
        if actual[relative]!=expected: _fail('NATIVE_EVIDENCE_SOURCE_DRIFT')
    return actual
def input_file_hashes(config,plan,config_path,plan_path):
    actual={}
    for value,pattern,expected in ((config_path,'protocol_*.json',config),(plan_path,'compiled_plan*.json',plan)):
        path=Path(value).resolve()
        if not path.is_relative_to((core.ROOT/'config').resolve()) or not path.match(pattern) or any(x in path.name.lower() for x in ('api_key','apikey','secret','credential')): _fail('EXPLICIT_FROZEN_INPUT_PATH_REQUIRED')
        raw=path.read_bytes()
        if json.loads(raw.decode('utf-8-sig'))!=expected: _fail('FROZEN_DISK_CONFIG_OR_PLAN_DRIFT')
        actual[str(path.relative_to(core.ROOT))]=hashlib.sha256(raw).hexdigest()
    actual.update(evidence_source_hashes(config))
    return actual
def validate_static(config,plan,recovery_review=None,reconciliation_review=None):
    if recovery_review is not None and reconciliation_review is not None: _fail('MUTUALLY_EXCLUSIVE_HISTORY_MODES')
    if bootstrap.enabled(config):
        validate_design(config,plan)
        if reconciliation_review is not None:
            return external_module.validate_review(reconciliation_review,config,plan,_source_hashes(),http.URLS)[1]
        if recovery_review is not None:
            return recovery_module.validate_review(recovery_review,config,plan,_source_hashes(),http.URLS)[1]
        return bootstrap.validate(config,plan,_source_hashes(),http.URLS)
    required=('frozen','paid_enabled','independent_review_approved','credentials_configured','network_enabled','credential_loading_enabled')
    if config.get('runtime_mode')!='PAID_USER_LAUNCH' or not all(config.get(k)is True for k in required): _fail('FROZEN_NETWORK_CREDENTIAL_GATES_CLOSED')
    validate_design(config,plan)
    sources=evidence_source_hashes(config)
    advance=config.get('formal_continuation_advance_authorization',{})
    if advance.get('ordinary_post_pilot_permission_preapproved')is not True or advance.get('all_technical_scientific_budget_and_authentication_gates_still_required')is not True: _fail('ADVANCE_CONTINUATION_NOT_AUTHORIZED')
    policy,phash=safe_json(config.get('runtime_policy_path',''),'config/runtime_policy')
    if phash!=config.get('runtime_policy_sha256') or policy.get('kind')!='COMMERCIAL_RUNTIME_TECHNICAL_POLICY' or policy.get('fixture')is not False or policy.get('require_attack_positive')is not False: _fail('COMMERCIAL_POLICY_REQUIRED')
    # Exact equality is supported only with an independently reviewed commercial
    # serializer/usage definition. No fixture counts or numerical tolerance fallback.
    if policy.get('calibration_criterion')!='exact_commercial_rendered_input_tokens': _fail('COMMERCIAL_CALIBRATION_CRITERION_PENDING')
    expected=policy.get('calibration_expected_input_tokens_by_id',{})
    cal=[r for r in plan if r['phase']=='calibration']
    if set(expected)!={r['request_id'] for r in cal} or any(type(v)is not int or v<=0 for v in expected.values()): _fail('EXACT_CALIBRATION_POLICY_COVERAGE')
    proof,ehash=safe_json(policy.get('commercial_evidence_path',''),'config/runtime_policy_evidence')
    if ehash!=policy.get('commercial_evidence_sha256') or proof.get('kind')!='COMMERCIAL_RENDERED_INPUT_AND_USAGE_DEFINITION' or proof.get('fixture')is not False or proof.get('session_id')!=config['session_id'] or proof.get('request_payloads')!={r['request_id']:r['payload_hash'] for r in plan} or proof.get('calibration_expected_input_tokens_by_id')!=expected or proof.get('evidence_source_hashes')!=sources: _fail('COMMERCIAL_POLICY_EVIDENCE_PENDING')
    record,_=safe_json(config.get('paid_review_record',''),'review')
    if record.get('kind')!='PAID_INSTRUMENT_ACCEPTANCE' or record.get('approved')is not True or record.get('config_hash')!=core.sha(config) or record.get('plan_hash')!=core.sha(plan) or record.get('runtime_policy_sha256')!=phash or record.get('commercial_evidence_sha256')!=ehash or record.get('source_hashes')!=_source_hashes(): _fail('NO_MATCHING_PAID_RUNTIME_REVIEW')
    build_schedule(config,plan); derive_technical_stages(plan)
    if config['cadence']!={'concurrency':1,'min_seconds_after_response':10,'arm_gap_seconds':30,'block_gap_seconds':60,'timeout_seconds':120,'retries':0}: _fail('FROZEN_CADENCE_MISMATCH')
    for provider,p in config['providers'].items():
        keys=('account_currency_verified','output_limit_verified','rendered_input_bound_verified','input_bound_evidence_verified','returned_model_verified')
        if p.get('http_url')!=http.URLS[provider] or not all(p.get(k)is True for k in keys) or p.get('fee_surcharge_multiplier')is None or p.get('currency')!=('USD' if provider=='openai' else 'CNY') or p.get('hard_cap')!=30 or p.get('dispatch_stop')!=27: _fail('PROVIDER_COMMERCIAL_EVIDENCE_PENDING')
        if provider=='deepseek' and p.get('billing_calendar',{}).get('verified')is not True: _fail('PRICE_CALENDAR_PENDING')
        for phase,pc in p['phases'].items():
            if pc['max_attempts'] and config.get('stage_authorizations',{}).get(provider,{}).get(phase)is not True: _fail('STATIC_STAGE_NOT_AUTHORIZED')
    probe=object.__new__(core.Ledger); probe.config=config; probe.plan={r['request_id']:r for r in plan}; probe.binding={'config':core.sha(config)}
    probe._check_inventory()
    for row in plan:
        probe._validate_item(row)
        if row['phase']=='calibration' and expected[row['request_id']]>row['bound']['input_upper']: _fail('CALIBRATION_EXPECTATION_OUTSIDE_PROVEN_BOUND')
    return policy
class RuntimeClock:
    def utcnow(self): return datetime.now(timezone.utc)
    def monotonic(self): return time.monotonic()
    def sleep(self,seconds): time.sleep(seconds)
class RoutedPipeline:
    def __init__(self,gate): self.gate=gate
    def send(self,rid):
        gate=self.gate; gate.require_new_request_permission(); adapter=gate.adapters[gate.ledger.plan[rid]['provider']]
        return http.DispatchPipeline(gate.ledger,adapter,runtime_gate=gate).send(rid)
class Journal:
    def __init__(self,root,binding,storage_binding=None):
        stored=copy.deepcopy(storage_binding if storage_binding is not None else binding)
        self.storage_binding=stored
        self.root=Path(root); self.path=self.root/'logs/runtime_workflow.sqlite'; anchor=self.root/'config/runtime_workflow_anchor.json'
        self.path.parent.mkdir(parents=True,exist_ok=True); anchor.parent.mkdir(parents=True,exist_ok=True)
        existing=anchor.exists()
        if existing:
            registration=json.loads(anchor.read_text(encoding='utf-8'))
            if registration.get('path')!=str(self.path.resolve()) or registration.get('binding')!=stored or not self.path.is_file() or self.path.stat().st_size==0: _fail('WORKFLOW_AUTHORITY_MISSING_OR_DRIFTED')
        else:
            if self.path.exists(): _fail('UNREGISTERED_WORKFLOW')
            anchor.write_text(core.canonical({'path':str(self.path.resolve()),'binding':stored}),encoding='utf-8')
        self.db=sqlite3.connect(self.path,timeout=5,isolation_level=None); self.binding=copy.deepcopy(binding)
        if existing:
            try:
                tables={r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if not {'meta','events'}.issubset(tables): _fail('WORKFLOW_SCHEMA_MISSING')
                row=self.db.execute('SELECT binding FROM meta WHERE id=1').fetchone()
                if row is None or json.loads(row[0])!=stored: _fail('WORKFLOW_BINDING')
                self.events()
            except BaseException:
                self.db.close(); _fail('WORKFLOW_CORRUPT')
        self.db.execute('PRAGMA journal_mode=WAL'); self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS meta(id INTEGER PRIMARY KEY CHECK(id=1),binding TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT,kind TEXT NOT NULL,data TEXT NOT NULL,prev TEXT NOT NULL,hash TEXT NOT NULL)')
        for op in ('UPDATE','DELETE'): self.db.execute(f"CREATE TRIGGER IF NOT EXISTS workflow_immutable_{op} BEFORE {op} ON events BEGIN SELECT RAISE(ABORT,'immutable workflow'); END")
        if not existing: self.db.execute('INSERT INTO meta VALUES(1,?)',(core.canonical(stored),))
    def events(self):
        prior='0'*64; records=[]
        for seq,kind,text,prev,h in self.db.execute('SELECT seq,kind,data,prev,hash FROM events ORDER BY seq'):
            data=json.loads(text)
            if prev!=prior or core.sha({'kind':kind,'data':data,'prev':prev})!=h: _fail('WORKFLOW_HASH_CHAIN')
            records.append((seq,kind,data,h)); prior=h
        return records
    def append(self,kind,data):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            records=self.events(); prev=records[-1][3] if records else '0'*64
            data=copy.deepcopy(data); h=core.sha({'kind':kind,'data':data,'prev':prev})
            self.db.execute('INSERT INTO events(kind,data,prev,hash) VALUES(?,?,?,?)',(kind,core.canonical(data),prev,h)); self.db.execute('COMMIT')
            return h
        except BaseException: self.db.execute('ROLLBACK'); raise
    def append_many(self,records):
        # Bound acceptance and stage authority become durable together.
        self.db.execute('BEGIN IMMEDIATE')
        try:
            old=self.events();prev=old[-1][3] if old else '0'*64
            for kind,data in records:
                data=copy.deepcopy(data);h=core.sha({'kind':kind,'data':data,'prev':prev})
                self.db.execute('INSERT INTO events(kind,data,prev,hash) VALUES(?,?,?,?)',(kind,core.canonical(data),prev,h));prev=h
            self.db.execute('COMMIT')
        except BaseException: self.db.execute('ROLLBACK');raise
    def close(self): self.db.close()
class RuntimeGate:
    @classmethod
    def open(cls,config,plan,config_path=None,plan_path=None,recovery_review=None,reconciliation_review=None):
        if config_path is None or plan_path is None: _fail('EXPLICIT_FROZEN_INPUT_PATH_REQUIRED')
        inputs=(str(Path(config_path).resolve()),str(Path(plan_path).resolve()))
        files=input_file_hashes(config,plan,*inputs)
        policy=validate_static(config,plan,recovery_review,reconciliation_review)  # Must fail before cash/prompt.
        recovery=None
        if reconciliation_review is not None:
            recovery,policy=external_module.validate_review(reconciliation_review,config,plan,_source_hashes(),http.URLS)
        if recovery_review is not None:
            recovery,policy=recovery_module.validate_review(recovery_review,config,plan,_source_hashes(),http.URLS)
        if recovery is not None:
            for required in ('logs/project_cash.sqlite','logs/runtime_workflow.sqlite','config/cash_authority.json','config/runtime_workflow_anchor.json'):
                if not (core.ROOT/required).is_file(): _fail('RECOVERY_EXISTING_AUTHORITY_REQUIRED')
        ledger=core.Ledger(core.Ledger.authority_path(),config,plan)
        obj=cls(); obj.root=core.ROOT.resolve(); obj.ledger=ledger; obj.policy=copy.deepcopy(policy); obj.recovery=recovery; obj.recovery_review=recovery_review; obj.reconciliation_review=reconciliation_review; ledger._runtime_gate=obj
        obj.schedule=tuple(build_schedule(config,plan)); obj.cal_stages,obj.diag_stages=derive_technical_stages(plan)
        obj.clock=RuntimeClock(); obj._active=None; obj.input_paths=inputs; obj._cadence_anchor=None
        obj.binding={**ledger.binding,'source_hashes':_source_hashes(),'schedule_hash':core.sha(obj.schedule),'technical_stages_hash':core.sha({'calibration':obj.cal_stages,'diagnostic':obj.diag_stages}),'policy_hash':core.sha(policy),'input_paths':list(inputs),'input_file_hashes':files,'fixture_only':False}
        try:
            if recovery is not None:
                obj.binding['recovery_rule_sha256']=recovery.rule_hash; obj.binding['recovery_review_sha256']=recovery.record_hash
            obj.journal=Journal(obj.root,obj.binding,recovery.rule['origin_workflow_binding'] if recovery is not None else None)
            if recovery is not None: recovery.migrate(ledger,obj.journal,obj.binding)
            obj.adapters={p:http.HTTPAdapter(network_enabled=not isinstance(recovery,external_module.Capability),runtime_gate=obj) for p in ('deepseek','openai')}
            obj.pipeline=RoutedPipeline(obj); obj.state(); return obj
        except BaseException:
            if hasattr(obj,'journal'): obj.journal.close()
            ledger.close(); raise
    def require_new_request_permission(self):
        if isinstance(getattr(self,'recovery',None),external_module.Capability):
            _fail('HISTORY_ONLY_NO_NEW_REQUESTS')
    def scope_grants(self):
        if not bootstrap.enabled(self.ledger.config): return {}
        grants={}
        for seq,kind,data,h in self.journal.events():
            if kind=='STAGE_GRANT':
                pair,record=bootstrap.validate_grant(self,data['path'],data['sha256'])
                if pair in grants or data['provider']!=pair[0] or data['phase']!=pair[1]: _fail('DUPLICATE_OR_INVALID_STAGE_GRANT')
                grants[pair]=record
        return grants
    def admitted_ids(self):
        return {rid for pair in self.scope_grants() for rid in self.stage_ids(*pair)}
    def cash_admission(self,rid):
        self.require_new_request_permission()
        # Also protects direct legacy Ledger.reserve/send_offline calls.
        s=self.state()
        if getattr(self,'recovery',None) is not None: self.recovery.scope(rid)
        if self._active!=rid or s['stopped_reason'] or s['next_id']!=rid: _fail('RUNTIME_CASH_CURSOR_REQUIRED')
        self._prereq(self.ledger.plan[rid]['phase'],s['accepted'])
        self.ledger._validate_item(self.ledger.plan[rid])
    def record_scope_grant(self,relative):
        if getattr(self,'recovery',None) is not None: _fail('RECOVERY_TECHNICAL_SCOPE_END')
        if not bootstrap.enabled(self.ledger.config): _fail('SCOPED_BOOTSTRAP_REQUIRED')
        s=self.state()
        if s['stopped_reason'] or self._active is not None: _fail('STOPPED_OR_ACTIVE_WORKFLOW')
        pair,record=bootstrap.validate_grant(self,relative,receipts=s['receipts'])
        self._prereq(pair[1],s['accepted'])
        if pair in self.scope_grants(): _fail('STAGE_ALREADY_ADMITTED')
        grants=self.scope_grants();grants[pair]=record
        cash=self.ledger.snapshot()
        for provider,p in self.ledger.config['providers'].items():
            remaining=core.money(0);phase_remaining={}
            for stage,grant in grants.items():
                if stage[0]!=provider: continue
                amount=core.money(0)
                for rid,bound in grant['bounds'].items():
                    if self.ledger._attempt(rid) in cash['attempts']: continue
                    row=copy.deepcopy(self.ledger.plan[rid]);row['bound']=bound
                    amount+=self.ledger._validate_commercial_item(row)
                remaining+=amount;phase_remaining[stage[1]]=amount
            confirmed,pending,_=self.ledger.totals(provider)
            if confirmed+pending+remaining>min(core.money(p['hard_cap']),core.money(p['dispatch_stop'])): _fail('STAGE_GRANT_REMAINING_BUDGET')
            for phase,amount in phase_remaining.items():
                pc,pp,_=self.ledger.totals(provider,phase)
                if pc+pp+amount>core.money(p['phases'][phase]['cash_subcap']): _fail('STAGE_GRANT_PHASE_BUDGET')
        self.journal.append_many([
            ('BOUND_ACCEPTANCE',{'provider':pair[0],'phase':pair[1],'bound_digest':core.sha(record['bounds']),'path':relative,'sha256':record['sha256']}),
            ('STAGE_GRANT',{'provider':pair[0],'phase':pair[1],'path':relative,'sha256':record['sha256']})])
        return record['sha256']
    def observed_response(self,rid):
        matches=[data for seq,kind,data,h in self.journal.events() if kind=='BOOTSTRAP_OBSERVATION' and data['request_id']==rid]
        if len(matches)!=1: _fail('EXACTLY_ONE_DURABLE_OBSERVATION_REQUIRED')
        event=matches[0];row=self.ledger.plan[rid]
        if any(event.get(k)!=v for k,v in (('provider',row['provider']),('phase',row['phase']),('payload_hash',row['payload_hash']),('binding_digest',core.sha(self.binding)))): _fail('OBSERVATION_BINDING')
        o=event['observation'];start=datetime.fromisoformat(o['request_started_utc']);end=datetime.fromisoformat(o['response_received_utc'])
        raw={k:copy.deepcopy(o[k]) for k in ('model','usage','service_tier','status','created') if k in o}
        if row['provider']=='openai':
            if 'incomplete_reason' in o: raw['incomplete_details']={'reason':o['incomplete_reason']}
            normalized=http.normalize_openai(raw,row['body']['model'])
        else:
            raw['choices']=[{'finish_reason':o.get('finish_reason')}]
            normalized=http.normalize_deepseek(raw,row['body']['model'],self.ledger.config,start,end)
        for k in ('request_started_utc','response_received_utc','elapsed_monotonic_seconds'): normalized[k]=o[k]
        return normalized
    def check_settlement(self,rid,response):
        self.require_new_request_permission()
        s=self.state(allow_active=True);a=self.ledger.snapshot()['attempts'].get(self.ledger._attempt(rid))
        if self._active!=rid or s['stopped_reason'] or s['next_id']!=rid or not a or a['status']!='RESERVED' or not a.get('transport_consumed'): _fail('CURRENT_SENT_RESERVED_SETTLEMENT_REQUIRED')
        if response!=self.observed_response(rid): _fail('SETTLEMENT_OBSERVATION_MISMATCH')
    def archive_observation(self,rid,observation):
        if not bootstrap.enabled(self.ledger.config): return
        if self._active!=rid: _fail('OBSERVATION_ACTIVE_BINDING')
        self._check_binding()
        row=self.ledger.plan[rid];a=self.ledger.snapshot()['attempts'].get(self.ledger._attempt(rid))
        if not a or a['status']!='RESERVED' or not a.get('transport_consumed'): _fail('OBSERVATION_WITHOUT_SENT_RESERVE')
        if any(kind=='BOOTSTRAP_OBSERVATION' and data['request_id']==rid for seq,kind,data,h in self.journal.events()): _fail('DUPLICATE_OBSERVATION')
        self.journal.append('BOOTSTRAP_OBSERVATION',{'request_id':rid,'provider':row['provider'],'phase':row['phase'],'payload_hash':row['payload_hash'],'binding_digest':core.sha(self.binding),'observation':observation})
    def record_adapter_failure(self,rid,diagnostic):
        if self._active!=rid: _fail('DIAGNOSTIC_ACTIVE_BINDING')
        self._check_binding(); diagnostic=http.validate_diagnostic(diagnostic)
        row=self.ledger.plan[rid]; attempt=self.ledger.snapshot()['attempts'].get(self.ledger._attempt(rid))
        if not attempt or attempt['status'] not in http.diagnostic_cash_states(diagnostic): _fail('DIAGNOSTIC_CASH_STATUS')
        if any(kind=='ADAPTER_DIAGNOSTIC' and data['request_id']==rid for seq,kind,data,h in self.journal.events()): _fail('DUPLICATE_ADAPTER_DIAGNOSTIC')
        self.journal.append('ADAPTER_DIAGNOSTIC',{'request_id':rid,'provider':row['provider'],'phase':row['phase'],
            'payload_hash':row['payload_hash'],'binding_digest':core.sha(self.binding),'cash_status_at_failure':attempt['status'],'diagnostic':diagnostic})
    def _wait_cadence(self,previous,row,response):
        same=previous.get('block_id')==row.get('block_id') and row.get('block_id')is not None
        cadence=self.ledger.config['cadence']
        gap=cadence['min_seconds_after_response'] if same and previous['arm']==row['arm'] else cadence['arm_gap_seconds'] if same else cadence['block_gap_seconds']
        try:
            wall=self.clock.utcnow();mono=self.clock.monotonic()
            if wall.utcoffset()is None or not math.isfinite(mono) or wall<datetime.fromisoformat(response['response_received_utc']): _fail('CADENCE_CLOCK_INVALID')
            anchor=self._cadence_anchor
            if anchor is None:
                # Monotonic values from an earlier process are not portable.
                # Never credit a wall-clock jump as elapsed protocol waiting.
                remaining=gap
            else:
                if anchor[0]!=previous['request_id']: _fail('CADENCE_ANCHOR_MISMATCH')
                elapsed=mono-anchor[2];wall_elapsed=(wall-anchor[1]).total_seconds()
                # Reuse the existing HTTP/receipt one-second clock consistency rule.
                if not math.isfinite(elapsed) or elapsed<0 or wall_elapsed<0 or abs(wall_elapsed-elapsed)>1: _fail('CADENCE_WALL_MONOTONIC_MISMATCH')
                remaining=max(0,gap-elapsed)
            self.clock.sleep(remaining)
            after_mono=self.clock.monotonic();after_wall=self.clock.utcnow()
            waited=after_mono-mono;wall_waited=(after_wall-wall).total_seconds()
            if not math.isfinite(waited) or waited<remaining or wall_waited<0 or abs(wall_waited-waited)>1: _fail('CADENCE_SLEEP_OR_CLOCK_MISMATCH')
        except BaseException:
            try: self.ledger.pause(row['provider'],'CADENCE_CLOCK_ANOMALY')
            except Exception: pass
            try: self.journal.append('STOP',{'reason':'CADENCE_CLOCK_ANOMALY','request_id':row['request_id']})
            except Exception: pass
            _fail('CADENCE_CLOCK_ANOMALY_STOPPED')
    def before_credential(self,provider=None,phase=None):
        self.require_new_request_permission()
        s=self.state()
        if s['stopped_reason'] or s['next_id'] is None: _fail('STOPPED_OR_COMPLETE_RUNTIME')
        row=self.ledger.plan[s['next_id']]
        if getattr(self,'recovery',None) is not None: self.recovery.scope(row['request_id'])
        if provider is not None and provider!=row['provider'] or phase is not None and phase!=row['phase']: _fail('LEGACY_PROVIDER_PHASE_CURSOR_BYPASS')
        self._prereq(row['phase'],s['accepted'])
        if bootstrap.enabled(self.ledger.config):
            if self._active is not None: _fail('ACTIVE_PREFLIGHT')
            self._active=row['request_id']
            try: self.ledger.preview(row['request_id'])
            finally: self._active=None
        else: self.ledger.preview(row['request_id'])
        return row
    def check_dispatch(self,rid,ledger,adapter,point,permit=None):
        self.require_new_request_permission()
        if ledger is not self.ledger or self._active!=rid or self.adapters.get(ledger.plan[rid]['provider'])is not adapter or adapter.runtime_gate is not self: _fail('RUNTIME_DISPATCH_BINDING_REQUIRED')
        s=self.state(allow_active=point!='reserve')
        if s['stopped_reason'] or s['next_id']!=rid: _fail('RUNTIME_CURSOR_OR_STOP')
        row=ledger.plan[rid]
        if getattr(self,'recovery',None) is not None: self.recovery.scope(rid)
        self._prereq(row['phase'],s['accepted']); ledger._validate_item(row)
        if point=='reserve':
            if permit is not None: _fail('PREMATURE_TRANSPORT_PERMIT')
        else:
            if type(permit)is not http.Permit or permit.runtime_gate is not self or permit.ledger is not ledger or permit.rid!=rid: _fail('TRANSPORT_RUNTIME_PERMIT_MISMATCH')
            attempt=ledger.snapshot()['attempts'].get(ledger._attempt(rid))
            if not attempt or attempt['status']!='RESERVED' or bool(attempt.get('transport_consumed'))!=(point=='physical'): _fail('TRANSPORT_CONSUMPTION_STATE')
            if bootstrap.enabled(ledger.config):
                cap=ledger._validate_item(row);confirmed,pending,_=ledger.totals(row['provider']);pc,pp,_=ledger.totals(row['provider'],row['phase'])
                provider=ledger.config['providers'][row['provider']]
                if core.money(attempt['reserve'])!=cap or confirmed+pending>min(core.money(provider['dispatch_stop']),core.money(provider['hard_cap'])) or pc+pp>core.money(provider['phases'][row['phase']]['cash_subcap']): _fail('PHYSICAL_EXPOSURE_OR_RESERVE_DRIFT')
    def accept_completed(self):
        self.require_new_request_permission()
        for provider,phase in self.cal_stages+self.diag_stages:
            s=self.state()
            if (provider,phase) not in s['accepted'] and all(r in s['receipts'] for r in self.stage_ids(provider,phase)):
                self.accept_stage(provider,phase)
    def stop_recovery(self):
        if getattr(self,'recovery',None) is None or isinstance(self.recovery,external_module.Capability): return
        try:
            if self.state()['stopped_reason']: return
        except BaseException: pass
        try: self.ledger.pause('deepseek','RECOVERY_ENTRY_FAILED')
        except Exception: pass
        try: self.journal.append('STOP',{'reason':'RECOVERY_ENTRY_FAILED'})
        except Exception: pass
    def close(self):
        for adapter in self.adapters.values():
            if isinstance(adapter.credential,http.Credential): adapter.credential.clear()
            adapter.credential=None
        try: self.journal.close()
        finally: self.ledger.close()
    def _check_binding(self):
        if tuple(self.input_paths)!=tuple(self.binding['input_paths']) or input_file_hashes(self.ledger.config,list(self.ledger.plan.values()),*self.input_paths)!=self.binding['input_file_hashes']: _fail('FROZEN_INPUT_FILES_DRIFT')
        if core.ROOT.resolve()!=self.root or self.ledger.path!=core.Ledger.authority_path(): _fail('RUNTIME_ROOT_OR_CASH_PATH_DRIFT')
        anchor=json.loads((self.root/'config/cash_authority.json').read_text(encoding='utf-8'))
        if anchor!={'path':str(self.ledger.path),'authority_id':self.ledger.binding['authority_id']}: _fail('CASH_REGISTRATION_DRIFT')
        meta=self.ledger.db.execute('SELECT binding FROM meta WHERE id=1').fetchone()
        if meta is None or json.loads(meta[0])!=self.ledger.binding: _fail('CASH_BINDING_DRIFT')
        registration=json.loads((self.root/'config/runtime_workflow_anchor.json').read_text(encoding='utf-8'))
        if registration!={'path':str(self.journal.path.resolve()),'binding':getattr(self.journal,'storage_binding',self.binding)}: _fail('WORKFLOW_REGISTRATION_DRIFT')
        if core.sha(self.policy)!=self.binding['policy_hash'] or validate_static(self.ledger.config,list(self.ledger.plan.values()),getattr(self,'recovery_review',None),getattr(self,'reconciliation_review',None))!=self.policy: _fail('COMMERCIAL_POLICY_DRIFT')
        if core.sha(self.ledger.config)!=self.binding['config'] or core.sha(list(self.ledger.plan.values()))!=self.binding['plan'] or _source_hashes()!=self.binding['source_hashes']: _fail('IMMUTABLE_WORKFLOW_BINDING_DRIFT')
        if self.ledger.binding['authority_id']!=self.binding['authority_id']: _fail('CASH_AUTHORITY_DRIFT')
        persisted=self.journal.db.execute('SELECT binding FROM meta WHERE id=1').fetchone()
        if persisted is None or json.loads(persisted[0])!=getattr(self.journal,'storage_binding',self.binding) or self.journal.binding!=self.binding: _fail('PERSISTED_WORKFLOW_BINDING_DRIFT')
        expected_schedule=tuple(build_schedule(self.ledger.config,list(self.ledger.plan.values())))
        if type(self.schedule)is not tuple or self.schedule!=expected_schedule or core.sha(self.schedule)!=self.binding['schedule_hash']: _fail('EXECUTION_SCHEDULE_DRIFT')
        cal,diag=derive_technical_stages(self.ledger.plan.values())
        if type(self.cal_stages)is not tuple or type(self.diag_stages)is not tuple or self.cal_stages!=cal or self.diag_stages!=diag or core.sha({'calibration':self.cal_stages,'diagnostic':self.diag_stages})!=self.binding['technical_stages_hash']: _fail('TECHNICAL_STAGE_SET_DRIFT')
    def stage_ids(self,p,phase): return [rid for rid in self.schedule if self.ledger.plan[rid]['provider']==p and self.ledger.plan[rid]['phase']==phase]
    def _clean(self,rid,res):
        row=self.ledger.plan[rid]; p=row['provider']
        # Normalize once more through a fixed whitelist; never copy arbitrary fields or content.
        keys=('model','service_tier','usage','api_status','truncated','price_regime','request_started_utc','response_received_utc','elapsed_monotonic_seconds')
        clean={k:copy.deepcopy(res[k]) for k in keys if k in res}
        if bootstrap.enabled(self.ledger.config) and clean!=self.observed_response(rid): _fail('RECEIPT_OBSERVATION_MISMATCH')
        if clean.get('model')!=row['body']['model']: _fail('RECEIPT_MODEL')
        if clean.get('api_status') not in ('completed','incomplete'): _fail('RECEIPT_OUTCOME')
        if p=='openai' and clean.get('service_tier') not in ('default','standard'): _fail('RECEIPT_TIER')
        if type(clean.get('truncated'))is not bool: _fail('RECEIPT_TRUNCATION_FLAG')
        start=datetime.fromisoformat(clean['request_started_utc']); end=datetime.fromisoformat(clean['response_received_utc'])
        elapsed=clean.get('elapsed_monotonic_seconds')
        if start.utcoffset() is None or end.utcoffset() is None or type(elapsed) not in (int,float) or not math.isfinite(elapsed) or elapsed<0 or elapsed>self.ledger.config['cadence']['timeout_seconds'] or (end-start).total_seconds()<0 or abs((end-start).total_seconds()-elapsed)>1: _fail('RECEIPT_CLOCK')
        if p=='deepseek' and not bootstrap.enabled(self.ledger.config) and http._regime(self.ledger.config,start,end,int(start.timestamp()))!=clean.get('price_regime'): _fail('RECEIPT_PRICE_WINDOW')
        usage=clean['usage']; cost=core.usage_cash(self.ledger.config,p,usage,clean.get('price_regime'))
        bound=self.ledger._bound(row)
        if cost['input_tokens']>bound['input_upper'] or cost['output_tokens']>bound['output_upper']: _fail('RECEIPT_BOUND_BREACH')
        if row['phase']=='calibration' and not bootstrap.enabled(self.ledger.config):
            expected=self.policy['calibration_expected_input_tokens_by_id'][rid]
            if type(expected)is not int or cost['input_tokens']!=expected: _fail('COMMERCIAL_CALIBRATION_INPUT_MISMATCH')
        # Restrict usage to the already validated provider schema, including optional reasoning detail.
        if p=='openai':
            u={'input_tokens':usage['input_tokens'],'input_tokens_details':{'cached_tokens':usage['input_tokens_details']['cached_tokens'],'cache_write_tokens':usage['input_tokens_details']['cache_write_tokens']},'output_tokens':usage['output_tokens']}
            if 'output_tokens_details' in usage: u['output_tokens_details']={'reasoning_tokens':usage['output_tokens_details'].get('reasoning_tokens',0)}
        else:
            u={k:usage[k] for k in ('prompt_tokens','prompt_cache_hit_tokens','prompt_cache_miss_tokens','completion_tokens')}
            if 'completion_tokens_details' in usage: u['completion_tokens_details']={'reasoning_tokens':usage['completion_tokens_details'].get('reasoning_tokens',0)}
        clean['usage']=u
        return clean,cost
    def _receipt(self,rid,res):
        if isinstance(getattr(self,'recovery',None),external_module.Capability) and self.recovery.is_external_target(rid):
            return self.recovery.covered_receipt(rid,res)
        clean,cost=self._clean(rid,res); r=self.ledger.plan[rid]
        return {'request_id':rid,'provider':r['provider'],'phase':r['phase'],'payload_hash':r['payload_hash'],'response':clean,'cash':str(cost['cash']),'input_cost':str(cost['input']),'output_cost':str(cost['output']),**(self.recovery.receipt_metadata(rid) if getattr(self,'recovery',None) is not None else {})}
    def _validate_saved(self,receipt,cash):
        rid=receipt['request_id']
        if rid not in self.ledger.plan: _fail('RECEIPT_ID')
        expected=self._receipt(rid,receipt['response'])
        if receipt!=expected: _fail('RECEIPT_SCHEMA_OR_VALUE')
        a=cash['attempts'].get(self.ledger._attempt(rid))
        wanted_status='EXTERNALLY_RECONCILED' if isinstance(getattr(self,'recovery',None),external_module.Capability) and self.recovery.is_external_target(rid) else 'SETTLED'
        if not a or a['status']!=wanted_status or a.get('bound_breach') or a['payload_hash']!=receipt['payload_hash']: _fail('RECEIPT_WITHOUT_MATCHING_SETTLED_CASH')
        if any(receipt[k]!=a[k] for k in ('cash','input_cost','output_cost')): _fail('RECEIPT_CASH_MISMATCH')
        measured=core.usage_cash(self.ledger.config,self.ledger.plan[rid]['provider'],receipt['response']['usage'],receipt['response'].get('price_regime'))
        if measured['input_tokens']!=a['input_tokens'] or measured['output_tokens']!=a['output_tokens']: _fail('RECEIPT_CASH_TOKEN_MISMATCH')
    def _stage_digest(self,p,phase,receipts):
        ids=self.stage_ids(p,phase)
        if not ids or any(rid not in receipts for rid in ids): _fail('INCOMPLETE_STAGE_RECEIPTS')
        return core.sha([receipts[rid] for rid in ids])
    def state(self,allow_active=False):
        self._check_binding(); cash=self.ledger.snapshot(); receipts={}; accepted=set(); stopped=None; observations={}; grants=set(); bounds=set(); diagnostics={}
        events=self.journal.events(); recovery=getattr(self,'recovery',None); mapped=False
        external=isinstance(recovery,external_module.Capability);external_evidence=False
        if recovery is not None: recovery.validate_migration(events,self.binding)
        for seq,kind,data,h in events:
            if kind=='SOURCE_MIGRATION' and recovery is not None:
                continue
            elif recovery is not None and kind==getattr(recovery,'mapping_kind','TECHNICAL_REPLACEMENT_ACCEPT'):
                target=recovery.rule['target_request_id']
                if mapped or target not in receipts or data!=recovery.mapping(receipts[target],self.binding): _fail('RECOVERY_SCIENTIFIC_MAPPING')
                mapped=True
            elif external and kind=='EXTERNAL_BILL_EVIDENCE_ACCEPT':
                if external_evidence or data!=recovery.evidence_acceptance(): _fail('EXTERNAL_EVIDENCE_ACCEPT_REPLAY')
                external_evidence=True
            elif bootstrap.enabled(self.ledger.config) and kind=='BOOTSTRAP_OBSERVATION':
                rid=data['request_id']; row=self.ledger.plan.get(rid)
                digest=recovery.historical_binding_digest() if external and recovery.historical(seq,kind,h) else core.sha(self.binding)
                if row is None or rid in observations or rid!=self.schedule[len(receipts)] or data['payload_hash']!=row['payload_hash'] or data['binding_digest']!=digest: _fail('OBSERVATION_BINDING_OR_ORDER')
                pair=(row['provider'],row['phase'])
                if row['phase']!='calibration' and (pair not in grants or row['phase']=='formal' and not all((p,'formal') in grants for p in ('deepseek','openai'))): _fail('OBSERVATION_BEFORE_STAGE_GRANT')
                observations[rid]=data
            elif kind=='ADAPTER_DIAGNOSTIC':
                rid=data['request_id']; row=self.ledger.plan.get(rid)
                attempt=cash['attempts'].get(self.ledger._attempt(rid)) if row is not None else None
                diagnostic=http.validate_diagnostic(data['diagnostic'])
                current=len(receipts)<len(self.schedule) and rid==self.schedule[len(receipts)]
                # A receipt may have committed before its append raised; do not invent/advance it.
                latest_committed=diagnostic['stage']=='RECEIPT_WRITE' and bool(receipts) and rid==self.schedule[len(receipts)-1] and rid in receipts
                saved_status=data.get('cash_status_at_failure')
                if row is None or rid in diagnostics or not (current or latest_committed) or not attempt or type(saved_status)is not str or saved_status not in http.diagnostic_cash_states(diagnostic): _fail('DIAGNOSTIC_BINDING_OR_ORDER')
                historical_external=external and recovery.historical(seq,kind,h)
                compatible=attempt['status']==saved_status or saved_status=='RESERVED' and attempt['status']=='UNKNOWN' or historical_external and saved_status=='UNKNOWN' and attempt['status']=='EXTERNALLY_RECONCILED'
                if not compatible: _fail('DIAGNOSTIC_BINDING_OR_ORDER')
                digest=recovery.historical_binding_digest() if historical_external else core.sha(self.binding)
                if any(data.get(k)!=v for k,v in (('provider',row['provider']),('phase',row['phase']),('payload_hash',row['payload_hash']),('binding_digest',digest))): _fail('DIAGNOSTIC_BINDING_OR_ORDER')
                diagnostics[rid]=diagnostic
            elif recovery is not None and kind in ('BOUND_ACCEPTANCE','STAGE_GRANT'):
                _fail('RECOVERY_TECHNICAL_SCOPE_END')
            elif bootstrap.enabled(self.ledger.config) and kind=='BOUND_ACCEPTANCE':
                pair,record=bootstrap.validate_grant(self,data['path'],data['sha256'],receipts)
                if pair in bounds or data['bound_digest']!=core.sha(record['bounds']): _fail('BOUND_ACCEPTANCE_INVALID')
                self._prereq(pair[1],accepted);bounds.add(pair)
            elif bootstrap.enabled(self.ledger.config) and kind=='STAGE_GRANT':
                pair,record=bootstrap.validate_grant(self,data['path'],data['sha256'],receipts)
                if pair not in bounds or pair in grants: _fail('STAGE_GRANT_WITHOUT_BOUND_ACCEPTANCE')
                self._prereq(pair[1],accepted);grants.add(pair)
            elif kind=='RECEIPT':
                rid=data['request_id']
                if external and recovery.is_external_target(rid) and not external_evidence: _fail('EXTERNAL_RECEIPT_WITHOUT_EVIDENCE_ACCEPT')
                if rid in receipts or rid!=self.schedule[len(receipts)]: _fail('WORKFLOW_RECEIPT_ORDER')
                self._prereq(self.ledger.plan[rid]['phase'],accepted)
                if bootstrap.enabled(self.ledger.config):
                    row=self.ledger.plan[rid];pair=(row['provider'],row['phase'])
                    if rid not in observations: _fail('RECEIPT_WITHOUT_PRIOR_OBSERVATION')
                    if row['phase']!='calibration' and pair not in grants: _fail('RECEIPT_BEFORE_STAGE_GRANT')
                self._validate_saved(data,cash); receipts[rid]=data
            elif kind=='STAGE_ACCEPT':
                pair=(data['provider'],data['phase'])
                if pair not in self.cal_stages+self.diag_stages: _fail('UNDECLARED_STAGE_ACCEPT')
                if pair in accepted or data.get('receipt_digest')!=self._stage_digest(*pair,receipts): _fail('INVALID_STAGE_ACCEPT')
                self._prereq(pair[1],accepted); accepted.add(pair)
            elif kind=='STOP':
                historical=recovery is not None and [kind,h] in recovery.rule['original_workflow_prefix']
                if not historical: stopped=data['reason']
            else: _fail('UNKNOWN_WORKFLOW_EVENT')
        for aid,a in cash['attempts'].items():
            rid=a['request_id']
            if recovery is not None and recovery.legacy(aid): continue
            if a['status'] not in ('SETTLED','EXTERNALLY_RECONCILED'):
                if not(allow_active and rid==self._active and a['status']=='RESERVED'): stopped=stopped or 'UNSETTLED_CASH'
            elif rid not in receipts: stopped=stopped or 'SETTLED_CASH_RECEIPT_MISSING'
        paused={p for p in cash['paused'] if recovery is None or not recovery.can_override(p)}
        if paused: stopped=stopped or 'PAUSED_CASH_PROVIDER'
        if recovery is not None and recovery.rule['target_request_id'] in receipts and not mapped: stopped=stopped or 'RECOVERY_SCIENTIFIC_MAP_MISSING'
        phase='SEALED'
        if all(s in accepted for s in self.cal_stages): phase='CALIBRATION_OBSERVED' if bootstrap.enabled(self.ledger.config) else 'CALIBRATION_ACCEPTED'
        if all(s in accepted for s in self.cal_stages+self.diag_stages):
            phase='DIAGNOSTICS_OBSERVED' if bootstrap.enabled(self.ledger.config) else 'FORMAL_ELIGIBLE'
            if bootstrap.enabled(self.ledger.config) and all((p,'formal') in grants for p in ('deepseek','openai')): phase='FORMAL_ELIGIBLE'
        if len(receipts)==len(self.schedule) and not stopped: phase='COMPLETED'
        if external and not stopped:
            if len(receipts)!=1 or set(receipts)!={recovery.rule['target_request_id']} or not mapped or not external_evidence or accepted or grants or bounds:_fail('HISTORY_ONLY_COMPLETION_INVARIANT')
            phase='HISTORY_ONLY_COMPLETE'
        if stopped: phase='STOPPED'
        return {'phase':phase,'stopped_reason':stopped,'retained_legacy_unknown_attempt':recovery.rule['legacy_attempt_id'] if recovery is not None else None,'replacement_technical_mapping':mapped,'adapter_diagnostics':diagnostics,'receipts':receipts,'accepted':accepted,'admitted_stages':grants,'deferred_ids':tuple(rid for rid in self.schedule if self.ledger.plan[rid]['phase']!='calibration' and (self.ledger.plan[rid]['provider'],self.ledger.plan[rid]['phase']) not in grants) if bootstrap.enabled(self.ledger.config) else (), 'cursor':len(receipts),'history_only':external,'new_request_allowance':0 if external else None,'next_id':None if external else self.schedule[len(receipts)] if len(receipts)<len(self.schedule) else None}
    def _prereq(self,phase,accepted):
        if phase in ('pilot','shape','formal') and not all(s in accepted for s in self.cal_stages): _fail('CALIBRATION_GATE_CLOSED')
        if phase=='formal' and not all(s in accepted for s in self.diag_stages): _fail('DIAGNOSTIC_GATE_CLOSED')
    def accept_stage(self,provider,phase):
        self.require_new_request_permission()
        if (provider,phase) not in self.cal_stages+self.diag_stages: _fail('ONLY_DECLARED_TECHNICAL_STAGES')
        s=self.state()
        if s['stopped_reason']: _fail('STOPPED_WORKFLOW')
        self._prereq(phase,s['accepted'])
        if (provider,phase) in s['accepted']: _fail('STAGE_ACCEPT_ALREADY_RECORDED')
        digest=self._stage_digest(provider,phase,s['receipts'])
        self.journal.append('STAGE_ACCEPT',{'provider':provider,'phase':phase,'receipt_digest':digest})
        return digest
    def _transport_guard(self,rid,permit):
        self.require_new_request_permission()
        if self._active!=rid or permit.ledger is not self.ledger: _fail('DIRECT_TRANSPORT_BYPASS')
        s=self.state(allow_active=True)
        if s['stopped_reason'] or s['next_id']!=rid: _fail('TRANSPORT_CURSOR_OR_STOP')
        self._prereq(self.ledger.plan[rid]['phase'],s['accepted'])
    def step(self,rid=None):
        self.require_new_request_permission()
        s=self.state()
        if s['stopped_reason']: _fail('STOPPED_WORKFLOW')
        wanted=s['next_id']
        if wanted is None: _fail('WORKFLOW_ALREADY_COMPLETE')
        if rid is not None and rid!=wanted: _fail('GLOBAL_CURSOR_MISMATCH')
        row=self.ledger.plan[wanted]; self._prereq(row['phase'],s['accepted'])
        if s['receipts']:
            prev=self.ledger.plan[self.schedule[s['cursor']-1]]
            self._wait_cadence(prev,row,s['receipts'][prev['request_id']]['response'])
        self._active=wanted; diagnostic_stage=None
        try:
            response=self.pipeline.send(wanted)
            diagnostic_stage='RECEIPT_PREPARE'; receipt=self._receipt(wanted,response)
            diagnostic_stage='RECEIPT_WRITE'
            if getattr(self,'recovery',None) is not None and wanted==self.recovery.rule['target_request_id']:
                self.journal.append_many([('RECEIPT',receipt),('TECHNICAL_REPLACEMENT_ACCEPT',self.recovery.mapping(receipt,self.binding))])
            else: self.journal.append('RECEIPT',receipt)
            diagnostic_stage=None
            predictions=self.policy.get('post_response_calibration_predictions',{}) if bootstrap.enabled(self.ledger.config) else {}
            if wanted in predictions and core.usage_cash(self.ledger.config,row['provider'],response['usage'],response.get('price_regime'))['input_tokens']!=predictions[wanted]:
                self.journal.append('STOP',{'reason':'SCIENTIFIC_CALIBRATION_MISMATCH','request_id':wanted})
                _fail('SCIENTIFIC_CALIBRATION_MISMATCH')
            self._cadence_anchor=(wanted,self.clock.utcnow(),self.clock.monotonic())
            return receipt
        except BaseException as exc:
            if diagnostic_stage is not None:
                try:
                    diagnostic=http.failure_diagnostic(exc,diagnostic_stage,physical_open_reached=self.adapters[row['provider']].network_enabled)
                    self.record_adapter_failure(wanted,diagnostic)
                except BaseException: pass
            try: self.ledger.pause(row['provider'],'RUNTIME_COORDINATOR_STOP')
            except Exception: pass
            try: self.journal.append('STOP',{'reason':'DISPATCH_BILL_OR_RECEIPT_FAILURE','request_id':wanted})
            except Exception: pass
            raise WorkflowError('DISPATCH_STOPPED_WITH_CASH_RETAINED') from None
        finally: self._active=None


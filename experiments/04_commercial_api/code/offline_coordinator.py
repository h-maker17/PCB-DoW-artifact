"""Offline-only staged coordinator. No live path, key reader, SDK or network client."""
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
import copy, hashlib, json, sqlite3, math
import protocol_core as core
import http_adapters as http

REAL_ROOT = Path(__file__).resolve().parents[1]


class WorkflowError(RuntimeError): pass


def _fail(code): raise WorkflowError(code)


def _source_hashes():
    return {n: hashlib.sha256((REAL_ROOT / 'code' / n).read_bytes()).hexdigest() for n in
            ('protocol_core.py', 'http_adapters.py', 'offline_coordinator.py')}


def guard_fixture(root, config):
    root = Path(root).resolve()
    if not root.is_relative_to((REAL_ROOT / 'tmp').resolve()) or not root.name.startswith(
        'coordinator-fixture-'): _fail('FIXTURE_ROOT_ONLY')
    if config.get('runtime_mode') != 'OFFLINE_FIXTURE' or config.get('network_enabled') is not False or config.get(
        'credential_loading_enabled') is not False: _fail('OFFLINE_ONLY')
    policy = config.get('offline_workflow_policy', {})
    if policy.get('fixture_only') is not True or policy.get(
        'ordinary_continuation_preapproved') is not True or policy.get('require_attack_positive') is not False: _fail(
        'FIXTURE_POLICY_REQUIRED')
    if core.ROOT.resolve() != root: _fail('CASH_ROOT_MISMATCH')
    return root


def build_schedule(config, plan):
    ids = [r['request_id'] for r in plan]
    if len(ids) != len(set(ids)): _fail('DUPLICATE_PLAN_ID')
    if set(config['providers']) != {'openai', 'deepseek'}: _fail('TWO_PROVIDER_SCHEDULE_REQUIRED')
    allowed = {'formal', 'calibration', 'pilot', 'shape'}
    if any(r['phase'] not in allowed for r in plan): _fail('UNSUPPORTED_SCHEDULE_PHASE')
    formal = [r for r in plan if r['phase'] == 'formal']
    expected = []
    for n in config['N']:
        for repeat in range(1, config['repeats'] + 1):
            for provider in (('deepseek', 'openai') if repeat % 2 else ('openai', 'deepseek')):
                for arm in core.structure.arm_order(provider, n, repeat):
                    for replica in range(1, n + 1): expected.append((provider, n, repeat, arm, replica))
    actual = [(r['provider'], r['N'], r['repeat'], r['arm'], r['replica']) for r in formal]
    if actual != expected: _fail('FORMAL_ORDER_OR_DESIGN_MISMATCH')
    ordered = []
    for provider, phase in (('deepseek', 'calibration'), ('openai', 'calibration'), ('deepseek', 'pilot'),
                            ('openai', 'shape')):
        ordered.extend(r['request_id'] for r in plan if r['provider'] == provider and r['phase'] == phase)
    ordered.extend(r['request_id'] for r in formal)
    if set(ordered) != set(ids) or len(ordered) != len(ids): _fail('INCOMPLETE_SCHEDULE')
    return ordered


def derive_technical_stages(plan):
    rows = list(plan)
    cal = tuple((p, 'calibration') for p in ('deepseek', 'openai') if
                any(r['provider'] == p and r['phase'] == 'calibration' for r in rows))
    diag = tuple((p, ph) for p, ph in (('deepseek', 'pilot'), ('openai', 'shape')) if
                 any(r['provider'] == p and r['phase'] == ph for r in rows))
    if len(cal) != 2 or len(diag) != 2: _fail('DECLARED_TECHNICAL_STAGES_REQUIRED')
    return cal, diag


@dataclass(frozen=True)
class FakeCredential:
    provider: str

    def __post_init__(self):
        if self.provider not in ('openai', 'deepseek'): _fail('ONLY_FAKE_PROVIDER_IDENTITIES')


class FakeClock:
    def __init__(self, stamp=None):
        self.stamp = stamp or datetime(2026, 10, 12, 2, tzinfo=timezone.utc);
        self.elapsed = 0.;
        self.sleeps = []
        if self.stamp.utcoffset() is None: _fail('AWARE_FIXTURE_CLOCK_REQUIRED')

    def utcnow(self):
        return self.stamp

    def monotonic(self):
        return self.elapsed

    def sleep(self, seconds):
        if seconds < 0: _fail('NEGATIVE_GAP')
        self.sleeps.append(seconds);
        self.stamp += timedelta(seconds=seconds);
        self.elapsed += seconds


class FakeRouter:
    """Known synthetic responses only; no credential value or external callable."""

    def __init__(self, clock, inputs, faults=None, extras=None):
        if type(clock) is not FakeClock: _fail('FAKE_CLOCK_REQUIRED')
        self.clock = clock;
        self.inputs = copy.deepcopy(inputs);
        self.faults = copy.deepcopy(faults or {});
        self.extras = copy.deepcopy(extras or {})
        self.identities = {p: FakeCredential(p) for p in ('openai', 'deepseek')};
        self.trace = [];
        self.owner = None

    def exchange(self, url, encoded, permit):
        if self.owner is None: _fail('UNBOUND_FAKE_ROUTER')
        self.owner._transport_guard(permit.rid, permit)
        row = permit.ledger.plan[permit.rid];
        provider = row['provider']
        if url != http.URLS[provider] or type(self.identities[provider]) is not FakeCredential or self.identities[
            provider].provider != provider: _fail('FAKE_ROUTING_MISMATCH')
        if json.loads(encoded) != row['body']: _fail('WIRE_BODY_MISMATCH')
        if permit.ledger.db.in_transaction: _fail('UNCOMMITTED_TRANSPORT')
        self.trace.append({'request_id': permit.rid, 'provider': provider, 'fake_identity': provider,
                           'at_utc': self.clock.utcnow().isoformat()})
        fault = self.faults.get(permit.rid)
        if fault == 'timeout': raise TimeoutError('SYNTHETIC_FAILURE_NOT_A_SECRET')
        if fault == '429': return 429, b'{}'
        t = self.inputs[permit.rid];
        output = row['body']['max_output_tokens' if provider == 'openai' else 'max_tokens']
        if fault == 'input_over': t = row['bound']['input_upper'] + 1
        if provider == 'openai':
            raw = {'model': row['body']['model'], 'service_tier': 'default',
                   'status': 'incomplete' if fault == 'incomplete' else 'completed',
                   'incomplete_details': {'reason': 'max_output_tokens'},
                   'usage': {'input_tokens': t, 'input_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0},
                             'output_tokens': output, 'output_tokens_details': {'reasoning_tokens': 0}}}
        else:
            raw = {'model': row['body']['model'], 'created': int(self.clock.utcnow().timestamp()),
                   'usage': {'prompt_tokens': t, 'prompt_cache_hit_tokens': 0, 'prompt_cache_miss_tokens': t,
                             'completion_tokens': output}, 'choices': [
                    {'finish_reason': 'length' if fault == 'incomplete' else 'stop',
                     'message': {'content': 'SYNTHETIC_TEXT_IGNORED'}}]}
        if fault == 'wrong_model': raw['model'] = 'WRONG_SYNTHETIC_MODEL'
        if fault == 'missing_usage': raw.pop('usage')
        if fault == 'cal_mismatch':
            if provider == 'openai':
                raw['usage']['input_tokens'] -= 1
            else:
                raw['usage']['prompt_tokens'] -= 1;
                raw['usage']['prompt_cache_miss_tokens'] -= 1
        raw.update(copy.deepcopy(self.extras))
        return 200, core.canonical(raw).encode('utf-8')


class Journal:
    def __init__(self, root, binding):
        self.root = Path(root);
        self.path = self.root / 'logs/offline_workflow.sqlite';
        anchor = self.root / 'config/offline_workflow_anchor.json'
        self.path.parent.mkdir(parents=True, exist_ok=True);
        anchor.parent.mkdir(parents=True, exist_ok=True)
        existing = anchor.exists()
        if existing:
            registration = json.loads(anchor.read_text(encoding='utf-8'))
            if registration.get('path') != str(self.path.resolve()) or registration.get(
                'binding') != binding or not self.path.is_file() or self.path.stat().st_size == 0: _fail(
                'WORKFLOW_AUTHORITY_MISSING_OR_DRIFTED')
        else:
            if self.path.exists(): _fail('UNREGISTERED_WORKFLOW')
            anchor.write_text(core.canonical({'path': str(self.path.resolve()), 'binding': binding}), encoding='utf-8')
        self.db = sqlite3.connect(self.path, timeout=5, isolation_level=None);
        self.binding = copy.deepcopy(binding)
        if existing:
            try:
                tables = {r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if not {'meta', 'events'}.issubset(tables): _fail('WORKFLOW_SCHEMA_MISSING')
                row = self.db.execute('SELECT binding FROM meta WHERE id=1').fetchone()
                if row is None or json.loads(row[0]) != binding: _fail('WORKFLOW_BINDING')
                self.events()
            except BaseException:
                self.db.close();
                _fail('WORKFLOW_CORRUPT')
        self.db.execute('PRAGMA journal_mode=WAL');
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS meta(id INTEGER PRIMARY KEY CHECK(id=1),binding TEXT NOT NULL)')
        self.db.execute(
            'CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT,kind TEXT NOT NULL,data TEXT NOT NULL,prev TEXT NOT NULL,hash TEXT NOT NULL)')
        for op in ('UPDATE', 'DELETE'): self.db.execute(
            f"CREATE TRIGGER IF NOT EXISTS workflow_immutable_{op} BEFORE {op} ON events BEGIN SELECT RAISE(ABORT,'immutable workflow'); END")
        if not existing: self.db.execute('INSERT INTO meta VALUES(1,?)', (core.canonical(binding),))

    def events(self):
        prior = '0' * 64;
        records = []
        for seq, kind, text, prev, h in self.db.execute('SELECT seq,kind,data,prev,hash FROM events ORDER BY seq'):
            data = json.loads(text)
            if prev != prior or core.sha({'kind': kind, 'data': data, 'prev': prev}) != h: _fail('WORKFLOW_HASH_CHAIN')
            records.append((seq, kind, data, h));
            prior = h
        return records

    def append(self, kind, data):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            records = self.events();
            prev = records[-1][3] if records else '0' * 64
            data = copy.deepcopy(data);
            h = core.sha({'kind': kind, 'data': data, 'prev': prev})
            self.db.execute('INSERT INTO events(kind,data,prev,hash) VALUES(?,?,?,?)',
                            (kind, core.canonical(data), prev, h));
            self.db.execute('COMMIT')
            return h
        except BaseException:
            self.db.execute('ROLLBACK'); raise

    def close(self):
        self.db.close()


class OfflineCoordinator:
    """This factory can create only fixture cash/journal, never real cash authority."""

    @classmethod
    def open(cls, root, config, plan, router):
        root = guard_fixture(root, config)
        if type(router) is not FakeRouter or router.owner is not None: _fail('FRESH_FAKE_ROUTER_REQUIRED')
        schedule = build_schedule(config, plan)
        # Prove every input-evidence file is an explicit fixture before constructing cash authority.
        for r in plan:
            e = r.get('bound')
            if not isinstance(e, dict): _fail('FIXTURE_BOUNDS_REQUIRED')
            p = (root / e['evidence_path']).resolve()
            if not p.is_relative_to(root) or 'api_key' in p.name.lower() or not json.loads(
                p.read_text(encoding='utf-8')).get('fixture'): _fail('FIXTURE_EVIDENCE_ONLY')
        ledger = core.Ledger(core.Ledger.authority_path(), config, plan)
        try:
            obj = cls();
            obj.root = root;
            obj.ledger = ledger;
            obj.schedule = tuple(schedule);
            obj.router = router;
            obj.clock = router.clock;
            obj._active = None
            obj.cal_stages, obj.diag_stages = derive_technical_stages(ledger.plan.values())
            obj.binding = {**ledger.binding, 'source_hashes': _source_hashes(), 'schedule_hash': core.sha(obj.schedule),
                           'technical_stages_hash': core.sha(
                               {'calibration': obj.cal_stages, 'diagnostic': obj.diag_stages}), 'fixture_only': True}
            obj.journal = Journal(root, obj.binding);
            router.owner = obj
            obj.adapter = http.HTTPAdapter(network_enabled=False, offline_exchange=router.exchange,
                                           fixture_clock=router.clock.utcnow, fixture_monotonic=router.clock.monotonic)
            obj.pipeline = http.DispatchPipeline(ledger, obj.adapter)
            obj._check_binding();
            obj.state()
            return obj
        except BaseException:
            if 'obj' in locals() and hasattr(obj, 'journal'):
                try:
                    obj.journal.close()
                except Exception:
                    pass
            ledger.close();
            raise

    def close(self):
        self.journal.close();
        self.ledger.close();
        self.router.owner = None

    def _check_binding(self):
        if core.sha(self.ledger.config) != self.binding['config'] or core.sha(list(self.ledger.plan.values())) != \
                self.binding['plan'] or _source_hashes() != self.binding['source_hashes']: _fail(
            'IMMUTABLE_WORKFLOW_BINDING_DRIFT')
        if self.ledger.binding['authority_id'] != self.binding['authority_id']: _fail('CASH_AUTHORITY_DRIFT')
        persisted = self.journal.db.execute('SELECT binding FROM meta WHERE id=1').fetchone()
        if persisted is None or json.loads(persisted[0]) != self.binding or self.journal.binding != self.binding: _fail(
            'PERSISTED_WORKFLOW_BINDING_DRIFT')
        expected_schedule = tuple(build_schedule(self.ledger.config, list(self.ledger.plan.values())))
        if type(self.schedule) is not tuple or self.schedule != expected_schedule or core.sha(self.schedule) != \
                self.binding['schedule_hash']: _fail('EXECUTION_SCHEDULE_DRIFT')
        cal, diag = derive_technical_stages(self.ledger.plan.values())
        if type(self.cal_stages) is not tuple or type(
            self.diag_stages) is not tuple or self.cal_stages != cal or self.diag_stages != diag or core.sha(
            {'calibration': self.cal_stages, 'diagnostic': self.diag_stages}) != self.binding[
            'technical_stages_hash']: _fail('TECHNICAL_STAGE_SET_DRIFT')

    def stage_ids(self, p, phase):
        return [rid for rid in self.schedule if
                self.ledger.plan[rid]['provider'] == p and self.ledger.plan[rid]['phase'] == phase]

    def _clean(self, rid, res):
        row = self.ledger.plan[rid];
        p = row['provider']
        # Normalize once more through a fixed whitelist; never copy arbitrary fields or content.
        keys = ('model', 'service_tier', 'usage', 'api_status', 'truncated', 'price_regime', 'request_started_utc',
                'response_received_utc', 'elapsed_monotonic_seconds')
        clean = {k: copy.deepcopy(res[k]) for k in keys if k in res}
        if clean.get('model') != row['body']['model']: _fail('RECEIPT_MODEL')
        if clean.get('api_status') not in ('completed', 'incomplete'): _fail('RECEIPT_OUTCOME')
        if p == 'openai' and clean.get('service_tier') not in ('default', 'standard'): _fail('RECEIPT_TIER')
        if type(clean.get('truncated')) is not bool: _fail('RECEIPT_TRUNCATION_FLAG')
        start = datetime.fromisoformat(clean['request_started_utc']);
        end = datetime.fromisoformat(clean['response_received_utc'])
        elapsed = clean.get('elapsed_monotonic_seconds')
        if start.utcoffset() is None or end.utcoffset() is None or type(elapsed) not in (int,
                                                                                         float) or not math.isfinite(
            elapsed) or elapsed < 0 or elapsed > self.ledger.config['cadence']['timeout_seconds'] or (
                end - start).total_seconds() < 0 or abs((end - start).total_seconds() - elapsed) > 1: _fail(
            'RECEIPT_CLOCK')
        if p == 'deepseek' and http._regime(self.ledger.config, start, end, int(start.timestamp())) != clean.get(
            'price_regime'): _fail('RECEIPT_PRICE_WINDOW')
        usage = clean['usage'];
        cost = core.usage_cash(self.ledger.config, p, usage, clean.get('price_regime'))
        if cost['input_tokens'] > row['bound']['input_upper'] or cost['output_tokens'] > row['bound'][
            'output_upper']: _fail('RECEIPT_BOUND_BREACH')
        if row['phase'] == 'calibration':
            expected = self.ledger.config['offline_workflow_policy']['calibration_expected_input_tokens_by_id'][rid]
            if type(expected) is not int or cost['input_tokens'] != expected: _fail(
                'FIXTURE_CALIBRATION_INPUT_MISMATCH')
        # Restrict usage to the already validated provider schema, including optional reasoning detail.
        if p == 'openai':
            u = {'input_tokens': usage['input_tokens'],
                 'input_tokens_details': {'cached_tokens': usage['input_tokens_details']['cached_tokens'],
                                          'cache_write_tokens': usage['input_tokens_details']['cache_write_tokens']},
                 'output_tokens': usage['output_tokens']}
            if 'output_tokens_details' in usage: u['output_tokens_details'] = {
                'reasoning_tokens': usage['output_tokens_details'].get('reasoning_tokens', 0)}
        else:
            u = {k: usage[k] for k in
                 ('prompt_tokens', 'prompt_cache_hit_tokens', 'prompt_cache_miss_tokens', 'completion_tokens')}
            if 'completion_tokens_details' in usage: u['completion_tokens_details'] = {
                'reasoning_tokens': usage['completion_tokens_details'].get('reasoning_tokens', 0)}
        clean['usage'] = u
        return clean, cost

    def _receipt(self, rid, res):
        clean, cost = self._clean(rid, res);
        r = self.ledger.plan[rid]
        return {'request_id': rid, 'provider': r['provider'], 'phase': r['phase'], 'payload_hash': r['payload_hash'],
                'response': clean, 'cash': str(cost['cash']), 'input_cost': str(cost['input']),
                'output_cost': str(cost['output'])}

    def _validate_saved(self, receipt, cash):
        rid = receipt['request_id']
        if rid not in self.ledger.plan: _fail('RECEIPT_ID')
        expected = self._receipt(rid, receipt['response'])
        if receipt != expected: _fail('RECEIPT_SCHEMA_OR_VALUE')
        a = cash['attempts'].get(self.ledger._attempt(rid))
        if not a or a['status'] != 'SETTLED' or a.get('bound_breach') or a['payload_hash'] != receipt[
            'payload_hash']: _fail('RECEIPT_WITHOUT_MATCHING_SETTLED_CASH')
        if any(receipt[k] != a[k] for k in ('cash', 'input_cost', 'output_cost')): _fail('RECEIPT_CASH_MISMATCH')
        measured = core.usage_cash(self.ledger.config, self.ledger.plan[rid]['provider'], receipt['response']['usage'],
                                   receipt['response'].get('price_regime'))
        if measured['input_tokens'] != a['input_tokens'] or measured['output_tokens'] != a['output_tokens']: _fail(
            'RECEIPT_CASH_TOKEN_MISMATCH')

    def _stage_digest(self, p, phase, receipts):
        ids = self.stage_ids(p, phase)
        if not ids or any(rid not in receipts for rid in ids): _fail('INCOMPLETE_STAGE_RECEIPTS')
        return core.sha([receipts[rid] for rid in ids])

    def state(self, allow_active=False):
        self._check_binding();
        cash = self.ledger.snapshot();
        receipts = {};
        accepted = set();
        stopped = None
        for seq, kind, data, h in self.journal.events():
            if kind == 'RECEIPT':
                rid = data['request_id']
                if rid in receipts or rid != self.schedule[len(receipts)]: _fail('WORKFLOW_RECEIPT_ORDER')
                self._prereq(self.ledger.plan[rid]['phase'], accepted)
                self._validate_saved(data, cash);
                receipts[rid] = data
            elif kind == 'STAGE_ACCEPT':
                pair = (data['provider'], data['phase'])
                if pair not in self.cal_stages + self.diag_stages: _fail('UNDECLARED_STAGE_ACCEPT')
                if pair in accepted or data.get('receipt_digest') != self._stage_digest(*pair, receipts): _fail(
                    'INVALID_STAGE_ACCEPT')
                self._prereq(pair[1], accepted);
                accepted.add(pair)
            elif kind == 'STOP':
                stopped = data['reason']
            else:
                _fail('UNKNOWN_WORKFLOW_EVENT')
        for aid, a in cash['attempts'].items():
            rid = a['request_id']
            if a['status'] != 'SETTLED':
                if not (allow_active and rid == self._active and a[
                    'status'] == 'RESERVED'): stopped = stopped or 'UNSETTLED_CASH'
            elif rid not in receipts:
                stopped = stopped or 'SETTLED_CASH_RECEIPT_MISSING'
        if cash['paused']: stopped = stopped or 'PAUSED_CASH_PROVIDER'
        phase = 'SEALED'
        if all(s in accepted for s in self.cal_stages): phase = 'CALIBRATION_ACCEPTED'
        if all(s in accepted for s in self.cal_stages + self.diag_stages): phase = 'FORMAL_ELIGIBLE'
        if len(receipts) == len(self.schedule) and not stopped: phase = 'COMPLETED'
        if stopped: phase = 'STOPPED'
        return {'phase': phase, 'stopped_reason': stopped, 'receipts': receipts, 'accepted': accepted,
                'cursor': len(receipts),
                'next_id': self.schedule[len(receipts)] if len(receipts) < len(self.schedule) else None}

    def _prereq(self, phase, accepted):
        if phase in ('pilot', 'shape', 'formal') and not all(s in accepted for s in self.cal_stages): _fail(
            'CALIBRATION_GATE_CLOSED')
        if phase == 'formal' and not all(s in accepted for s in self.diag_stages): _fail('DIAGNOSTIC_GATE_CLOSED')

    def accept_stage(self, provider, phase):
        if (provider, phase) not in self.cal_stages + self.diag_stages: _fail('ONLY_DECLARED_TECHNICAL_STAGES')
        s = self.state()
        if s['stopped_reason']: _fail('STOPPED_WORKFLOW')
        self._prereq(phase, s['accepted'])
        if (provider, phase) in s['accepted']: _fail('STAGE_ACCEPT_ALREADY_RECORDED')
        digest = self._stage_digest(provider, phase, s['receipts'])
        self.journal.append('STAGE_ACCEPT', {'provider': provider, 'phase': phase, 'receipt_digest': digest})
        return digest

    def _transport_guard(self, rid, permit):
        if self._active != rid or permit.ledger is not self.ledger: _fail('DIRECT_TRANSPORT_BYPASS')
        s = self.state(allow_active=True)
        if s['stopped_reason'] or s['next_id'] != rid: _fail('TRANSPORT_CURSOR_OR_STOP')
        self._prereq(self.ledger.plan[rid]['phase'], s['accepted'])

    def step(self, rid=None):
        s = self.state()
        if s['stopped_reason']: _fail('STOPPED_WORKFLOW')
        wanted = s['next_id']
        if wanted is None: _fail('WORKFLOW_ALREADY_COMPLETE')
        if rid is not None and rid != wanted: _fail('GLOBAL_CURSOR_MISMATCH')
        row = self.ledger.plan[wanted];
        self._prereq(row['phase'], s['accepted'])
        if s['receipts']:
            prev = self.ledger.plan[self.schedule[s['cursor'] - 1]]
            same = prev.get('block_id') == row.get('block_id') and row.get('block_id') is not None
            gap = self.ledger.config['cadence']['min_seconds_after_response'] if same and prev['arm'] == row['arm'] else \
            self.ledger.config['cadence']['arm_gap_seconds'] if same else self.ledger.config['cadence'][
                'block_gap_seconds']
            end = datetime.fromisoformat(s['receipts'][prev['request_id']]['response']['response_received_utc'])
            elapsed = (self.clock.utcnow() - end).total_seconds()
            if elapsed < 0: _fail('FIXTURE_CLOCK_ROLLBACK')
            self.clock.sleep(max(0, gap - elapsed))
        self._active = wanted
        try:
            response = self.pipeline.send(wanted)
            receipt = self._receipt(wanted, response)
            self.journal.append('RECEIPT', receipt)
            return receipt
        except BaseException:
            try:
                self.ledger.pause(row['provider'], 'OFFLINE_COORDINATOR_STOP')
            except Exception:
                pass
            try:
                self.journal.append('STOP', {'reason': 'DISPATCH_BILL_OR_RECEIPT_FAILURE', 'request_id': wanted})
            except Exception:
                pass
            raise WorkflowError('DISPATCH_STOPPED_WITH_CASH_RETAINED') from None
        finally:
            self._active = None

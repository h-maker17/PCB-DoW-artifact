"""No network or credentials. One project cash authority, strict all-stage inventory."""
from pathlib import Path
import copy
import hashlib
import json
import sqlite3
import uuid
from decimal import Decimal
from datetime import datetime, timezone
import offline_prepare as structure

ROOT = Path(__file__).resolve().parents[1]


class GateError(RuntimeError): pass


def canonical(x): return json.dumps(x, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def sha(x): return hashlib.sha256(x if isinstance(x, bytes) else canonical(x).encode()).hexdigest()


def money(x):
    try:
        d = Decimal(str(x))
    except Exception:
        raise GateError('INVALID_AMOUNT') from None
    if not d.is_finite() or d < 0: raise GateError('INVALID_AMOUNT')
    return d


def request_body(config, provider, doc, arm, replica, run_salt, phase='formal'):
    p = config['providers'][provider]
    if config['corpus']['grounding_words'] != 32: raise GateError('UNSUPPORTED_STRUCTURE')
    words = config['corpus']['pilot_words_by_provider'][provider] if phase == 'pilot' else \
    config['corpus']['words_by_provider_doc'][provider][str(doc['doc_id'])]
    body = copy.deepcopy(p['request_template'])
    body['input' if provider == 'openai' else 'messages'] = structure.messages(provider, doc, arm, replica, run_salt,
                                                                               words)
    return body


def max_cash(config, provider, input_upper, output_upper):
    if type(input_upper) is not int or type(output_upper) is not int: raise GateError('INVALID_TOKEN_BOUND')
    p = config['providers'][provider];
    rates = p.get('price_per_million', p.get('price_per_million_peak'))
    keys = ('ordinary', 'read', 'write') if provider == 'openai' else ('hit', 'miss')
    if p.get('fee_surcharge_multiplier') is None: raise GateError('UNVERIFIED_SURCHARGE')
    return (money(input_upper) * max(money(rates[k]) for k in keys) + money(output_upper) * money(
        rates['output'])) * money(p['fee_surcharge_multiplier']) / 1000000


def _mapping(value):
    if not isinstance(value, dict): raise GateError('MALFORMED_MAPPING')
    return value


def _count(value):
    if type(value) is not int or value < 0: raise GateError('MALFORMED_TOKEN_COUNT')
    return value


def usage_cash(config, provider, usage, regime=None):
    usage = _mapping(usage);
    p = config['providers'][provider]
    if provider == 'openai':
        total = _count(usage['input_tokens']);
        rates = p['price_per_million']
        if config.get('bootstrap_scope'):
            # 2500 is safely within the published short range. No exact 272K
            # interpretation is invented; larger responses retain the full reserve.
            if total > config['corpus']['maximum_input_tokens']: raise GateError('OPENAI_PRICE_RANGE_UNRESOLVED')
        d = _mapping(usage['input_tokens_details'])
        reads = _count(d['cached_tokens']);
        writes = _count(d['cache_write_tokens'])
        if reads + writes > total: raise GateError('INPUT_CONSERVATION')
        output = _count(usage['output_tokens']);
        reasoning = _count(_mapping(usage.get('output_tokens_details', {})).get('reasoning_tokens', 0))
        cost = money(total - reads - writes) * money(rates['ordinary']) + money(reads) * money(rates['read']) + money(
            writes) * money(rates['write'])
    else:
        if regime not in ('peak', 'offpeak'): raise GateError('UNKNOWN_PRICE_WINDOW')
        rates = p['price_per_million_' + regime];
        total = _count(usage['prompt_tokens']);
        hit = _count(usage['prompt_cache_hit_tokens']);
        miss = _count(usage['prompt_cache_miss_tokens'])
        if hit + miss != total: raise GateError('INPUT_CONSERVATION')
        output = _count(usage['completion_tokens']);
        reasoning = _count(_mapping(usage.get('completion_tokens_details', {})).get('reasoning_tokens', 0))
        cost = money(hit) * money(rates['hit']) + money(miss) * money(rates['miss'])
    if reasoning > output: raise GateError('OUTPUT_CONSERVATION')
    factor = p.get('fee_surcharge_multiplier')
    if factor is None: raise GateError('UNVERIFIED_SURCHARGE')
    input_cost = cost * money(factor) / 1000000;
    output_cost = money(output) * money(rates['output']) * money(factor) / 1000000
    return {'input': input_cost, 'output': output_cost, 'cash': input_cost + output_cost, 'input_tokens': total,
            'output_tokens': output}


class FakeTransport:
    def __init__(self, response=None, error=None): self.response = response; self.error = error; self.calls = 0

    def send(self, body):
        self.calls += 1
        if self.error: raise self.error
        return copy.deepcopy(self.response)


class Ledger:
    """All sessions/providers/phases share ROOT/logs/project_cash.sqlite."""

    @staticmethod
    def authority_path():
        return (ROOT / 'logs/project_cash.sqlite').resolve()

    def __init__(self, path, config, plan, recover=True):
        self.path = Path(path).resolve()
        if self.path != self.authority_path(): raise GateError('NONCANONICAL_CASH_AUTHORITY')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.config = copy.deepcopy(config);
        self.plan = {x['request_id']: copy.deepcopy(x) for x in plan}
        if len(self.plan) != len(plan): raise GateError('DUPLICATE_REQUEST_ID')
        self.binding = {'config': sha(config), 'plan': sha(plan), 'session_id': config['session_id']}
        self._check_inventory()
        anchor = (ROOT / 'config/cash_authority.json').resolve()
        anchor.parent.mkdir(parents=True, exist_ok=True)
        existing_authority = anchor.exists()
        if existing_authority:
            registered = json.loads(anchor.read_text(encoding='utf-8'))
            if registered['path'] != str(self.path) or not self.path.is_file(): raise GateError(
                'CASH_AUTHORITY_MISSING_OR_RELOCATED')
            if self.path.stat().st_size == 0: raise GateError('EXISTING_CASH_AUTHORITY_EMPTY')
        else:
            if self.path.exists(): raise GateError('UNREGISTERED_EXISTING_LEDGER')
            registered = {'path': str(self.path), 'authority_id': uuid.uuid4().hex}
            with anchor.open('x', encoding='utf-8') as f:
                f.write(canonical(registered))
        self.binding['authority_id'] = registered['authority_id']
        self.db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        if existing_authority:
            try:
                names = {row[0] for row in self.db.execute('SELECT name FROM sqlite_master WHERE type=?', ('table',))}
                if not {'meta', 'events'}.issubset(names): raise GateError('EXISTING_CASH_SCHEMA_MISSING')
                old = self.db.execute('SELECT binding FROM meta WHERE id=1').fetchone()
                if old is None: raise GateError('EXISTING_CASH_METADATA_MISSING')
                if json.loads(old[0]) != self.binding: raise GateError('PROTOCOL_MIGRATION_DISABLED')
                self.snapshot()
            except BaseException:
                self.db.close();
                raise GateError('EXISTING_CASH_AUTHORITY_INVALID') from None
        self.db.execute('PRAGMA journal_mode=WAL');
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS meta(id INTEGER PRIMARY KEY CHECK(id=1),binding TEXT NOT NULL)')
        self.db.execute(
            'CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT,kind TEXT NOT NULL,data TEXT NOT NULL,prev TEXT NOT NULL,hash TEXT NOT NULL)')
        for operation in ('UPDATE', 'DELETE'):
            self.db.execute(
                f"CREATE TRIGGER IF NOT EXISTS immutable_event_{operation.lower()} BEFORE {operation} ON events BEGIN SELECT RAISE(ABORT,'immutable events'); END")
        self.db.execute('BEGIN IMMEDIATE')
        try:
            row = self.db.execute('SELECT binding FROM meta WHERE id=1').fetchone()
            if row is None:
                if existing_authority: raise GateError('EXISTING_CASH_METADATA_MISSING')
                self.db.execute('INSERT INTO meta VALUES(1,?)', (canonical(self.binding),))
            elif json.loads(row[0]) != self.binding:
                raise GateError('REVISION_REQUIRES_APPROVED_CARRY_FORWARD')
            if recover:
                for attempt, a in self.snapshot()['attempts'].items():
                    if a['status'] == 'RESERVED': self._append('UNKNOWN',
                                                               {'attempt': attempt, 'reason': 'RESTART_PENDING'})
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK');
            self.db.close();
            raise

    def _check_inventory(self):
        inventory = self.config.get('stage_inventory')
        if not isinstance(inventory, dict): raise GateError('MISSING_ALL_STAGE_INVENTORY')
        for provider, p in self.config['providers'].items():
            if provider not in inventory: raise GateError('MISSING_PROVIDER_INVENTORY')
            for phase, pc in p['phases'].items():
                ids = [rid for rid, r in self.plan.items() if r['provider'] == provider and r['phase'] == phase]
                declaration = inventory[provider].get(phase)
                if not isinstance(declaration, dict) or declaration.get('request_ids') != ids or len(ids) != pc[
                    'max_attempts']: raise GateError('INCOMPLETE_STAGE_INVENTORY')
                if phase == 'count' and ids: raise GateError('AUTHENTICATED_COUNT_DISABLED')
            if any(r['phase'] not in p['phases'] for r in self.plan.values() if
                   r['provider'] == provider): raise GateError('UNKNOWN_PHASE')
        if any(r['provider'] not in self.config['providers'] for r in self.plan.values()): raise GateError(
            'UNKNOWN_PROVIDER')

    def close(self):
        self.db.close()

    def _attempt(self, rid):
        recovery = getattr(self, '_recovery', None)
        return recovery.attempt_id(rid) if recovery is not None else self.binding['session_id'] + '|' + rid

    def recovery_override(self, provider):
        recovery = getattr(self, '_recovery', None)
        return recovery is not None and recovery.can_override(provider)

    def _append(self, kind, data):
        row = self.db.execute('SELECT hash FROM events ORDER BY seq DESC LIMIT 1').fetchone();
        prev = row[0] if row else '0' * 64
        stamp = {'at_utc': datetime.now(timezone.utc).isoformat(), **data};
        h = sha({'kind': kind, 'data': stamp, 'prev': prev})
        self.db.execute('INSERT INTO events(kind,data,prev,hash) VALUES(?,?,?,?)', (kind, canonical(stamp), prev, h))

    def snapshot(self):
        attempts = {};
        paused = set();
        prev = '0' * 64
        for kind, text, prior, h in self.db.execute('SELECT kind,data,prev,hash FROM events ORDER BY seq'):
            data = json.loads(text)
            if prior != prev or sha({'kind': kind, 'data': data, 'prev': prior}) != h: raise GateError(
                'LEDGER_INTEGRITY')
            prev = h
            if kind == 'REVISION': continue
            if kind == 'PAUSE': paused.add(data['provider']); continue
            aid = data['attempt']
            if kind == 'RESERVE':
                if aid in attempts: raise GateError('DUPLICATE_RESERVE')
                attempts[aid] = {**data, 'status': 'RESERVED'}
            elif kind == 'TRANSPORT_START':
                if aid not in attempts or attempts[aid]['status'] != 'RESERVED' or attempts[aid].get(
                    'transport_consumed'): raise GateError('INVALID_TRANSPORT_START')
                attempts[aid]['transport_consumed'] = True
            elif kind == 'UNKNOWN':
                attempts[aid]['status'] = 'UNKNOWN'; paused.add(attempts[aid]['provider'])
            elif kind == 'EXTERNAL_BILL_RECONCILIATION':
                if aid not in attempts or attempts[aid]['status'] != 'UNKNOWN' or attempts[aid][
                    'provider'] != 'deepseek': raise GateError('EXTERNAL_RECONCILIATION_STATE')
                if data.get('basis') != 'VENDOR_EXPORT_MATCHED_ARCHIVED_RESPONSE' or data.get(
                    'clock_criterion') != 'FAILED': raise GateError('EXTERNAL_RECONCILIATION_BASIS')
                if money(data.get('cash')) > money(attempts[aid]['reserve']) or money(data.get('input_cost')) + money(
                    data.get('output_cost')) != money(data.get('cash')): raise GateError(
                    'EXTERNAL_RECONCILIATION_AMOUNT')
                attempts[aid].update(data);
                attempts[aid]['status'] = 'EXTERNALLY_RECONCILED'
            elif kind == 'SETTLE':
                attempts[aid].update(data);
                attempts[aid]['status'] = 'SETTLED'
                if data.get('bound_breach'): paused.add(attempts[aid]['provider'])
            else:
                raise GateError('UNKNOWN_EVENT')
        return {'attempts': attempts, 'paused': paused}

    def totals(self, provider, phase=None):
        confirmed = Decimal(0);
        pending = Decimal(0);
        count = 0
        for a in self.snapshot()['attempts'].values():
            if a['provider'] != provider or phase is not None and a['phase'] != phase: continue
            count += 1
            if a['status'] in ('SETTLED', 'EXTERNALLY_RECONCILED'):
                confirmed += money(a['cash'])
            else:
                pending += money(a['reserve'])
        return confirmed, pending, count

    def _bootstrap_gate(self):
        import bootstrap_policy, runtime_gate
        gate = getattr(self, '_runtime_gate', None)
        if type(gate) is not runtime_gate.RuntimeGate or gate.ledger is not self: raise GateError(
            'BOUND_RUNTIME_GATE_REQUIRED')
        return gate

    def _bound(self, item):
        import bootstrap_policy
        if bootstrap_policy.enabled(self.config): return bootstrap_policy.bound(self._bootstrap_gate(), item)
        return item['bound']

    def _validate_item(self, item):
        import bootstrap_policy
        if not bootstrap_policy.enabled(self.config): return self._validate_commercial_item(item)
        gate = self._bootstrap_gate();
        b = bootstrap_policy.bound(gate, item)
        if item['phase'] == 'calibration': return bootstrap_policy.RESERVE[item['provider']]
        clone = copy.deepcopy(item);
        clone['bound'] = b
        return self._validate_commercial_item(clone)

    def _validate_commercial_item(self, item):
        if item['phase'] == 'count': raise GateError('AUTHENTICATED_COUNT_DISABLED')
        p = self.config['providers'][item['provider']]
        if item['body']['model'] != p['candidate_model'] or sha(item['body']) != item['payload_hash']: raise GateError(
            'PAYLOAD_MODEL_DRIFT')
        field = 'input' if item['provider'] == 'openai' else 'messages'
        actual = copy.deepcopy(item['body']);
        expected = copy.deepcopy(p['request_template']);
        actual.pop(field, None);
        expected.pop(field, None)
        if actual != expected: raise GateError('REQUEST_PARAMETERS_DRIFT')
        e = _mapping(item['bound']);
        file = (ROOT / e['evidence_path']).resolve()
        if not file.is_relative_to(ROOT.resolve()) or file.suffix != '.json': raise GateError('EVIDENCE_PATH')
        parts = file.relative_to(ROOT.resolve()).parts
        fixture = len(parts) >= 3 and parts[0] == 'tmp' and parts[1].startswith(
            'ledger-fixture-') and file.name.startswith('bound-')
        official = len(parts) >= 3 and parts[:2] == ('config', 'token_bounds')
        if not (fixture or official) or any(
            s in file.name.lower() for s in ('api_key', 'secret', 'credential')): raise GateError(
            'FORBIDDEN_EVIDENCE_PATH')
        raw = file.read_bytes();
        evidence = _mapping(json.loads(raw))
        if hashlib.sha256(raw).hexdigest() != e['evidence_hash']: raise GateError('EVIDENCE_DRIFT')
        if evidence.get('fixture') and self.config['runtime_mode'] != 'OFFLINE_FIXTURE': raise GateError(
            'FIXTURE_NOT_AUTHORITY')
        for name, value in [('payload_hash', item['payload_hash']), ('config_hash', self.binding['config']),
                            ('input_upper', e['input_upper'])]:
            if evidence.get(name) != value or e.get(name) != value: raise GateError('EVIDENCE_BINDING')
        if not e['verified'] or _count(e['input_upper']) > self.config['corpus'][
            'maximum_input_tokens']: raise GateError('UNVERIFIED_INPUT_BOUND')
        out = item['body']['max_output_tokens' if item['provider'] == 'openai' else 'max_tokens']
        if out != e['output_upper'] or out != p.get('max_output_tokens', p.get('max_tokens')): raise GateError(
            'OUTPUT_BOUND')
        return max_cash(self.config, item['provider'], e['input_upper'], e['output_upper'])

    def _require_new_request_permission(self):
        runtime = getattr(self, '_runtime_gate', None)
        if runtime is not None: runtime.require_new_request_permission()
        recovery = getattr(self, '_recovery', None)
        if recovery is not None and getattr(recovery, 'new_requests_allowed', True) is False: raise GateError(
            'HISTORY_ONLY_NO_NEW_REQUESTS')

    def _admission(self, rid):
        self._require_new_request_permission()
        c = self.config
        if sha(c) != self.binding['config']: raise GateError('CONFIG_DRIFT')
        if not all(c[x] for x in ('frozen', 'paid_enabled', 'independent_review_approved',
                                  'credentials_configured')): raise GateError('CLOSED_GATES')
        item = self.plan[rid];
        provider = item['provider'];
        phase = item['phase'];
        p = c['providers'][provider]
        if phase == 'count': raise GateError('AUTHENTICATED_COUNT_DISABLED')
        import bootstrap_policy
        scoped = bootstrap_policy.enabled(c)
        if scoped:
            self._bootstrap_gate().cash_admission(rid)
        elif not all(p[x] for x in
                     ('account_currency_verified', 'output_limit_verified', 'rendered_input_bound_verified',
                      'input_bound_evidence_verified')):
            raise GateError('UNVERIFIED_PROVIDER')
        state = self.snapshot();
        aid = self._attempt(rid)
        recovery = getattr(self, '_recovery', None)
        exempt = self.recovery_override(provider)
        if provider in state['paused'] and not exempt or any(
            a['provider'] == provider and a['status'] not in ('SETTLED', 'EXTERNALLY_RECONCILED') and not (
                    exempt and recovery.legacy(key)) for key, a in state['attempts'].items()): raise GateError(
            'PAUSED_OR_UNSETTLED_PROVIDER')
        if aid in state['attempts']: raise GateError('ZERO_RETRY')
        cap = self._validate_item(item);
        confirmed, pending, _ = self.totals(provider);
        pc, pp, num = self.totals(provider, phase);
        phase_cfg = p['phases'][phase]
        allowance = recovery.extra_attempt(provider, phase) if recovery is not None else 0
        if num >= phase_cfg['max_attempts'] + allowance or pc + pp + cap > money(
            phase_cfg['cash_subcap']): raise GateError('PHASE_CAP')
        admitted = None if not scoped else self._bootstrap_gate().admitted_ids()
        remaining = sum((self._validate_item(r) for key, r in self.plan.items() if
                         r['provider'] == provider and self._attempt(key) not in state['attempts'] and key != rid and (
                                     admitted is None or key in admitted)), Decimal(0))
        if scoped and phase == 'calibration': remaining = Decimal(
            0)  # rolling one original calibration, not a zero-cost future stage
        if confirmed + pending + cap + remaining > min(money(p['dispatch_stop']),
                                                       money(p['hard_cap'])): raise GateError('ALL_STAGE_BUDGET')
        return cap, item, aid

    def preview(self, rid):
        self._require_new_request_permission()
        self.db.execute('BEGIN IMMEDIATE')
        try:
            return self._admission(rid)[0]
        finally:
            self.db.execute('ROLLBACK')

    def reserve(self, rid):
        self._require_new_request_permission()
        self.db.execute('BEGIN IMMEDIATE')
        try:
            cap, item, aid = self._admission(rid);
            p = self.config['providers'][item['provider']]
            self._append('RESERVE', {'attempt': aid, 'request_id': rid, 'session_id': self.binding['session_id'],
                                     'provider': item['provider'], 'currency': p['currency'], 'phase': item['phase'],
                                     'payload_hash': item['payload_hash'], 'reserve': str(cap), **(
                    self._recovery.reserve_metadata(rid) if getattr(self, '_recovery', None) is not None else {})})
            self.db.execute('COMMIT');
            return cap
        except BaseException:
            self.db.execute('ROLLBACK'); raise

    def consume_transport(self, rid, payload_hash):
        self._require_new_request_permission()
        # Durable single-use authorization; commit before physical transmission.
        self.db.execute('BEGIN IMMEDIATE')
        try:
            if sha(self.config) != self.binding['config']: raise GateError('CONFIG_DRIFT')
            state = self.snapshot();
            aid = self._attempt(rid);
            a = state['attempts'].get(aid)
            if not a or a['status'] != 'RESERVED' or a.get('transport_consumed'): raise GateError(
                'TRANSPORT_ALREADY_CONSUMED_OR_UNAVAILABLE')
            if a['provider'] in state['paused'] and not self.recovery_override(a['provider']) or a[
                'payload_hash'] != payload_hash or self.plan[rid]['payload_hash'] != payload_hash: raise GateError(
                'TRANSPORT_BINDING')
            self._append('TRANSPORT_START', {'attempt': aid})
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK'); raise

    def pause(self, provider, reason):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self._append('PAUSE', {'provider': provider, 'reason': reason});
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK'); raise

    def unknown(self, rid, reason):
        self._require_new_request_permission()
        self.db.execute('BEGIN IMMEDIATE')
        try:
            aid = self._attempt(rid);
            a = self.snapshot()['attempts'][aid]
            if a['status'] in ('SETTLED', 'EXTERNALLY_RECONCILED'): raise GateError('SETTLED_ALREADY')
            self._append('UNKNOWN', {'attempt': aid, 'reason': reason});
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK'); raise

    def settle(self, rid, response):
        self._require_new_request_permission()
        item = self.plan[rid];
        p = self.config['providers'][item['provider']]
        try:
            if sha(self.config) != self.binding['config']: raise GateError('SETTLEMENT_CONFIG_DRIFT')
            response = _mapping(response)
            import bootstrap_policy
            if bootstrap_policy.enabled(self.config): self._bootstrap_gate().check_settlement(rid, response)
            if response.get('model') != p['candidate_model']: raise GateError('MODEL_DRIFT')
            if item['provider'] == 'openai' and response.get('service_tier') not in ('default',
                                                                                     'standard'): raise GateError(
                'TIER_DRIFT')
            cost = usage_cash(self.config, item['provider'], response['usage'], response.get('price_regime'))
        except Exception:
            self.unknown(rid, 'MALFORMED_OR_UNKNOWN_BILL');
            raise GateError('SETTLEMENT_PARSE_FAILED') from None
        self.db.execute('BEGIN IMMEDIATE')
        try:
            aid = self._attempt(rid);
            a = self.snapshot()['attempts'][aid]
            if a['status'] in ('SETTLED', 'EXTERNALLY_RECONCILED'): raise GateError('SETTLED_ALREADY')
            bound = self._bound(item)
            breach = cost['cash'] > money(a['reserve']) or cost['input_tokens'] > bound['input_upper'] or cost[
                'output_tokens'] > bound['output_upper']
            self._append('SETTLE',
                         {'attempt': aid, 'input_cost': str(cost['input']), 'output_cost': str(cost['output']),
                          'cash': str(cost['cash']), 'bound_breach': breach, 'input_tokens': cost['input_tokens'],
                          'output_tokens': cost['output_tokens']});
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK')
            # Persist pause if database is still writable; unresolved reserve also blocks next send.
            try:
                self.unknown(rid, 'SETTLEMENT_STORAGE_FAILED')
            except Exception:
                pass
            raise GateError('SETTLEMENT_STORAGE_FAILED') from None
        if breach: raise GateError('BOUND_BREACH_ACCOUNTED')

    def send_offline(self, rid, transport):
        self._require_new_request_permission()
        if type(transport) is not FakeTransport: raise GateError('ONLY_FAKE_SUPPORTED')
        self.reserve(rid)
        try:
            response = transport.send(self.plan[rid]['body'])
        except BaseException:
            self.unknown(rid, 'TRANSPORT_FAILED'); raise GateError('TRANSPORT_FAILED') from None
        self.settle(rid, response)
        return {'request_id': rid, 'settled': True}

    def approve_revision(self, new_config, new_plan, approval_path):
        """Unsupported in this experiment: never change an outstanding bill's tariff."""
        raise GateError('PROTOCOL_MIGRATION_DISABLED')

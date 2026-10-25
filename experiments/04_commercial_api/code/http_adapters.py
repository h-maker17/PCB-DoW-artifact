"""Standard-library adapters; network off by default, never reads a key file/env."""
import copy
import hashlib
from pathlib import Path
import json
import sqlite3
import ssl
import time
import math
from datetime import datetime, timezone, timedelta
import urllib.request
import urllib.error
import protocol_core as core

URLS = {'openai': 'https://api.openai.com/v1/responses', 'deepseek': 'https://api.deepseek.com/chat/completions'}
_SEAL = object()


class AdapterFailure(RuntimeError):
    def __init__(self, code): self.code = code; super().__init__(code)


class Credential:
    def __init__(self, value, provider=None): self._value = value; self.provider = provider

    def __repr__(self): return '<Credential redacted>'

    def __str__(self): return '<Credential redacted>'

    def clear(self): self._value = None


class Permit:
    def __init__(self, seal, ledger, rid, runtime_gate=None):
        if seal is not _SEAL: raise core.GateError('PERMIT_NOT_ISSUED')
        self.ledger = ledger;
        self.rid = rid;
        self.seal = seal;
        self.runtime_gate = runtime_gate

    def check(self, body):
        db = self.ledger
        if self.seal is not _SEAL or db.db.in_transaction: raise core.GateError('RESERVE_NOT_COMMITTED')
        if core.sha(db.config) != db.binding['config']: raise core.GateError('CONFIG_DRIFT')
        row = db.plan[self.rid];
        a = db.snapshot()['attempts'].get(db._attempt(self.rid))
        if not a or a['status'] != 'RESERVED' or a['payload_hash'] != core.sha(body) or core.sha(body) != row[
            'payload_hash']: raise core.GateError('NO_MATCHING_DURABLE_RESERVE')
        if a.get('transport_consumed'): raise core.GateError('TRANSPORT_ALREADY_CONSUMED_OR_UNAVAILABLE')
        if row['phase'] == 'count': raise core.GateError('AUTHENTICATED_COUNT_DISABLED')
        if row['provider'] in db.snapshot()['paused'] and not getattr(db, 'recovery_override', lambda p: False)(
            row['provider']): raise core.GateError('PAUSED_PROVIDER')
        return row


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs): return None


def _integer(value):
    if type(value) is not int or value < 0: raise AdapterFailure('INVALID_USAGE')
    return value


def normalize_openai(raw, expected_model):
    if not isinstance(raw, dict) or raw.get('model') != expected_model: raise AdapterFailure('MODEL_DRIFT')
    u = raw.get('usage')
    if not isinstance(u, dict) or not isinstance(u.get('input_tokens_details'), dict): raise AdapterFailure(
        'MISSING_USAGE')
    d = u['input_tokens_details']
    usage = {'input_tokens': _integer(u.get('input_tokens')),
             'input_tokens_details': {'cached_tokens': _integer(d.get('cached_tokens')),
                                      'cache_write_tokens': _integer(d.get('cache_write_tokens'))},
             'output_tokens': _integer(u.get('output_tokens'))}
    if 'output_tokens_details' in u:
        od = u['output_tokens_details']
        if not isinstance(od, dict): raise AdapterFailure('INVALID_USAGE')
        if 'reasoning_tokens' in od: usage['output_tokens_details'] = {
            'reasoning_tokens': _integer(od['reasoning_tokens'])}
    tier = raw.get('service_tier')
    if tier not in ('default', 'standard'): raise AdapterFailure('TIER_DRIFT')
    status = raw.get('status')
    if status not in ('completed', 'incomplete'): raise AdapterFailure('UNSUPPORTED_API_OUTCOME')
    result = {'model': expected_model, 'service_tier': tier, 'usage': usage, 'api_status': status}
    if status == 'incomplete':
        reason = raw.get('incomplete_details', {})
        result['truncated'] = isinstance(reason, dict) and reason.get('reason') == 'max_output_tokens'
    else:
        result['truncated'] = False
    # Never copy output text, headers, errors, arbitrary fields or credentials.
    return result


def _regime(config, start, end, created):
    calendar = config['providers']['deepseek'].get('billing_calendar', {})
    if calendar.get('verified') is not True: raise AdapterFailure('PRICE_WINDOW_UNKNOWN')
    if type(created) is not int: raise AdapterFailure('PRICE_WINDOW_UNKNOWN')
    tolerance = calendar.get('clock_tolerance_seconds', 0)
    if type(tolerance) is not int or tolerance < 0: raise AdapterFailure('PRICE_WINDOW_UNKNOWN')
    if end < start: raise AdapterFailure('PRICE_CLOCK_MISMATCH')
    delta = timedelta(seconds=tolerance);
    created_dt = datetime.fromtimestamp(created, timezone.utc)
    if not start - delta <= created_dt <= end + delta: raise AdapterFailure('PRICE_CLOCK_MISMATCH')

    def classify(stamp):
        local = stamp.astimezone(timezone(timedelta(hours=8)));
        date = local.date().isoformat()
        dates = calendar.get('holiday_dates_verified', {})
        if date not in dates or type(dates[date]) is not bool: raise AdapterFailure('PRICE_CALENDAR_UNKNOWN')
        minute = local.hour * 60 + local.minute
        peak = local.weekday() < 5 and not dates[date] and (540 <= minute < 720 or 840 <= minute < 1080)
        return 'peak' if peak else 'offpeak'

    # Inspect every internal schedule and midnight boundary, including both sides.
    lower = start - delta;
    upper = end + delta;
    points = {lower, upper, created_dt}
    local_zone = timezone(timedelta(hours=8))
    day = lower.astimezone(local_zone).replace(hour=0, minute=0, second=0, microsecond=0)
    while day <= upper:
        for minute in (0, 540, 720, 840, 1080, 1440):
            boundary = day + timedelta(minutes=minute)
            for point in (boundary, boundary - timedelta(microseconds=1)):
                if lower <= point <= upper: points.add(point)
        day += timedelta(days=1)
    results = {classify(t) for t in points}
    if len(results) != 1: raise AdapterFailure('PRICE_WINDOW_CROSSING')
    return results.pop()


def normalize_deepseek(raw, expected_model, config, start, end):
    if not isinstance(raw, dict) or raw.get('model') != expected_model: raise AdapterFailure('MODEL_DRIFT')
    u = raw.get('usage')
    if not isinstance(u, dict): raise AdapterFailure('MISSING_USAGE')
    usage = {k: _integer(u.get(k)) for k in
             ('prompt_tokens', 'prompt_cache_hit_tokens', 'prompt_cache_miss_tokens', 'completion_tokens')}
    if 'completion_tokens_details' in u:
        detail = u['completion_tokens_details']
        if not isinstance(detail, dict): raise AdapterFailure('INVALID_USAGE')
        if 'reasoning_tokens' in detail: usage['completion_tokens_details'] = {
            'reasoning_tokens': _integer(detail['reasoning_tokens'])}
    choices = raw.get('choices')
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict): raise AdapterFailure(
        'MISSING_FINISH_REASON')
    finish = choices[0].get('finish_reason')
    if finish not in ('stop', 'length'): raise AdapterFailure('UNSUPPORTED_API_OUTCOME')
    return {'model': expected_model, 'usage': usage, 'api_status': 'completed', 'truncated': finish == 'length',
            'price_regime': _regime(config, start, end, raw.get('created'))}


def observation_fields(raw, provider, start, end, elapsed, credential=None):
    # Only identifiers and numeric billing fields, never output/error/header text.
    # Unknown or malformed values remain explicitly absent, hence cannot settle.
    result = {'request_started_utc': start.isoformat(), 'response_received_utc': end.isoformat(),
              'elapsed_monotonic_seconds': elapsed}
    if not isinstance(raw, dict): return result
    for key in ('model', 'service_tier', 'status'):
        value = raw.get(key)
        if isinstance(value, str) and len(value) <= 128 and all(ch.isalnum() or ch in '-_./' for ch in value) and (
                credential is None or not credential._value or credential._value not in value): result[key] = value
    if type(raw.get('created')) is int: result['created'] = raw['created']
    u = raw.get('usage')
    if isinstance(u, dict):
        keys = ('input_tokens', 'output_tokens', 'total_tokens') if provider == 'openai' else ('prompt_tokens',
                                                                                               'prompt_cache_hit_tokens',
                                                                                               'prompt_cache_miss_tokens',
                                                                                               'completion_tokens',
                                                                                               'total_tokens')
        clean = {k: u[k] for k in keys if type(u.get(k)) is int}
        detail_keys = ('input_tokens_details', 'output_tokens_details') if provider == 'openai' else (
            'completion_tokens_details',)
        for key in detail_keys:
            value = u.get(key)
            if isinstance(value, dict): clean[key] = {k: value[k] for k in
                                                      ('cached_tokens', 'cache_write_tokens', 'reasoning_tokens') if
                                                      type(value.get(k)) is int}
        result['usage'] = clean
    if provider == 'deepseek':
        choices = raw.get('choices')
        if isinstance(choices, list) and choices and isinstance(choices[0], dict) and choices[0].get(
            'finish_reason') in ('stop', 'length'): result['finish_reason'] = choices[0]['finish_reason']
    elif isinstance(raw.get('incomplete_details'), dict) and raw['incomplete_details'].get(
        'reason') == 'max_output_tokens':
        result['incomplete_reason'] = 'max_output_tokens'
    return result


SAFE_ERROR_CODES = frozenset(
    ('ACTIVE_PREFLIGHT', 'ADVANCE_CONTINUATION_NOT_AUTHORIZED', 'ATTEMPT_FAILED_RESERVE_RETAINED',
     'AUTHENTICATED_COUNT_DISABLED', 'BOOTSTRAP_BUDGET_AUTHORIZATION_REQUIRED', 'BOOTSTRAP_CONTEXT_PRICE_CONTRACT',
     'BOOTSTRAP_CONTRACT_POLICY_REQUIRED', 'BOOTSTRAP_EVIDENCE_PATH', 'BOOTSTRAP_PHASE_CAP',
     'BOOTSTRAP_PLAN_OR_CADENCE_DRIFT', 'BOOTSTRAP_PRIORS_REQUIRED', 'BOOTSTRAP_PRIOR_BINDING',
     'BOOTSTRAP_PRIOR_SOURCE_COVERAGE', 'BOOTSTRAP_PRIOR_SOURCE_DRIFT', 'BOOTSTRAP_PROVIDER_CONTRACT',
     'BOOTSTRAP_WHITELIST_DENIED', 'BOUND_ACCEPTANCE_INVALID', 'BOUND_RUNTIME_GATE_REQUIRED', 'CADENCE_ANCHOR_MISMATCH',
     'CADENCE_CLOCK_ANOMALY_STOPPED', 'CADENCE_CLOCK_INVALID', 'CADENCE_SLEEP_OR_CLOCK_MISMATCH',
     'CADENCE_WALL_MONOTONIC_MISMATCH', 'CALIBRATION_EXPECTATION_OUTSIDE_PROVEN_BOUND', 'CALIBRATION_GATE_CLOSED',
     'CALIBRATION_OBSERVATIONS_INCOMPLETE', 'CASH_AUTHORITY_DRIFT', 'CASH_BINDING_DRIFT', 'CASH_REGISTRATION_DRIFT',
     'COMMERCIAL_CALIBRATION_CRITERION_PENDING', 'COMMERCIAL_CALIBRATION_INPUT_MISMATCH', 'COMMERCIAL_POLICY_DRIFT',
     'COMMERCIAL_POLICY_EVIDENCE_PENDING', 'COMMERCIAL_POLICY_REQUIRED', 'CONFIG_DRIFT',
     'CURRENT_SENT_RESERVED_SETTLEMENT_REQUIRED', 'DEEPSEEK_HIGHEST_PRICE_BINDING', 'DIAGNOSTIC_GATE_CLOSED',
     'DIRECT_TRANSPORT_BYPASS', 'DUPLICATE_OBSERVATION', 'DUPLICATE_OR_INVALID_STAGE_GRANT',
     'ELAPSED_DEADLINE_EXCEEDED', 'EVIDENCE_SOURCE_PATH_FORBIDDEN', 'EXACTLY_ONE_DURABLE_OBSERVATION_REQUIRED',
     'EXACT_CALIBRATION_POLICY_COVERAGE', 'EXACT_COMMERCIAL_POLICY_REQUIRED', 'EXACT_CURRENT_PLAN_INVENTORY_REQUIRED',
     'EXECUTION_SCHEDULE_DRIFT', 'EXPLICIT_FROZEN_INPUT_PATH_REQUIRED', 'FINAL_DEEPSEEK_NATIVE_REFERENCE_REQUIRED',
     'FINAL_SESSION_FREEZE_REQUIRED', 'FIXTURE_NOT_REAL_TRANSPORT', 'FORMAL_BOUND_KIND', 'FORMAL_RESEARCH_DESIGN_DRIFT',
     'FORMAL_TWO_PROVIDER_GRANTS_REQUIRED', 'FROZEN_CADENCE_MISMATCH', 'FROZEN_DISK_CONFIG_OR_PLAN_DRIFT',
     'FROZEN_INPUT_FILES_DRIFT', 'FROZEN_NETWORK_CREDENTIAL_GATES_CLOSED', 'FULL_PLAN_COMMERCIAL_BOUND_COVERAGE',
     'GLOBAL_CURSOR_MISMATCH', 'HTTP_ERROR_UNKNOWN_CHARGE', 'IMMUTABLE_WORKFLOW_BINDING_DRIFT',
     'INCOMPLETE_STAGE_RECEIPTS', 'INVALID_STAGE_ACCEPT', 'INVALID_USAGE', 'LEGACY_PROVIDER_PHASE_CURSOR_BYPASS',
     'MALFORMED_OR_INTERRUPTED_RESPONSE', 'MISSING_FINISH_REASON', 'MISSING_USAGE', 'MODEL_DRIFT',
     'NATIVE_EVIDENCE_SOURCE_DRIFT', 'NATIVE_EVIDENCE_SOURCE_MANIFEST_REQUIRED', 'NETWORK_DISABLED',
     'NO_MATCHING_BOOTSTRAP_RUNTIME_REVIEW', 'NO_MATCHING_DURABLE_RESERVE', 'NO_MATCHING_PAID_RUNTIME_REVIEW',
     'NO_PROVIDER_EPHEMERAL_CREDENTIAL', 'OBSERVATION_ACTIVE_BINDING', 'OBSERVATION_BEFORE_STAGE_GRANT',
     'OBSERVATION_BINDING', 'OBSERVATION_BINDING_OR_ORDER', 'OBSERVATION_WITHOUT_SENT_RESERVE',
     'ONLY_DECLARED_TECHNICAL_STAGES', 'OPENAI_SHORT_LONG_PRICE_BINDING', 'ORIGINAL_ELEVEN_WHITELIST_REQUIRED',
     'OUTPUT_BOUND', 'PAUSED_PROVIDER', 'PAYLOAD_MODEL_DRIFT', 'PERMIT_NOT_ISSUED', 'PERMIT_REQUIRED',
     'PERSISTED_WORKFLOW_BINDING_DRIFT', 'PHYSICAL_EXPOSURE_OR_RESERVE_DRIFT', 'PIPELINE_RUNTIME_GATE_MISMATCH',
     'PREMATURE_TRANSPORT_PERMIT', 'PRICE_CALENDAR_PENDING', 'PRICE_CALENDAR_UNKNOWN', 'PRICE_CLOCK_MISMATCH',
     'PRICE_WINDOW_CROSSING', 'PRICE_WINDOW_UNKNOWN', 'PROVIDER_COMMERCIAL_EVIDENCE_PENDING',
     'RECEIPT_BEFORE_STAGE_GRANT', 'RECEIPT_BOUND_BREACH', 'RECEIPT_CASH_MISMATCH', 'RECEIPT_CASH_TOKEN_MISMATCH',
     'RECEIPT_CLOCK', 'RECEIPT_ID', 'RECEIPT_MODEL', 'RECEIPT_OBSERVATION_MISMATCH', 'RECEIPT_OUTCOME',
     'RECEIPT_PRICE_WINDOW', 'RECEIPT_SCHEMA_OR_VALUE', 'RECEIPT_TIER', 'RECEIPT_TRUNCATION_FLAG',
     'RECEIPT_WITHOUT_MATCHING_SETTLED_CASH', 'RECEIPT_WITHOUT_PRIOR_OBSERVATION', 'REQUEST_PARAMETERS_DRIFT',
     'RESERVE_NOT_COMMITTED', 'RESPONSE_SIZE_INVALID', 'RUNTIME_CASH_CURSOR_REQUIRED', 'RUNTIME_CURSOR_OR_STOP',
     'RUNTIME_DISPATCH_BINDING_REQUIRED', 'RUNTIME_ROOT_OR_CASH_PATH_DRIFT', 'SCIENTIFIC_CALIBRATION_MISMATCH',
     'SCOPED_BOOTSTRAP_REQUIRED', 'SETTLEMENT_OBSERVATION_MISMATCH', 'STAGE_ACCEPT_ALREADY_RECORDED',
     'STAGE_ALREADY_ADMITTED', 'STAGE_GRANT_ACCEPTANCE_REQUIRED', 'STAGE_GRANT_DRIFT',
     'STAGE_GRANT_OBSERVATION_BINDING', 'STAGE_GRANT_PAYLOAD_BINDING', 'STAGE_GRANT_PHASE_BUDGET',
     'STAGE_GRANT_REMAINING_BUDGET', 'STAGE_GRANT_WITHOUT_BOUND_ACCEPTANCE', 'STAGE_NOT_ADMITTED',
     'STATIC_STAGE_NOT_AUTHORIZED', 'STOPPED_OR_ACTIVE_WORKFLOW', 'STOPPED_OR_COMPLETE_RUNTIME', 'STOPPED_WORKFLOW',
     'STUDY_MODEL_DRIFT', 'TECHNICAL_STAGE_SET_DRIFT', 'TIER_DRIFT', 'TRANSPORT_ALREADY_CONSUMED_OR_UNAVAILABLE',
     'TRANSPORT_CONSUMPTION_STATE', 'TRANSPORT_CURSOR_OR_STOP', 'TRANSPORT_RUNTIME_PERMIT_MISMATCH',
     'TRANSPORT_TIMEOUT_OR_NETWORK_ERROR', 'UNAPPROVED_POLICY_OR_REVIEW_PATH', 'UNDECLARED_STAGE_ACCEPT',
     'UNDECLARED_STAGE_GRANT', 'UNFROZEN_ENDPOINT', 'UNKNOWN_WORKFLOW_EVENT', 'UNREGISTERED_WORKFLOW',
     'UNSUPPORTED_API_OUTCOME', 'WALL_CLOCK_INVALID', 'WALL_CLOCK_MONOTONIC_MISMATCH', 'WORKFLOW_ALREADY_COMPLETE',
     'WORKFLOW_AUTHORITY_MISSING_OR_DRIFTED', 'WORKFLOW_BINDING', 'WORKFLOW_CORRUPT', 'WORKFLOW_HASH_CHAIN',
     'WORKFLOW_RECEIPT_ORDER', 'WORKFLOW_REGISTRATION_DRIFT', 'WORKFLOW_SCHEMA_MISSING'))

SAFE_ERROR_CODES = SAFE_ERROR_CODES | {'SETTLEMENT_PARSE_FAILED', 'SETTLEMENT_STORAGE_FAILED', 'BOUND_BREACH_ACCOUNTED'}
# Future-only diagnostics: literals/types/numeric status/local clocks; no raw exception text.
DIAGNOSTIC_STAGES = frozenset(('PRE_TRANSPORT_CHECK', 'SSL_CONTEXT_SETUP', 'HTTP_OPENER_SETUP',
                               'REQUEST_BUILD', 'PHYSICAL_GATE', 'OPENER_OPEN', 'RESPONSE_BODY_READ', 'RESPONSE_CHECK',
                               'JSON_DECODE', 'OBSERVATION_ARCHIVE', 'NORMALIZE', 'SETTLEMENT', 'RECEIPT_PREPARE',
                               'RECEIPT_WRITE'))
DIAGNOSTIC_TYPES = frozenset(('ADAPTER_FAILURE', 'CORE_GATE_ERROR', 'HTTP_ERROR', 'URL_ERROR',
                              'TLS_CERTIFICATE_ERROR', 'TLS_ERROR', 'TIMEOUT', 'CONNECTION_ERROR', 'OS_ERROR',
                              'JSON_DECODE_ERROR', 'UNICODE_ERROR', 'VALUE_ERROR', 'TYPE_ERROR', 'SQLITE_ERROR',
                              'INTERRUPTED', 'OTHER', 'NONE'))
DIAGNOSTIC_KEYS = frozenset(('schema_version', 'stage', 'error_type', 'safe_error_code',
                             'http_status', 'url_reason_type', 'physical_open_reached', 'request_started_utc',
                             'failure_observed_utc', 'elapsed_monotonic_seconds'))


def _diagnostic_type(exc):
    for typ, label in ((AdapterFailure, 'ADAPTER_FAILURE'), (core.GateError, 'CORE_GATE_ERROR'),
                       (urllib.error.HTTPError, 'HTTP_ERROR'), (urllib.error.URLError, 'URL_ERROR'),
                       (ssl.SSLCertVerificationError, 'TLS_CERTIFICATE_ERROR'), (ssl.SSLError, 'TLS_ERROR'),
                       (TimeoutError, 'TIMEOUT'), (ConnectionError, 'CONNECTION_ERROR'),
                       (sqlite3.Error, 'SQLITE_ERROR'), (OSError, 'OS_ERROR'),
                       (json.JSONDecodeError, 'JSON_DECODE_ERROR'), (UnicodeError, 'UNICODE_ERROR'),
                       (ValueError, 'VALUE_ERROR'), (TypeError, 'TYPE_ERROR'),
                       ((KeyboardInterrupt, SystemExit), 'INTERRUPTED')):
        if isinstance(exc, typ): return label
    return 'NONE' if exc is None else 'OTHER'


def _utc_text(value):
    if type(value) is not datetime or value.utcoffset() != timedelta(0): return None
    return value.isoformat()


def validate_diagnostic(value):
    if type(value) is not dict or any(type(k) is not str for k in value) or set(
        value) != DIAGNOSTIC_KEYS: raise core.GateError('INVALID_SAFE_DIAGNOSTIC')
    if any(type(value[k]) is not str for k in
           ('stage', 'error_type', 'safe_error_code', 'url_reason_type')): raise core.GateError(
        'INVALID_SAFE_DIAGNOSTIC')
    if type(value['schema_version']) is not int or value['schema_version'] != 1: raise core.GateError(
        'INVALID_SAFE_DIAGNOSTIC')
    if value['stage'] not in DIAGNOSTIC_STAGES or value['error_type'] not in DIAGNOSTIC_TYPES or value[
        'url_reason_type'] not in DIAGNOSTIC_TYPES: raise core.GateError('INVALID_SAFE_DIAGNOSTIC')
    if value['safe_error_code'] not in SAFE_ERROR_CODES | {'UNCLASSIFIED'}: raise core.GateError(
        'INVALID_SAFE_DIAGNOSTIC')
    status = value['http_status']
    if status is not None and (type(status) is not int or not 100 <= status <= 599): raise core.GateError(
        'INVALID_SAFE_DIAGNOSTIC')
    if type(value['physical_open_reached']) is not bool: raise core.GateError('INVALID_SAFE_DIAGNOSTIC')
    for name in ('request_started_utc', 'failure_observed_utc'):
        text = value[name]
        if text is not None:
            if type(text) is not str or len(text) > 40: raise core.GateError('INVALID_SAFE_DIAGNOSTIC')
            try:
                parsed = datetime.fromisoformat(text)
            except Exception:
                raise core.GateError('INVALID_SAFE_DIAGNOSTIC') from None
            if _utc_text(parsed) != text: raise core.GateError('INVALID_SAFE_DIAGNOSTIC')
    elapsed = value['elapsed_monotonic_seconds']
    if elapsed is not None and (
            type(elapsed) not in (int, float) or not math.isfinite(elapsed) or elapsed < 0): raise core.GateError(
        'INVALID_SAFE_DIAGNOSTIC')
    return copy.deepcopy(value)


def diagnostic_cash_states(diagnostic):
    if diagnostic['stage'] in ('RECEIPT_PREPARE', 'RECEIPT_WRITE'): return ('SETTLED',)
    if diagnostic['stage'] == 'SETTLEMENT': return ('RESERVED', 'UNKNOWN', 'SETTLED')
    return ('UNKNOWN',)


def failure_diagnostic(exc, stage='PRE_TRANSPORT_CHECK', start=None, start_mono=None, physical_open_reached=False):
    code = None;
    status = None;
    reason_type = 'NONE';
    elapsed = None
    try:
        if isinstance(exc, AdapterFailure):
            code = exc.code
        elif isinstance(exc, core.GateError) and len(exc.args) == 1:
            code = exc.args[0]
        if isinstance(exc, urllib.error.HTTPError):
            status = exc.code if type(exc.code) is int and 100 <= exc.code <= 599 else None
        elif isinstance(exc, urllib.error.URLError):
            reason_type = _diagnostic_type(exc.reason)
    except BaseException:
        code = None;status = None;reason_type = 'OTHER'
    try:
        if type(start_mono) in (int, float):
            delta = time.monotonic() - start_mono
            if math.isfinite(delta) and delta >= 0: elapsed = delta
    except BaseException:
        pass
    return validate_diagnostic(
        {'schema_version': 1, 'stage': stage if stage in DIAGNOSTIC_STAGES else 'PRE_TRANSPORT_CHECK',
         'error_type': _diagnostic_type(exc),
         'safe_error_code': code if type(code) is str and code in SAFE_ERROR_CODES else 'UNCLASSIFIED',
         'http_status': status, 'url_reason_type': reason_type, 'physical_open_reached': physical_open_reached is True,
         'request_started_utc': _utc_text(start), 'failure_observed_utc': _utc_text(datetime.now(timezone.utc)),
         'elapsed_monotonic_seconds': elapsed})


def _diagnosed_failure(code, exc, stage, start, start_mono, physical_open_reached):
    result = AdapterFailure(code)
    result.diagnostic = failure_diagnostic(exc, stage, start, start_mono, physical_open_reached)
    return result


class HTTPAdapter:
    def __init__(self, network_enabled=False, credential=None, offline_exchange=None, fixture_clock=None,
                 fixture_monotonic=None, runtime_gate=None):
        self.runtime_gate = runtime_gate;
        self.network_enabled = network_enabled;
        self.credential = credential;
        self.offline_exchange = offline_exchange;
        self.fixture_clock = fixture_clock;
        self.fixture_monotonic = fixture_monotonic

    def readiness(self, config, provider, phase, ledger=None, rid=None, point='reserve', permit=None):
        if phase == 'count': raise core.GateError('AUTHENTICATED_COUNT_DISABLED')
        if self.network_enabled:
            from runtime_gate import RuntimeGate
            if type(self.runtime_gate) is not RuntimeGate or ledger is None or rid is None: raise core.GateError(
                'BOUND_RUNTIME_GATE_REQUIRED')
            if self.offline_exchange is not None or self.fixture_clock is not None or self.fixture_monotonic is not None: raise core.GateError(
                'FIXTURE_NOT_REAL_TRANSPORT')
            self.runtime_gate.check_dispatch(rid, ledger, self, point, permit)
            if not isinstance(self.credential, Credential) or self.credential.provider != provider or not isinstance(
                self.credential._value, str) or not self.credential._value: raise core.GateError(
                'NO_PROVIDER_EPHEMERAL_CREDENTIAL')
        elif config.get('runtime_mode') != 'OFFLINE_FIXTURE' or self.offline_exchange is None:
            raise core.GateError('NETWORK_DISABLED')
        if config['providers'][provider].get('http_url') != URLS[provider]: raise core.GateError('UNFROZEN_ENDPOINT')

    def exchange(self, body, permit):
        if type(permit) is not Permit: raise core.GateError('PERMIT_REQUIRED')
        row = permit.check(body);
        c = permit.ledger.config;
        provider = row['provider'];
        self.readiness(c, provider, row['phase'], permit.ledger, permit.rid, 'transport', permit)
        clock = self.fixture_clock or (lambda: datetime.now(timezone.utc));
        monotonic = self.fixture_monotonic or time.monotonic
        start = clock();
        start_mono = monotonic()
        encoded = core.canonical(body).encode('utf-8')
        permit.ledger.consume_transport(permit.rid, core.sha(body))
        stage = 'PRE_TRANSPORT_CHECK';
        physical_open_reached = False
        try:
            if not self.network_enabled:
                # Test double gets no actual credential, authorization header, socket or opener.
                status, data = self.offline_exchange(URLS[provider], encoded, permit)
            else:
                stage = 'SSL_CONTEXT_SETUP';
                context = ssl.create_default_context()
                stage = 'HTTP_OPENER_SETUP'
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(),
                                                     urllib.request.HTTPSHandler(context=context))
                stage = 'REQUEST_BUILD'
                request = urllib.request.Request(URLS[provider], data=encoded,
                                                 headers={'Content-Type': 'application/json',
                                                          'Authorization': 'Bearer ' + self.credential._value},
                                                 method='POST')
                stage = 'PHYSICAL_GATE';
                self.readiness(c, provider, row['phase'], permit.ledger, permit.rid, 'physical', permit)
                stage = 'OPENER_OPEN';
                physical_open_reached = True
                with opener.open(request, timeout=c['cadence']['timeout_seconds']) as response:
                    stage = 'RESPONSE_BODY_READ';
                    status = response.status;
                    data = response.read(2_000_001)
            stage = 'RESPONSE_CHECK';
            end = clock();
            elapsed = monotonic() - start_mono
            if not math.isfinite(elapsed) or elapsed < 0 or elapsed > c['cadence'][
                'timeout_seconds']: raise AdapterFailure('ELAPSED_DEADLINE_EXCEEDED')
            if start.utcoffset() is None or end.utcoffset() is None: raise AdapterFailure('WALL_CLOCK_INVALID')
            wall_elapsed = (end - start).total_seconds()
            # Conservative one-second clock-consistency guard, not a hard cancellation guarantee.
            if wall_elapsed < 0 or abs(wall_elapsed - elapsed) > 1: raise AdapterFailure(
                'WALL_CLOCK_MONOTONIC_MISMATCH')
            if type(status) is not int or not 200 <= status < 300: raise AdapterFailure('HTTP_ERROR_UNKNOWN_CHARGE')
            if not isinstance(data, bytes) or len(data) > 2_000_000: raise AdapterFailure('RESPONSE_SIZE_INVALID')
            stage = 'JSON_DECODE';
            raw = json.loads(data.decode('utf-8'))
            if self.runtime_gate is not None:
                stage = 'OBSERVATION_ARCHIVE';
                self.runtime_gate.archive_observation(permit.rid, observation_fields(raw, provider, start, end, elapsed,
                                                                                     self.credential))
            stage = 'NORMALIZE';
            normalized = normalize_openai(raw, body['model']) if provider == 'openai' else normalize_deepseek(raw, body[
                'model'], c, start, end)
            normalized['request_started_utc'] = start.isoformat();
            normalized['response_received_utc'] = end.isoformat();
            normalized['elapsed_monotonic_seconds'] = elapsed
            return normalized
        except AdapterFailure as exc:
            exc.diagnostic = failure_diagnostic(exc, stage, start, start_mono, physical_open_reached)
            raise
        except urllib.error.HTTPError as exc:
            raise _diagnosed_failure('HTTP_ERROR_UNKNOWN_CHARGE', exc, stage, start, start_mono,
                                     physical_open_reached) from None
        except (TimeoutError, urllib.error.URLError) as exc:
            raise _diagnosed_failure('TRANSPORT_TIMEOUT_OR_NETWORK_ERROR', exc, stage, start, start_mono,
                                     physical_open_reached) from None
        except BaseException as exc:
            raise _diagnosed_failure('MALFORMED_OR_INTERRUPTED_RESPONSE', exc, stage, start, start_mono,
                                     physical_open_reached) from None


class DispatchPipeline:
    def __init__(self, ledger, adapter, runtime_gate=None):
        self.ledger = ledger; self.adapter = adapter; self.runtime_gate = runtime_gate

    def send(self, rid):
        row = self.ledger.plan[rid]
        if self.adapter.network_enabled and self.runtime_gate is not self.adapter.runtime_gate: raise core.GateError(
            'PIPELINE_RUNTIME_GATE_MISMATCH')
        self.adapter.readiness(self.ledger.config, row['provider'], row['phase'], self.ledger, rid)
        self.ledger.reserve(rid)  # FULL-synchronous durable event/hash before any HTTP call.
        permit = Permit(_SEAL, self.ledger, rid, self.runtime_gate)
        try:
            normalized = self.adapter.exchange(row['body'], permit)
        except BaseException as exc:
            self.ledger.unknown(rid, 'ADAPTER_FAILURE_UNKNOWN_CHARGE')  # Preserve full reserve first.
            try:
                diagnostic = validate_diagnostic(exc.diagnostic) if isinstance(exc, AdapterFailure) and hasattr(exc,
                                                                                                                'diagnostic') else failure_diagnostic(
                    exc)
                if self.runtime_gate is not None: self.runtime_gate.record_adapter_failure(rid, diagnostic)
            except BaseException:
                pass  # A failed diagnostic never releases UNKNOWN or authorizes another call.
            raise AdapterFailure('ATTEMPT_FAILED_RESERVE_RETAINED') from None
        try:
            self.ledger.settle(rid, normalized)
        except BaseException as exc:
            try:
                diagnostic = failure_diagnostic(exc, 'SETTLEMENT', physical_open_reached=self.adapter.network_enabled)
                if self.runtime_gate is not None: self.runtime_gate.record_adapter_failure(rid, diagnostic)
            except BaseException:
                pass
            raise  # Preserve the original settlement exception and durable cash state.
        return normalized

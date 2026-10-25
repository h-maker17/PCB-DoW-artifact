"""Explicit bootstrap contracts and append-only commercial stage capabilities.
No observed count/model is asserted before the first calibration response.
"""
import copy, hashlib, json
from decimal import Decimal
from pathlib import Path
import protocol_core as core

CADENCE = {'concurrency': 1, 'min_seconds_after_response': 10, 'arm_gap_seconds': 30, 'block_gap_seconds': 60,
           'timeout_seconds': 120, 'retries': 0}
CONTEXT = {'openai': 1050000, 'deepseek': 1048576}
RATES = {'openai': {'ordinary': 20, 'read': 2, 'write': 25, 'output': 75},
         'deepseek': {'hit': .3, 'miss': 9, 'output': 27}}
RESERVE = {'openai': Decimal('26.2548'), 'deepseek': Decimal('9.438912')}
SHORT = {'ordinary': 10, 'read': 1, 'write': 12.5, 'output': 50}


def enabled(config): return config.get('bootstrap_scope', {}).get('kind') == 'BOUNDED_CALIBRATION_THREE_LAYER_V1'


def fail(reason): raise core.GateError(reason)


def read(relative, folder):
    p = (core.ROOT / relative).resolve();
    base = (core.ROOT / folder).resolve()
    if not p.is_relative_to(base) or p.suffix != '.json' or any(
        x in str(p.relative_to(core.ROOT)).lower() for x in ('api_key', 'apikey', 'secret', 'credential')): fail(
        'BOOTSTRAP_EVIDENCE_PATH')
    raw = p.read_bytes();
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def sources(config):
    declared = config['bootstrap_scope'].get('source_hashes')
    if not isinstance(declared, dict) or not declared: fail('BOOTSTRAP_PRIORS_REQUIRED')
    required = {'analysis/bounded_bootstrap_priors_20261013/sources/' + name + '.json' for name in
                ('deepseek_hosted_context', 'deepseek_request_contract', 'deepseek_highest_token_price',
                 'openai_hosted_context_and_highest_standard_price')} | {
                   'config/USER_BOOTSTRAP_BUDGET_AND_DEEPSEEK_FEE_DECLARATION_20261013.json',
                   'config/USER_BILLING_DECLARATION_20261012.json'}
    if not required.issubset(declared): fail('BOOTSTRAP_PRIOR_SOURCE_COVERAGE')
    for rel, digest in declared.items():
        folder = 'analysis/bounded_bootstrap_priors_20261013/sources' if rel.startswith('analysis/') else 'config'
        _, actual = read(rel, folder)
        if actual != digest: fail('BOOTSTRAP_PRIOR_SOURCE_DRIFT')
    return dict(declared)


def item_contract(config, row):
    p = config['providers'][row['provider']];
    body = row['body']
    if core.sha(body) != row['payload_hash'] or body.get('model') != p['candidate_model']: fail('PAYLOAD_MODEL_DRIFT')
    field = 'input' if row['provider'] == 'openai' else 'messages'
    actual = copy.deepcopy(body);
    expected = copy.deepcopy(p['request_template']);
    actual.pop(field, None);
    expected.pop(field, None)
    if actual != expected: fail('REQUEST_PARAMETERS_DRIFT')
    out = 'max_output_tokens' if row['provider'] == 'openai' else 'max_tokens'
    if body.get(out) != 64 or p.get(out) != 64: fail('OUTPUT_BOUND')


def validate(config, plan, source_hashes, urls):
    scope = config['bootstrap_scope'];
    fixture = config.get('runtime_mode') == 'OFFLINE_FIXTURE'
    required = ('frozen', 'paid_enabled', 'independent_review_approved', 'credentials_configured', 'network_enabled',
                'credential_loading_enabled')
    if config.get('runtime_mode') not in ('PAID_USER_LAUNCH', 'OFFLINE_FIXTURE') or not all(
        config.get(k) is True for k in required): fail('FROZEN_NETWORK_CREDENTIAL_GATES_CLOSED')
    if not fixture and (config.get('session_id') == 'DRAFT-NOT-FOR-PAID-REQUESTS' or scope.get(
        'final_session_and_payloads_frozen') is not True): fail('FINAL_SESSION_FREEZE_REQUIRED')
    if config['cadence'] != CADENCE or core.sha(plan) != scope.get('research_plan_hash'): fail(
        'BOOTSTRAP_PLAN_OR_CADENCE_DRIFT')
    advance = config.get('formal_continuation_advance_authorization', {})
    if advance.get('ordinary_post_pilot_permission_preapproved') is not True or advance.get(
        'all_technical_scientific_budget_and_authentication_gates_still_required') is not True: fail(
        'ADVANCE_CONTINUATION_NOT_AUTHORIZED')
    policy, phash = read(scope.get('policy_path', ''), 'config/runtime_policy')
    if phash != scope.get('policy_sha256') or policy.get('kind') != 'BOUNDED_BOOTSTRAP_CONTRACT_POLICY' or policy.get(
        'fixture') is not fixture or policy.get('require_attack_positive') is not False: fail(
        'BOOTSTRAP_CONTRACT_POLICY_REQUIRED')
    if policy.get('session_id') != config['session_id'] or policy.get('plan_hash') != core.sha(plan) or policy.get(
        'source_hashes') != sources(config): fail('BOOTSTRAP_PRIOR_BINDING')
    cal = [r for r in plan if r['phase'] == 'calibration'];
    whitelist = [{k: r[k] for k in ('request_id', 'provider', 'phase', 'payload_hash')} for r in cal]
    if len(cal) != 11 or policy.get('whitelist') != whitelist: fail('ORIGINAL_ELEVEN_WHITELIST_REQUIRED')
    for row in cal: item_contract(config, row)
    for name, p in config['providers'].items():
        if p.get('http_url') != urls[name] or p.get('currency') != ('USD' if name == 'openai' else 'CNY') or p.get(
            'account_currency_verified') is not True or p.get('output_limit_verified') is not True or core.money(
            p.get('fee_surcharge_multiplier')) != 1 or p.get('hard_cap') != 30 or p.get('dispatch_stop') != 27: fail(
            'BOOTSTRAP_PROVIDER_CONTRACT')
        if p['phases']['calibration']['cash_subcap'] != 27 or p['phases']['calibration']['max_attempts'] != (
        5 if name == 'openai' else 6): fail('BOOTSTRAP_PHASE_CAP')
        contract = policy.get('contracts', {}).get(name, {})
        if contract != {'bound_kind': 'published_api_context_upper', 'input_upper': CONTEXT[name], 'output_upper': 64,
                        'highest_rates': RATES[name], 'reserve': str(RESERVE[name]), 'surcharge_upper': 1}: fail(
            'BOOTSTRAP_CONTEXT_PRICE_CONTRACT')
        if name == 'openai' and (
                p.get('price_per_million') != SHORT or p.get('price_per_million_long') != RATES[name]): fail(
            'OPENAI_SHORT_LONG_PRICE_BINDING')
        if name == 'deepseek' and (
                p.get('price_per_million_peak') != RATES[name] or p.get('price_per_million_offpeak') != {'hit': .15,
                                                                                                         'miss': 4.5,
                                                                                                         'output': 13.5}): fail(
            'DEEPSEEK_HIGHEST_PRICE_BINDING')
    # Authorization is an exact saved human declaration, not an invoice or an API permission.
    authorization, ahash = read(scope.get('budget_authorization_path', ''), 'config')
    if ahash != scope.get('budget_authorization_sha256') or authorization.get(
        'bootstrap_substantive_budget_revision_approved') is not True or authorization.get(
        'deepseek_user_declares_no_tax_or_other_additional_fee') is not True or authorization.get(
        'deepseek_surcharge_factor_upper') != '1': fail('BOOTSTRAP_BUDGET_AUTHORIZATION_REQUIRED')
    record, _ = read(scope.get('review_path', ''), 'review')
    expected = {'kind': 'BOOTSTRAP_INSTRUMENT_ACCEPTANCE', 'approved': True, 'fixture': fixture,
                'config_hash': core.sha(config), 'plan_hash': core.sha(plan), 'policy_sha256': phash,
                'source_hashes': source_hashes, 'prior_source_hashes': sources(config)}
    if any(record.get(k) != v for k, v in expected.items()): fail('NO_MATCHING_BOOTSTRAP_RUNTIME_REVIEW')
    probe = object.__new__(core.Ledger);
    probe.config = config;
    probe.plan = {r['request_id']: r for r in plan};
    probe._check_inventory()
    return policy


def bound(gate, row):
    item_contract(gate.ledger.config, row)
    if row['phase'] == 'calibration':
        entry = {k: row[k] for k in ('request_id', 'provider', 'phase', 'payload_hash')}
        if entry not in gate.policy['whitelist']: fail('BOOTSTRAP_WHITELIST_DENIED')
        return {'kind': 'published_api_context_upper', 'input_upper': CONTEXT[row['provider']], 'output_upper': 64}
    grants = gate.scope_grants();
    stage = (row['provider'], row['phase'])
    if stage not in grants: fail('STAGE_NOT_ADMITTED')
    if row['phase'] == 'formal' and not all((p, 'formal') in grants for p in ('deepseek', 'openai')): fail(
        'FORMAL_TWO_PROVIDER_GRANTS_REQUIRED')
    return copy.deepcopy(grants[stage]['bounds'][row['request_id']])


def validate_grant(gate, relative, expected_hash=None, receipts=None):
    record, ghash = read(relative, 'review/stage_grants')
    if expected_hash is not None and ghash != expected_hash: fail('STAGE_GRANT_DRIFT')
    binding = {k: gate.binding[k] for k in
               ('config', 'plan', 'session_id', 'authority_id', 'source_hashes', 'schedule_hash')}
    fixture = gate.policy['fixture']
    if record.get('kind') != 'COMMERCIAL_STAGE_GRANT' or record.get('approved') is not True or record.get(
        'fixture') is not fixture or record.get('binding') != binding: fail('STAGE_GRANT_ACCEPTANCE_REQUIRED')
    pair = (record.get('provider'), record.get('phase'))
    if pair not in gate.diag_stages + tuple((p, 'formal') for p in ('deepseek', 'openai')): fail(
        'UNDECLARED_STAGE_GRANT')
    ids = gate.stage_ids(*pair)
    if record.get('request_payloads') != {rid: gate.ledger.plan[rid]['payload_hash'] for rid in ids}: fail(
        'STAGE_GRANT_PAYLOAD_BINDING')
    policy, phash = read(record.get('commercial_policy_path', ''), 'config/runtime_policy')
    proof, ehash = read(policy.get('commercial_evidence_path', ''), 'config/runtime_policy_evidence')
    expected = policy.get('calibration_expected_input_tokens_by_id', {})
    c = gate.ledger.config;
    rows = list(gate.ledger.plan.values());
    all_payloads = {r['request_id']: r['payload_hash'] for r in rows}
    cal = {r['request_id'] for r in rows if r['phase'] == 'calibration'}
    if record.get('commercial_policy_sha256') != phash or record.get(
        'commercial_evidence_sha256') != ehash or policy.get(
        'kind') != 'COMMERCIAL_RUNTIME_TECHNICAL_POLICY' or policy.get('fixture') is not fixture or policy.get(
        'require_attack_positive') is not False or policy.get(
        'calibration_criterion') != 'exact_commercial_rendered_input_tokens' or set(expected) != cal or any(
        type(v) is not int or v <= 0 for v in expected.values()): fail('EXACT_COMMERCIAL_POLICY_REQUIRED')
    if policy.get('commercial_evidence_sha256') != ehash or proof.get(
        'kind') != 'COMMERCIAL_RENDERED_INPUT_AND_USAGE_DEFINITION' or proof.get('fixture') is not fixture or proof.get(
        'session_id') != c['session_id'] or proof.get('request_payloads') != all_payloads or proof.get(
        'calibration_expected_input_tokens_by_id') != expected or proof.get('evidence_source_hashes') != record.get(
        'evidence_source_hashes'): fail('COMMERCIAL_POLICY_EVIDENCE_PENDING')
    # The source set is independently reviewed and pinned, including the final DS native reference.
    source_config = copy.deepcopy(c);
    source_config['runtime_evidence_source_hashes'] = record.get('evidence_source_hashes')
    import runtime_gate
    runtime_gate.evidence_source_hashes_legacy(source_config)
    if record.get('provider_evidence') != {'account_currency_verified': True, 'output_limit_verified': True,
                                           'rendered_input_bound_verified': True, 'input_bound_evidence_verified': True,
                                           'returned_model_verified': True}: fail(
        'PROVIDER_COMMERCIAL_EVIDENCE_PENDING')
    if record.get('all_plan_bounds') is None or set(record['all_plan_bounds']) != set(all_payloads): fail(
        'FULL_PLAN_COMMERCIAL_BOUND_COVERAGE')
    for row in rows:
        b = record['all_plan_bounds'][row['request_id']]
        if b.get('kind') != 'commercial_rendered_payload_upper': fail('FORMAL_BOUND_KIND')
        clone = copy.deepcopy(row);
        clone['bound'] = b;
        gate.ledger._validate_commercial_item(clone)
        if row['phase'] == 'calibration' and expected[row['request_id']] > b['input_upper']: fail(
            'CALIBRATION_EXPECTATION_OUTSIDE_PROVEN_BOUND')
    if receipts is not None:
        for rid in cal:
            if rid not in receipts: fail('CALIBRATION_OBSERVATIONS_INCOMPLETE')
            r = receipts[rid];
            measured = core.usage_cash(c, r['provider'], r['response']['usage'], r['response'].get('price_regime'))
            if measured['input_tokens'] != expected[rid]: fail('COMMERCIAL_CALIBRATION_INPUT_MISMATCH')
        if record.get('calibration_receipt_digest') != core.sha(
            [receipts[rid] for rid in gate.schedule if rid in cal]): fail('STAGE_GRANT_OBSERVATION_BINDING')
    result = copy.deepcopy(record);
    result['bounds'] = {rid: record['all_plan_bounds'][rid] for rid in ids};
    result['path'] = relative;
    result['sha256'] = ghash
    return pair, result

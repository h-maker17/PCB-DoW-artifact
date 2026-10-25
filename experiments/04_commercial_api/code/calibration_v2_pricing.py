"""Prospective calibration-only usage valuation; never an invoice claim."""
from datetime import datetime, timezone, timedelta
from decimal import Decimal as D
import math


class EvidenceError(ValueError): pass


def count(value):
    if type(value) is not int or value < 0: raise EvidenceError('USAGE_MISSING_OR_INVALID')
    return value


def stamp(value):
    try:
        result = datetime.fromisoformat(value)
    except (ValueError, TypeError):
        raise EvidenceError('TIME_FIELDS_INVALID') from None
    if result.utcoffset() != timedelta(0): raise EvidenceError('TIME_FIELDS_INVALID')
    return result


def classify_window(contract, lower, upper):
    if lower.utcoffset() != timedelta(0) or upper.utcoffset() != timedelta(0) or upper < lower: raise EvidenceError(
        'LOCAL_TIME_INVALID')
    dates = contract['deepseek']['covered_dates']
    local = timezone(timedelta(hours=8));
    points = {lower, upper}
    if lower.astimezone(local).date().isoformat() not in dates or upper.astimezone(
        local).date().isoformat() not in dates: raise EvidenceError('CALENDAR_NOT_COVERED')
    day = lower.astimezone(local).replace(hour=0, minute=0, second=0, microsecond=0)
    while day <= upper:
        for minute in (0, 540, 720, 840, 1080, 1440):
            boundary = day + timedelta(minutes=minute)
            for t in (boundary, boundary - timedelta(microseconds=1)):
                if lower <= t <= upper: points.add(t)
        day += timedelta(days=1)
    regimes = set()
    for point in points:
        t = point.astimezone(local);
        date = t.date().isoformat()
        if date not in dates: raise EvidenceError('CALENDAR_NOT_COVERED')
        # Only dates independently checked nonholiday on both official tariffs
        # are admitted. No interpretation of an omitted holiday clause.
        if dates[date].get('nonholiday_verified') is not True: raise EvidenceError('CALENDAR_NOT_COVERED')
        minute = t.hour * 60 + t.minute
        peak = t.weekday() < 5 and (540 <= minute < 720 or 840 <= minute < 1080)
        regimes.add('peak' if peak else 'offpeak')
    if len(regimes) != 1: raise EvidenceError('PRICE_WINDOW_NOT_UNIQUE')
    return regimes.pop()


def preflight_price(contract, provider, now, timeout=120):
    if provider == 'openai': return 'STANDARD_RESERVE_CONTRACT_ONLY'
    # One-second reported-time precision sensitivity, not service clock skew.
    return classify_window(contract, now - timedelta(seconds=1), now + timedelta(seconds=timeout + 1))


def worst_reserve(contract, provider):
    p = contract[provider];
    rates = p['highest_rates']
    buckets = ('ordinary', 'read', 'write') if provider == 'openai' else ('hit', 'miss')
    return (D(p['input_context_upper']) * max(D(str(rates[k])) for k in buckets) + D(64) * D(str(rates['output']))) * D(
        p['surcharge_upper']) / D(1000000)


def validate_raw(raw, provider, expected_model, *, require_trace=True):
    if not isinstance(raw, dict) or raw.get('model') != expected_model: raise EvidenceError('MODEL_DRIFT')
    if require_trace:
        identifier = raw.get('id');
        created = raw.get('created' if provider == 'deepseek' else 'created_at')
        if type(identifier) is not str or not identifier or len(identifier) > 256 or type(
            created) is not int or created < 0: raise EvidenceError('RESPONSE_ID_OR_CREATED_MISSING')
    u = raw.get('usage')
    if not isinstance(u, dict): raise EvidenceError('USAGE_MISSING_OR_INVALID')
    if provider == 'openai':
        if raw.get('service_tier') not in ('default', 'standard'): raise EvidenceError('TIER_DRIFT')
        if raw.get('status') not in ('completed', 'incomplete'): raise EvidenceError('RESPONSE_STATUS_INVALID')
        if raw.get('status') == 'incomplete' and raw.get('incomplete_details', {}).get(
            'reason') != 'max_output_tokens': raise EvidenceError('RESPONSE_STATUS_INVALID')
        output_items = raw.get('output')
        if not isinstance(output_items, list): raise EvidenceError('OUTPUT_STRUCTURE_INVALID')
        item_types = []
        for item in output_items:
            if not isinstance(item, dict) or item.get('type') not in ('message', 'reasoning'): raise EvidenceError(
                'OUTPUT_STRUCTURE_INVALID')
            item_types.append(item['type'])
            if item['type'] == 'reasoning':
                if not isinstance(item.get('summary'), list): raise EvidenceError('OUTPUT_STRUCTURE_INVALID')
            else:
                if item.get('role') != 'assistant' or not isinstance(item.get('content'), list): raise EvidenceError(
                    'OUTPUT_STRUCTURE_INVALID')
                for part in item['content']:
                    if not isinstance(part, dict) or part.get('type') not in ('output_text',
                                                                              'refusal'): raise EvidenceError(
                        'OUTPUT_STRUCTURE_INVALID')
                    if type(part.get(
                        'text' if part['type'] == 'output_text' else 'refusal')) is not str: raise EvidenceError(
                        'OUTPUT_STRUCTURE_INVALID')
        structure = {'output_array_present': True, 'item_types': item_types,
                     'visible_text_required_for_calibration': False}
        d = u.get('input_tokens_details')
        if not isinstance(d, dict): raise EvidenceError('USAGE_MISSING_OR_INVALID')
        total = count(u.get('input_tokens'));
        output = count(u.get('output_tokens'))
        read = count(d.get('cached_tokens'));
        write = count(d.get('cache_write_tokens'))
        if read + write > total: raise EvidenceError('USAGE_CONSERVATION')
        buckets = {'ordinary': total - read - write, 'read': read, 'write': write}
        detail = u.get('output_tokens_details')
        if detail is not None and type(detail) is not dict: raise EvidenceError('USAGE_MISSING_OR_INVALID')
        reason = detail.get('reasoning_tokens') if detail is not None else None
    else:
        choices = raw.get('choices')
        if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict) or choices[0].get(
            'finish_reason') not in ('stop', 'length'): raise EvidenceError('RESPONSE_STATUS_INVALID')
        message = choices[0].get('message')
        if not isinstance(message, dict) or message.get('role') != 'assistant' or 'content' not in message or (
                message['content'] is not None and type(message['content']) is not str): raise EvidenceError(
            'OUTPUT_STRUCTURE_INVALID')
        structure = {'message_role': 'assistant', 'content_kind': 'null' if message['content'] is None else 'string',
                     'visible_text_required_for_calibration': False}
        total = count(u.get('prompt_tokens'));
        output = count(u.get('completion_tokens'))
        hit = count(u.get('prompt_cache_hit_tokens'));
        miss = count(u.get('prompt_cache_miss_tokens'))
        if hit + miss != total: raise EvidenceError('USAGE_CONSERVATION')
        buckets = {'hit': hit, 'miss': miss}
        detail = u.get('completion_tokens_details')
        if detail is not None and type(detail) is not dict: raise EvidenceError('USAGE_MISSING_OR_INVALID')
        reason = detail.get('reasoning_tokens') if detail is not None else None
    if total == 0: raise EvidenceError('EMPTY_INPUT_USAGE')
    if count(u.get('total_tokens')) != total + output: raise EvidenceError('USAGE_CONSERVATION')
    if reason is not None and count(reason) > output: raise EvidenceError('USAGE_CONSERVATION')
    return {'input_tokens': total, 'output_tokens': output, 'buckets': buckets, 'reasoning_tokens': reason,
            'output_structure': structure,
            'reasoning_tokens_missing_reason': 'NOT_SEPARATELY_ITEMIZED_NONADDITIVE_OUTPUT_CATEGORY' if reason is None else None}


def cost_at(parsed, rates, factor):
    input_cost = sum((D(v) * D(str(rates[k])) for k, v in parsed['buckets'].items()), D(0)) * D(factor) / D(1000000)
    output_cost = D(parsed['output_tokens']) * D(str(rates['output'])) * D(factor) / D(1000000)
    return {'input_cost': str(input_cost), 'output_cost': str(output_cost), 'cash': str(input_cost + output_cost)}


def value_response(raw, provider, model, contract, timing):
    parsed = validate_raw(raw, provider, model)
    start = stamp(timing['client_start_utc']);
    send = stamp(timing['physical_open_started_utc']);
    end = stamp(timing['response_received_utc'])
    elapsed = timing['elapsed_monotonic_seconds']
    flags = []
    local_valid = type(elapsed) in (int, float) and math.isfinite(
        elapsed) and 0 <= elapsed <= 120 and start <= send <= end and abs((end - start).total_seconds() - elapsed) <= 1
    if not local_valid: flags.append('LOCAL_CLOCK_OR_DURATION_ANOMALY')
    created = raw.get('created' if provider == 'deepseek' else 'created_at')
    created_dt = None
    if type(created) is int:
        try:
            created_dt = datetime.fromtimestamp(created, timezone.utc)
        except (ValueError, OverflowError, OSError):
            flags.append('CREATED_UNREPRESENTABLE')
        if created_dt is not None and not start <= created_dt <= end: flags.append(
            'CREATED_POINT_OUTSIDE_CLIENT_INTERVAL')
    else:
        flags.append('CREATED_MISSING_OR_INVALID')
    lo = hi = None;
    price_basis = None;
    regime = None;
    price_error = None
    if provider == 'openai':
        # Price classification is separate from the2500 acceptance ceiling.
        # Reviewed safe bands are strictly away from the displayed272K edge;
        # counts near that unresolved edge retain the short/long interval.
        lo = cost_at(parsed, contract[provider]['short_rates'], contract[provider]['surcharge_upper'])
        hi = cost_at(parsed, contract[provider]['highest_rates'], contract[provider]['surcharge_upper'])
        if parsed['input_tokens'] <= contract[provider]['unambiguous_short_max_input']:
            hi = lo;
            price_basis = 'PUBLIC_STANDARD_SHORT_CONTRACT_WITH_REAL_USAGE';
            regime = 'short'
        elif parsed['input_tokens'] >= contract[provider]['unambiguous_long_min_input']:
            lo = hi;
            price_basis = 'PUBLIC_STANDARD_LONG_CONTRACT_WITH_REAL_USAGE';
            regime = 'long'
        else:
            price_error = 'DISPLAYED_272K_EDGE_NOT_UNIQUELY_ASSIGNED'
    else:
        lo = cost_at(parsed, contract[provider]['offpeak_rates'], contract[provider]['surcharge_upper'])
        hi = cost_at(parsed, contract[provider]['peak_rates'], contract[provider]['surcharge_upper'])
        if local_valid and created_dt is not None:
            try:
                # New explicitly reviewed valuation convention covers common
                # integer quantization representations, not clock correction.
                regime = classify_window(contract, min(start, send, created_dt - timedelta(seconds=1)),
                                         max(end, created_dt + timedelta(seconds=1)))
                lo = hi = cost_at(parsed, contract[provider][regime + '_rates'], contract[provider]['surcharge_upper'])
                price_basis = 'PUBLIC_TARIFF_OBSERVED_TIME_ENVELOPE_CONSENSUS_ESTIMATE_NOT_INVOICE'
            except EvidenceError as exc:
                price_error = str(exc)
        else:
            price_error = 'TIME_OR_PRICE_EVIDENCE_NOT_UNIQUE'
    breaches = []
    if parsed['input_tokens'] > 2500: breaches.append('INPUT_ABOVE_2500')
    if parsed['output_tokens'] > 64: breaches.append('OUTPUT_ABOVE_64')
    if not local_valid: breaches.append('LOCAL_CLOCK_OR_DURATION_ANOMALY')
    return {'parsed': parsed, 'raw_created': created, 'time_flags': flags, 'price_regime': regime,
            'price_basis': price_basis,
            'valuation_status': 'USAGE_PRICED_ESTIMATE' if price_basis else 'PRICE_INTERVAL_UNRESOLVED',
            'price_error': price_error, 'lower': lo, 'upper': hi,
            'target_2100_2400': 2100 <= parsed['input_tokens'] <= 2400,
            'acceptance_breaches': breaches, 'technical_accepted': bool(price_basis and not breaches),
            'invoice_verified': False, 'provider_clock_synchronization_proven': False}

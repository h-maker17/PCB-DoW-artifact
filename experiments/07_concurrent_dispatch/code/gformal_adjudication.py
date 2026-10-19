"""Append-only, effect-blind resource and interruption adjudication.

Raw results, raw verdicts and raw ledger events are never rewritten. A review
covers every attempt in a paused segment, including retained/unaffected attempts.
Its ledger prefix remains verifiable after later rounds are appended.
"""
from pathlib import Path
import copy
import hashlib
import json

def _core():
    import formal_core
    return formal_core

def evidence_files(root):
    root = Path(root)
    names = ('*-pause.json', 'resource-monitor.jsonl', 'resource-final-check.json',
             'stage-failure.json', 'cleanup-cost.json', 'stage-completed.json',
             'cleanup-signals.jsonl', 'owned-service.json')
    paths = set()
    for name in names:
        paths.update(root.glob('segments/*/' + name))
    return {p.relative_to(root).as_posix(): _core().sha(_core().bounded(root, p))
            for p in sorted(paths)}

def _pauses(root):
    c = _core()
    root = Path(root)
    return {p.relative_to(root).as_posix(): c.sha(c.bounded(root, p))
            for p in sorted(root.glob('segments/*/*-pause.json'))}

def _events(ledger):
    raw = ledger.path.read_bytes() if ledger.path.exists() else b''
    if raw and not raw.endswith(b'\n'):
        raise ValueError('Partial ledger cannot be adjudicated')
    return raw, [json.loads(line) for line in raw.splitlines()]

def _check_record(ledger, record, raw, events):
    c = _core()
    if (record.get('experiment_id') != ledger.experiment or
        record.get('source_revision') != ledger.source or
        not record.get('review_reference') or record.get('effect_inspected') is not False or
        record.get('coverage_policy') != 'all_attempts_in_paused_segments'):
        raise ValueError('Resource adjudication identity/effect-blind coverage differs')
    n = record.get('ledger_prefix_bytes')
    if type(n) is not int or n <= 0 or n > len(raw) or not raw[:n].endswith(b'\n') or hashlib.sha256(raw[:n]).hexdigest() != record.get('ledger_prefix_sha256'):
        raise ValueError('Resource adjudication ledger prefix differs')
    pauses = record.get('pause_files', {})
    evidence = record.get('evidence_files', {})
    if not pauses or not set(pauses).issubset(evidence):
        raise ValueError('Resource adjudication does not bind pauses')
    for name, expected in evidence.items():
        p = c.bounded(ledger.root, ledger.root / name)
        if not p.is_file() or c.sha(p) != expected:
            raise ValueError('Resource adjudication evidence changed: ' + name)
    current = _pauses(ledger.root)
    if any(current.get(name) != value for name, value in pauses.items()):
        raise ValueError('Resource pause changed after adjudication')
    segments = {Path(name).parent.name for name in pauses}
    actual_evidence = {k: v for k, v in evidence_files(ledger.root).items() if Path(k).parent.name in segments}
    if evidence != actual_evidence:
        raise ValueError('Resource adjudication must bind all preserved segment evidence')
    started = {e['attempt_id']: e for e in events if e['event'] == 'started' and e.get('segment_id') in segments}
    ends = {e['attempt_id']: e for e in events if e['event'] == 'finished'}
    decisions = record.get('decisions', {})
    if set(decisions) != set(started) or any(k not in ends for k in started):
        raise ValueError('Every paused-segment attempt needs a terminal and explicit adjudication')
    for attempt, decision in decisions.items():
        end = ends[attempt]
        if decision.get('result_sha256') != end['result_sha256'] or decision.get('validity_sha256') != end['validity_sha256'] or not decision.get('evidence_reference'):
            raise ValueError('Resource decision is not bound to raw terminal evidence')
        reason = decision.get('reason')
        if reason not in c.TECHNICAL | c.CAPACITY | {'retain'}:
            raise ValueError('Unresolved/unsupported resource decision')
        if reason == 'external_resource_conflict' and decision.get('external_occupancy_confirmed') is not True:
            raise ValueError('Unknown attribution cannot become external conflict')
        if reason != 'retain' and end['reason'] in c.CAPACITY and reason != end['reason']:
            raise ValueError('Condition-related terminal cannot become a technical retry')
    return decisions

def state(ledger, require_resolved=True):
    c = _core()
    raw, events = _events(ledger)
    pauses = _pauses(ledger.root)
    records = {}
    covered = {}
    overrides = {}
    for p in sorted((ledger.root / 'protocol/resource-adjudications').glob('*.json')):
        c.bounded(ledger.root, p)
        record = c.read(p)
        decisions = _check_record(ledger, record, raw, events)
        digest = c.sha(p)
        records[p.relative_to(ledger.root).as_posix()] = digest
        for name, value in record['pause_files'].items():
            if name in covered:
                raise ValueError('Pause adjudicated twice; no ambiguous override')
            covered[name] = value
        for attempt, decision in decisions.items():
            if attempt in overrides:
                raise ValueError('Attempt adjudicated twice')
            overrides[attempt] = dict(decision, adjudication_sha256=digest)
    unresolved = sorted(set(pauses) - set(covered))
    if require_resolved and unresolved:
        raise ValueError('Unresolved resource/technical pause requires evidence-bound adjudication')
    return dict(overrides=overrides, unresolved=unresolved,
        binding_sha256=c.canonical(dict(pauses=pauses, records=records)),
        runtime_evidence_sha256=c.canonical(evidence_files(ledger.root)))

def publish(ledger, path, decisions, review_reference):
    """Reviewer supplies retain/reason for every terminal in unresolved segments.

    A reason decision is {reason, evidence_reference, external_occupancy_confirmed}.
    No cost ratio or effect is computed by this interface.
    """
    c = _core()
    path = c.bounded(ledger.root, path)
    expected_parent = ledger.root / 'protocol/resource-adjudications'
    if path.parent.resolve() != expected_parent.resolve() or path.suffix != '.json':
        raise ValueError('Adjudication must use independent append-only protocol directory')
    prior = state(ledger, require_resolved=False)
    pauses = {k: v for k, v in _pauses(ledger.root).items() if k in prior['unresolved']}
    segments = {Path(name).parent.name for name in pauses}
    evidence = {k: v for k, v in evidence_files(ledger.root).items() if Path(k).parent.name in segments}
    raw, events = _events(ledger)
    ends = {e['attempt_id']: e for e in events if e['event'] == 'finished'}
    bound = {}
    for attempt, decision in decisions.items():
        if attempt not in ends:
            raise ValueError('Cannot adjudicate unfinished or nonexistent terminal')
        bound[attempt] = dict(decision, result_sha256=ends[attempt]['result_sha256'], validity_sha256=ends[attempt]['validity_sha256'])
    record = dict(experiment_id=ledger.experiment, source_revision=ledger.source,
        review_reference=review_reference, effect_inspected=False,
        coverage_policy='all_attempts_in_paused_segments',
        impact_scope='conservative whole-segment review; retain unaffected attempts explicitly',
        ledger_prefix_bytes=len(raw), ledger_prefix_sha256=hashlib.sha256(raw).hexdigest(),
        pause_files=pauses, evidence_files=evidence, decisions=bound)
    _check_record(ledger, record, raw, events)
    c.save_exact(path, record)
    return state(ledger)

def effective(event_or_verdict, attempt, resource_state):
    out = copy.deepcopy(event_or_verdict)
    decision = resource_state['overrides'].get(attempt)
    if decision:
        out['raw_cost_eligible'] = out['cost_eligible']
        out['raw_reason'] = out['reason']
        out['adjudication_sha256'] = decision['adjudication_sha256']
        if decision['reason'] != 'retain':
            out.update(cost_eligible=False, reason=decision['reason'])
    return out

def verify_resume(ledger, review):
    c = _core()
    current = state(ledger)
    if _pauses(ledger.root) and (not review or review.get('ledger_sha256') != c.sha(ledger.path) or not review.get('review_reference') or review.get('resource_state_sha256') != current['binding_sha256']):
        raise ValueError('Resume requires ledger and resource adjudication binding')
    return current

def recovery_reason(ledger, info, review):
    """Missing terminal is a record state, never a diagnosis by itself."""
    c = _core()
    if not review or (review.get('experiment_id') != ledger.experiment or
        review.get('source_revision') != ledger.source or
        review.get('attempt_id') != info['attempt_id'] or
        not review.get('review_reference') or review.get('effect_inspected') is not False):
        raise ValueError('Missing terminal requires evidence-bound recovery cause review')
    raw, events = _events(ledger)
    n = review.get('ledger_prefix_bytes')
    if (type(n) is not int or n <= 0 or n > len(raw) or not raw[:n].endswith(b'\n') or
        hashlib.sha256(raw[:n]).hexdigest() != review.get('ledger_prefix_sha256') or
        not any(e.get('attempt_id') == info['attempt_id'] and e['event'] == 'started'
                for e in [json.loads(x) for x in raw[:n].splitlines()])):
        raise ValueError('Recovery review ledger prefix differs or does not cover start')
    reason = review.get('reason')
    if reason not in c.TECHNICAL | c.CAPACITY:
        raise ValueError('Unknown interruption remains unresolved, no technical retry')
    evidence = review.get('evidence_files', {})
    if not evidence:
        raise ValueError('Recovery cause needs preserved raw evidence')
    for name, digest in evidence.items():
        p = c.bounded(ledger.root, ledger.root / name)
        if not p.is_file() or c.sha(p) != digest:
            raise ValueError('Recovery cause evidence changed')
        if p.suffix == '.json':
            record = c.read(p)
            if isinstance(record, dict):
                known = record.get('failure_class', record.get('interruption_cause', record.get('reason')))
                if known in c.CAPACITY and reason != known:
                    raise ValueError('Condition-related evidence cannot become technical retry')
    if reason == 'external_resource_conflict' and review.get('external_occupancy_confirmed') is not True:
        raise ValueError('Unknown recovery cause is not external occupancy')
    return reason

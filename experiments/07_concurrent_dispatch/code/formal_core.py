"""Standalone formal protocol: no pilot import, no model or GPU dependency."""
import csv, json, hashlib, math, random, statistics, os, time, collections, functools, tempfile, re
from pathlib import Path
from contextlib import contextmanager
from gformal_adjudication import state as resource_state, effective, verify_resume, recovery_reason

TECHNICAL = {'wrong_service', 'duplicate_request_id', 'harness_violation', 'external_resource_conflict',
             'log_corruption', 'metering_invalid', 'burst_protocol_deviation', 'transport_protocol_failure'}
CAPACITY = {'capacity_failure', 'timeout', 'engine_failure'}
RECOVERY_PENDING_NAME = re.compile(
    r'\.(?:recovered-cause-review\.json|recovered-terminal\.json|recovered-validity\.json|artifact-manifest\.json)\.pending-[a-z0-9_]{8}')


def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def canonical(obj): return hashlib.sha256(
    json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def read(p): return json.loads(Path(p).read_text(encoding='utf-8'))


def save(p, obj):
    p = Path(p);
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open('x', encoding='utf-8', newline='\n') as f: json.dump(obj, f, ensure_ascii=False,
                                                                     indent=2);f.flush();os.fsync(f.fileno())


def save_exact(p, obj):
    """Publish complete bytes without replacement; same durable contents may re-enter."""
    p = Path(p);
    p.parent.mkdir(parents=True, exist_ok=True)
    expected = json.dumps(obj, ensure_ascii=False, indent=2).encode('utf-8')

    def check():
        if p.is_symlink() or not p.is_file() or p.read_bytes() != expected: raise ValueError(
            'Recovery artifact conflicts with expected bytes: ' + p.name)

    def sync_parent():
        if os.name == 'posix':
            directory = os.open(p.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)

    if p.exists() or p.is_symlink(): check();sync_parent();return
    fd, name = tempfile.mkstemp(prefix='.' + p.name + '.pending-', dir=p.parent);
    pending = Path(name)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(expected);f.flush();os.fsync(f.fileno())
        try:
            os.link(pending, p)
        except FileExistsError:
            check()
        sync_parent()
    finally:
        pending.unlink(missing_ok=True)
    check()


def append(p, obj):
    with Path(p).open('a', encoding='utf-8', newline='\n') as f: f.write(
        json.dumps(obj, ensure_ascii=False, separators=(',', ':')) + '\n');f.flush();os.fsync(f.fileno())


def artifact_manifest(root, dest, row):
    root = Path(root);
    dest = Path(dest);
    files = []
    for p in sorted(dest.rglob('*')):
        if p.is_file() and p.name != 'artifact-manifest.json':
            # Only our exact mkstemp namespaces, directly in this round, are transient.
            # Hard exit can leave a published target and its link; preserve both bytes.
            if p.parent == dest and not p.is_symlink() and RECOVERY_PENDING_NAME.fullmatch(p.name): continue
            bounded(root, p)
            with p.open('r+b') as f:
                os.fsync(f.fileno())
            files.append(dict(path=p.relative_to(root).as_posix(), bytes=p.stat().st_size, sha256=sha(p)))
    prefixes = []
    for x in row.get('trace_prefixes', []):
        p = bounded(root, x['path']);
        raw = p.read_bytes()[:x['prefix_bytes']]
        if len(raw) != x['prefix_bytes'] or hashlib.sha256(raw).hexdigest() != x['prefix_sha256']: raise ValueError(
            'Native trace prefix changed')
        prefixes.append(
            dict(path=p.relative_to(root).as_posix(), prefix_bytes=x['prefix_bytes'], prefix_sha256=x['prefix_sha256']))
    save_exact(dest / 'artifact-manifest.json', dict(files=files, trace_prefixes=prefixes))


def verify_artifacts(root, manifest):
    for x in manifest['files']:
        p = bounded(root, Path(root) / x['path'])
        if p.stat().st_size != x['bytes'] or sha(p) != x['sha256']: raise ValueError('Round raw artifact bytes changed')
    for x in manifest['trace_prefixes']:
        p = bounded(root, Path(root) / x['path']);
        raw = p.read_bytes()[:x['prefix_bytes']]
        if len(raw) != x['prefix_bytes'] or hashlib.sha256(raw).hexdigest() != x['prefix_sha256']: raise ValueError(
            'Native prefix bytes changed')


def utc():
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def bounded(root, p):
    p = Path(p);
    root = Path(root).resolve()
    if p.is_symlink() or not p.resolve().is_relative_to(root): raise ValueError(
        'Artifact outside independent formal root')
    return p


def cellkey(r): return (r['document_id'], r['repeat_id'], r['N'], r['schedule'], r['arm'])


def logical(experiment, c): return 'formal-' + experiment + '-' + canonical(list(cellkey(c)))[:32]


def schedule(docs, R=3, seed=20261011):
    ids = sorted(d['id'] for d in docs)
    if len(ids) != 30 or len(set(ids)) != 30 or R != 3: raise ValueError(
        'Formal requires original30 unique docs and R3')
    rng = random.Random(seed);
    blocks = [(d, k) for d in ids for k in range(1, 4)];
    rng.shuffle(blocks);
    out = []
    for d, k in blocks:
        cells = [(n, s, a) for n in (5, 20) for s in ('S', 'P', 'C') for a in ('D', 'A', 'B')];
        rng.shuffle(cells)
        for n, s, a in cells: out.append(
            dict(position=len(out) + 1, document_id=d, repeat_id=k, N=n, schedule=s, arm=a))
    validate_schedule(out, ids);
    return out


def validate_schedule(rows, ids):
    expected = {(d, k, n, s, a) for d in ids for k in (1, 2, 3) for n in (5, 20) for s in ('S', 'P', 'C') for a in
                ('D', 'A', 'B')}
    if len(ids) != 30 or len(set(ids)) != 30 or len(rows) != 1620 or len({cellkey(r) for r in rows}) != 1620 or {
        cellkey(r) for r in rows} != expected: raise ValueError('Formal matrix not all1620 unique cells')
    if [r['position'] for r in rows] != list(range(1, 1621)) or sum(r['N'] for r in rows) != 20250: raise ValueError(
        'Position/request count differs')


def verify_source(rev):
    rev = Path(rev);
    seal = read(rev / 'source-seal.json');
    actual = {p.relative_to(rev).as_posix(): sha(p) for p in
              sorted((rev / 'code').glob('*.py')) + sorted((rev / 'tests').glob('*.py'))}
    if actual != seal['source_files'] or canonical(actual) != seal['source_bundle_sha256']: raise ValueError(
        'Formal source differs from sealed bundle')
    return seal['source_bundle_sha256']


def check_manifest(m):
    if m['phase'] != 'formal' or m['model'] != 'llama' or m['D'] != 30 or m['R'] != 3 or m['planned_rounds'] != 1620 or \
            m['planned_model_requests'] != 20250: raise ValueError('Wrong formal design')
    if m['attempt_policy'] != {'maximum_attempt_index': 2, 'whole_round_retry_limit': 1, 'automatic_HTTP_retries': 0,
                               'automatic_round_retries': 0, 'pilot_exception_allowed': False}: raise ValueError(
        'Forbidden attempt policy')
    if m['analysis'] != {'primary_N': 20, 'primary_f': .1, 'bootstrap_draws': 10000, 'seed': 20261011, 'R': 3,
                         'primary_arms': ['C-A', 'C-D'], 'interaction_arms': ['C-A', 'C-D', 'S-A', 'S-D'],
                         'all_R_required': True, 'secondary_confirmatory': False}: raise ValueError(
        'Statistical plan differs')
    if m['cost_limit'] != {'unlimited': True, 'rate_RMB_per_hour': 6, 'new_instance_allowed': False} or m[
        'walltime_limit'] != {'unlimited': True}: raise ValueError('Budget authorization differs')
    required = ['experiment_id', 'data_sha256', 'pilot_data_sha256', 'inputs_sha256', 'schedule_sha256',
                'server_config_sha256', 'client_config_sha256', 'protocol_sha256', 'engine_dependency_lock_sha256',
                'model_manifest_sha256', 'token_audit_sha256', 'pilot_evidence_archive_sha256',
                'reviewed_reference_sha256']
    if any(not isinstance(m.get(k), str) or not m[k] for k in required): raise ValueError(
        'Required formal binding missing')
    if not m['experiment_id'].startswith('G_FORMAL_') or '/' in m['experiment_id'] or '\\' in m[
        'experiment_id']: raise ValueError('Unsafe experiment ID')
    if not m['L2_gate']['capability_passed'] or not m['L2_gate']['short_output_observation_passed']: raise ValueError(
        'Dual gate not passed')
    if m['GPU_UUID'] != 'GPU-15654ab8-354e-54e9-17ee-08dd799f7027': raise ValueError('Different GPU')
    return True


def gate(rev, approval):
    rev = Path(rev);
    m = read(rev / 'experiment-manifest.json');
    check_manifest(m);
    source = verify_source(rev)
    if m.get('execution_authorized') is not True: raise ValueError('Formal manifest execution not authorized')
    if sha(rev / 'implementation-v5.md') != m.get('implementation_protocol_sha256'): raise ValueError(
        'v5 implementation protocol binding differs')
    pairs = {'data_sha256': 'data/documents.json', 'pilot_data_sha256': 'data/pilot-documents.json',
             'inputs_sha256': 'data/inputs.json', 'schedule_sha256': 'schedule.json',
             'server_config_sha256': 'server-config.json', 'client_config_sha256': 'client-config.json',
             'protocol_sha256': 'protocol.md', 'token_audit_sha256': 'token-audit.json',
             'reviewed_reference_sha256': 'reference-Llama-config.json'}
    for field, p in pairs.items():
        if sha(rev / p) != m[field]: raise ValueError('Frozen input/config/schedule binding differs: ' + field)
    docs = read(rev / 'data/documents.json');
    pilot = read(rev / 'data/pilot-documents.json')
    if {d['id'] for d in docs} & {d['id'] for d in pilot}: raise ValueError('Pilot/formal document overlap')
    validate_schedule(read(rev / 'schedule.json'), [d['id'] for d in docs])
    for d in docs:
        if hashlib.sha256(d['G'].encode()).hexdigest() != d['G_sha256']: raise ValueError('Document bytes differ')
    audit = read(rev / 'token-audit.json')
    if not audit['passed'] or audit['formal_documents'] != 30 or audit['model'] != 'llama': raise ValueError(
        'Real token audit missing')
    a = read(approval)
    expected = {'source_bundle_sha256': source, 'manifest_sha256': sha(rev / 'experiment-manifest.json'),
                'schedule_sha256': m['schedule_sha256'], 'data_sha256': m['data_sha256'],
                'protocol_sha256': m['protocol_sha256'], 'token_audit_sha256': m['token_audit_sha256']}
    if a.get('execution_authorized') is not True or any(a.get(k) != v for k, v in expected.items()) or not a.get(
        'review_reference'): raise ValueError('Specific formal source/manifest not approved')
    return m


class Ledger:
    def __init__(self, root, experiment, cells, source):
        self.root = Path(root);
        self.path = self.root / 'protocol/attempt-ledger.jsonl';
        self.experiment = experiment;
        self.cells = {logical(experiment, c): c for c in cells};
        self.source = source;
        self.events = []
        self.path.parent.mkdir(parents=True, exist_ok=True);
        self.reload()

    def reload(self):
        raw = self.path.read_bytes() if self.path.exists() else b''
        if raw and not raw.endswith(b'\n'): raise RuntimeError('Partial ledger line; stop')
        self.events = [json.loads(x) for x in raw.splitlines()]
        if any(e.get('experiment_id') != self.experiment or e.get('source_revision') != self.source or e.get(
            'phase') != 'formal' for e in self.events): raise ValueError('Foreign/pilot ledger record')
        history = {}
        for e in self.events:
            k = e.get('logical_id')
            if k not in self.cells or e.get('event') not in ('planned', 'started', 'finished') or any(
                e.get(f) != self.cells[k][f] for f in
                ('document_id', 'repeat_id', 'N', 'schedule', 'arm', 'position')): raise ValueError(
                'Ledger record not frozen formal cell')
            h = history.setdefault(k, {'planned': False, 'starts': [], 'ends': []})
            if e['event'] == 'planned':
                if h['planned']: raise ValueError('Duplicate planned event')
                h['planned'] = True
            elif e['event'] == 'started':
                if not h['planned'] or e.get('attempt_index') not in (1, 2) or e['attempt_index'] != len(
                    h['starts']) + 1: raise ValueError('Invalid attempt start')
                previous = effective(h['ends'][0], h['ends'][0]['attempt_id'], resource_state(self, False)) if h[
                    'ends'] else None
                if e['attempt_index'] == 2 and (len(h['ends']) != 1 or previous['cost_eligible'] or previous[
                    'reason'] not in TECHNICAL): raise ValueError('Illegal formal retry')
                if e['attempt_index'] == 2:
                    decision = e.get('retry_decision') or {}
                    if (e.get('retry_of') != previous['attempt_id'] or e.get('retry_reason') != previous[
                        'reason'] or decision.get('approved') is not True or decision.get('reason') != previous[
                        'reason'] or decision.get('retry_of') != previous['attempt_id'] or decision.get(
                        'source_revision') != self.source or decision.get('result_sha256') != previous[
                        'result_sha256'] or not decision.get('review_reference')): raise ValueError(
                        'Recorded retry does not bind reviewed technical evidence')
                    if 'adjudication_sha256' in previous and decision.get('adjudication_sha256') != previous[
                        'adjudication_sha256']: raise ValueError('Recorded retry resource adjudication differs')
                h['starts'].append(e)
            else:
                if len(h['starts']) != len(h['ends']) + 1 or e['attempt_id'] != h['starts'][-1][
                    'attempt_id']: raise ValueError('Invalid terminal event')
                h['ends'].append(e)

    @contextmanager
    def transaction(self):
        lock = self.path.with_name(self.path.name + '.lock')
        try:
            lock.mkdir()
        except FileExistsError:
            raise RuntimeError('Ledger locked; no launch')
        try:
            self.reload();yield
        finally:
            lock.rmdir()

    def add(self, event):
        e = dict(event, experiment_id=self.experiment, source_revision=self.source, phase='formal', recorded_utc=utc());
        append(self.path, e);
        self.events.append(e)

    def plan(self):
        with self.transaction():
            if self.events:
                p = [e for e in self.events if e['event'] == 'planned']
                if len(p) != len(self.cells) or {e['logical_id'] for e in p} != set(self.cells): raise ValueError(
                    'Plan ledger changed')
                return
            for k, c in self.cells.items(): self.add(dict(c, event='planned', logical_id=k))

    def start(self, cell, segment, decision=None):
        reviewed = resource_state(self)
        k = logical(self.experiment, cell)
        with self.transaction():
            if k not in self.cells or self.cells[k] != cell or not any(
                e['event'] == 'planned' and e['logical_id'] == k for e in self.events): raise ValueError(
                'Unplanned cell')
            history = [e for e in self.events if e.get('logical_id') == k];
            starts = [e for e in history if e['event'] == 'started'];
            ends = [e for e in history if e['event'] == 'finished']
            if not starts:
                if decision: raise ValueError('First cannot have retry authorization')
                index = 1;
                parent = None;
                reason = None
            else:
                if len(starts) != 1 or len(ends) != 1 or starts[0]['attempt_id'] != ends[0][
                    'attempt_id']: raise ValueError('Consumed retry or unfinished attempt; no implicit resume')
                first = effective(ends[0], ends[0]['attempt_id'], reviewed)
                if first['cost_eligible'] or first['reason'] not in TECHNICAL: raise ValueError(
                    'Qualified or condition-related round cannot be retried')
                if not decision or decision.get('approved') is not True or decision.get('reason') != first[
                    'reason'] or decision.get('retry_of') != first['attempt_id'] or decision.get(
                    'source_revision') != self.source or not decision.get('review_reference') or decision.get(
                    'result_sha256') != first['result_sha256']: raise ValueError(
                    'Retry requires evidence-bound technical decision')
                if 'adjudication_sha256' in first and (
                        decision.get('adjudication_sha256') != first['adjudication_sha256'] or decision.get(
                    'resource_state_sha256') != reviewed['binding_sha256']): raise ValueError(
                    'Retry requires exact resource adjudication binding')
                index = 2;
                parent = first['attempt_id'];
                reason = first['reason']
            info = dict(cell, event='started', logical_id=k, attempt_index=index, attempt_id=k + f'-a{index}',
                        retry_of=parent, retry_reason=reason, segment_id=segment, source_revision=self.source,
                        approved_revision=self.source, experiment_id=self.experiment, phase='formal', model='llama',
                        retry_decision=decision)
            self.add(info);
            return info

    def finish(self, info, result_path, decision_path):
        result_path = bounded(self.root, result_path);
        decision_path = bounded(self.root, decision_path);
        row = read(result_path);
        v = read(decision_path)
        if row.get('attempt_id') != info['attempt_id'] or v['attempt_id'] != info['attempt_id']: raise ValueError(
            'Result lineage mismatch')
        artifacts = result_path.parent / 'artifact-manifest.json'
        artifact_manifest(self.root, result_path.parent, row)
        verify_artifacts(self.root, read(artifacts))
        with self.transaction():
            starts = [e for e in self.events if e['event'] == 'started' and e.get('attempt_id') == info['attempt_id']]
            if len(starts) != 1 or any(e['event'] == 'finished' and e.get('attempt_id') == info['attempt_id'] for e in
                                       self.events): raise ValueError('Terminal duplicate/missing start')
            self.add(dict(info, event='finished', result_path=result_path.relative_to(self.root).as_posix(),
                          result_sha256=sha(result_path), validity_path=decision_path.relative_to(self.root).as_posix(),
                          validity_sha256=sha(decision_path),
                          artifact_manifest_path=artifacts.relative_to(self.root).as_posix(),
                          artifact_manifest_sha256=sha(artifacts), cost_eligible=v['cost_eligible'], reason=v['reason'],
                          sent_requests=v.get('sent_requests'), sent_count_known=v['sent_count_known'],
                          completed_requests=v.get('completed_requests'), failed_requests=v.get('failed_requests'),
                          not_sent_requests=v.get('not_sent_requests')))

    def unfinished(self):
        self.reload();
        ends = {e['attempt_id'] for e in self.events if e['event'] == 'finished'}
        return [e for e in self.events if e['event'] == 'started' and e['attempt_id'] not in ends]

    def selected(self):
        if self.unfinished(): raise ValueError('Unfinished attempts prevent selection/restart')
        self.reload();
        rows = [];
        decisions = [];
        reviewed = resource_state(self)
        for k in self.cells:
            ends = [e for e in self.events if e['event'] == 'finished' and e.get('logical_id') == k]
            if not ends: continue
            if sorted(e['attempt_index'] for e in ends) != list(range(1, len(ends) + 1)) or len(
                ends) > 2: raise ValueError('Formal lineage not1/2')
            for e in ends:
                p = bounded(self.root, self.root / e['result_path']);
                v = bounded(self.root, self.root / e['validity_path'])
                if sha(p) != e['result_sha256'] or sha(v) != e['validity_sha256']: raise ValueError(
                    'Sealed terminal bytes changed')
                artifacts = bounded(self.root, self.root / e['artifact_manifest_path'])
                if sha(artifacts) != e['artifact_manifest_sha256']: raise ValueError('Artifact manifest changed')
                verify_artifacts(self.root, read(artifacts))
            e = ends[-1];
            rows.append(read(self.root / e['result_path']));
            decisions.append(effective(read(self.root / e['validity_path']), e['attempt_id'], reviewed))
        return rows, decisions

    def terminal_events(self, require_resolved=True):
        self.reload();
        reviewed = resource_state(self, require_resolved)
        return [effective(e, e['attempt_id'], reviewed) for e in self.events if e['event'] == 'finished']

    def counts(self):
        self.reload();
        ends = self.terminal_events(False);
        first = [e for e in ends if e['attempt_index'] == 1];
        retry = [e for e in ends if e['attempt_index'] == 2]

        def count(xs):
            known = [e for e in xs if e['sent_count_known']]
            return dict(attempts=len(xs), invalid_or_failed=sum(not e['cost_eligible'] for e in xs),
                        known_actual_sent=sum(e['sent_requests'] for e in known),
                        known_request_failures=sum(e.get('failed_requests') or 0 for e in known),
                        planned_not_sent=sum(e.get('not_sent_requests') or 0 for e in known),
                        unknown_sent_attempts=len(xs) - len(known))

        started = {e['logical_id'] for e in self.events if e['event'] == 'started'}
        return dict(planned_rounds=len(self.cells), attempted_original_rounds=len(started),
                    unattempted_original_rounds=len(self.cells) - len(started), first=count(first), retry=count(retry),
                    unfinished=len(self.unfinished()))


def validate_round(info, row, requests, isolation, expected_ids, burst_reference):
    """No CAF/effect computed here. Cost and overlap eligibility remain separate."""
    n = info['N'];
    reason = None;
    errors = []
    if row.get('phase') != 'formal' or row.get('model') != 'llama' or any(row.get(k) != info[k] for k in
                                                                          ('experiment_id', 'source_revision',
                                                                           'attempt_id', 'document_id', 'repeat_id',
                                                                           'N', 'schedule', 'arm')): errors.append(
        'foreign_lineage')
    actual = [r for r in requests if r.get('client_send_monotonic') is not None];
    known = row.get('sent_count_known', True);
    sent = row.get('sent_requests') if not known else len(actual)
    successes = sum(r.get('success') is True for r in actual);
    failures = len(actual) - successes
    if known and (type(sent) is not int or sent < 0 or sent > n): errors.append('send_count_invalid')
    if len(requests) != n or {r.get('replica_id') for r in requests} != set(range(1, n + 1)): errors.append(
        'request_count_or_replica_set')
    if len({r.get('request_id') for r in requests}) != len(requests) or len(
        {r.get('response_id') for r in requests}) != len(requests): reason = 'duplicate_request_id';errors.append(
        'duplicate_request_id')
    metrics = True
    for r in requests:
        i = r.get('replica_id');
        p, h, o = r.get('P_i'), r.get('h_i'), r.get('O_i')
        if type(p) is not int or p <= 0 or type(h) is not int or not 0 <= h <= p or type(
            o) is not int or o < 0 or r.get('native_ids_exact_offline') is not True or r.get(
            'metric_valid') is not True or not r.get('prompt_token_ids_sha256') or (expected_ids and (
                i not in expected_ids or p != len(expected_ids[i]) or r['prompt_token_ids_sha256'] != hashlib.sha256(
            json.dumps(expected_ids[i]).encode()).hexdigest())): metrics = False
        if r.get('HTTP_status') != 200 or r.get('success') is not True: errors.append('HTTP_or_generation_failure')
    if not metrics: errors.append('metering_invalid')
    if not isolation or isolation.get('engine_reset', {}).get('success') is not True or any(
        isolation.get(side, {}).get(x) != 0 for side in ('before', 'after') for x in
        ('vllm:num_requests_running', 'vllm:num_requests_waiting')): errors.append('reset_or_drain_invalid')
    if row.get('tool_call_count') != (1 if info['arm'] == 'B' else n): errors.append('tool_count_invalid')
    if info['schedule'] != 'S':
        b = actual[1:] if info['schedule'] == 'P' else actual;
        t = [r['client_send_monotonic'] for r in b]
        spread = max(t) - min(t) if len(t) == (n - 1 if info['schedule'] == 'P' else n) else None
        if not isinstance(burst_reference, (float, int)) or not math.isfinite(
            burst_reference) or burst_reference <= 0 or spread is None or spread > .05 or spread > .25 * burst_reference: errors.append(
            'burst_protocol_deviation')
    else:
        spread = None
    completed = len(requests) == n and successes == n and all(r.get('success') is True for r in requests)
    if completed and metrics:
        P = sum(r['P_i'] for r in requests);
        H = sum(r['h_i'] for r in requests)
        if row.get('P') != P or row.get('H') != H or row.get('U') != P - H: errors.append('aggregate_mismatch')
    if errors:
        if reason is None:
            if row.get('failure_class') in CAPACITY:
                reason = row['failure_class']
            elif 'HTTP_or_generation_failure' in errors:
                reason = 'transport_protocol_failure' if row.get(
                    'demonstrated_transport_protocol_failure') else 'capacity_failure'
            elif 'burst_protocol_deviation' in errors:
                reason = 'burst_protocol_deviation'
            elif 'metering_invalid' in errors:
                reason = 'metering_invalid'
            else:
                reason = 'harness_violation'
    else:
        reason = 'qualified'
    evidence = row.get('evidence', {});
    state = evidence.get('backend_overlap_status', 'unknown')
    if state not in ('confirmed_overlap', 'confirmed_none', 'unknown'): raise ValueError('Invalid overlap state')
    return dict(attempt_id=info['attempt_id'], logical_id=info['logical_id'], cost_eligible=not errors, reason=reason,
                errors=sorted(set(errors)), complete=completed, overlap_status=state, overlap_is_not_cost_filter=True,
                request_mapping_fraction=evidence.get('request_mapping_fraction'),
                completed_scheduled_steps_fraction=evidence.get('completed_scheduled_steps_fraction'),
                time_coverage=None, time_coverage_missing_reason='step completeness is not clock-time coverage',
                sent_count_known=known, sent_requests=sent, completed_requests=successes if known else None,
                failed_requests=failures if known else None, not_sent_requests=n - sent if known else None,
                burst_send_spread=spread, locked_without_effect=True)


def lock_validity(ledger, path, review_reference):
    rows, decisions = ledger.selected()
    reviewed = resource_state(ledger)
    if not review_reference: raise ValueError('Independent validity review reference required')
    mapping = {d['attempt_id']: d for d in decisions}
    report = {'experiment_id': ledger.experiment, 'source_revision': ledger.source, 'ledger_sha256': sha(ledger.path),
              'selected_attempts': [r['attempt_id'] for r in rows], 'decisions': mapping,
              'review_reference': review_reference, 'effect_computed_before_lock': False, 'created_utc': utc(),
              'resource_state_sha256': reviewed['binding_sha256'],
              'runtime_evidence_sha256': reviewed['runtime_evidence_sha256']}
    save(path, report);
    return report


def bootstrap(values, exponentiate=False, seed=20261011):
    return _bootstrap(tuple(values), exponentiate, seed)


@functools.lru_cache(maxsize=512)
def _bootstrap(values, exponentiate=False, seed=20261011):
    if len(values) < 2: return {
        'estimate': math.exp(statistics.mean(values)) if exponentiate and values else statistics.mean(
            values) if values else None, 'CI95': None, 'n_valid': len(values),
        'reason': 'fewer_than_two_valid_documents'}
    rng = random.Random(seed);
    draws = sorted(math.fsum(rng.choices(values, k=len(values))) / len(values) for _ in range(10000))

    def q(x):
        p = (len(draws) - 1) * x;
        a = int(p);
        b = min(a + 1, len(draws) - 1);
        return draws[a] + (draws[b] - draws[a]) * (p - a)

    tf = math.exp if exponentiate else lambda x: x
    return dict(estimate=tf(statistics.mean(values)), CI95=[tf(q(.025)), tf(q(.975))], n_valid=len(values), draws=10000,
                seed=seed)


def analysis(ledger, lock_path, docs):
    lock = read(lock_path)
    reviewed = resource_state(ledger)
    if lock.get('resource_state_sha256') != reviewed['binding_sha256'] or lock.get('runtime_evidence_sha256') != \
            reviewed['runtime_evidence_sha256']: raise ValueError(
        'Validity lock no longer binds resource adjudication/evidence')
    if sha(ledger.path) != lock['ledger_sha256'] or lock['experiment_id'] != ledger.experiment or lock[
        'source_revision'] != ledger.source: raise ValueError('Validity lock no longer binds raw ledger')
    rows, decisions = ledger.selected()
    if [r['attempt_id'] for r in rows] != lock['selected_attempts'] or {d['attempt_id']: d for d in decisions} != lock[
        'decisions']: raise ValueError('Validity lock altered')
    allowed = {d['id'] for d in docs};
    ix = {};
    valid = {d['attempt_id']: d['cost_eligible'] for d in decisions}
    for r in rows:
        if r.get('phase') != 'formal' or r.get('model') != 'llama' or r.get(
            'experiment_id') != ledger.experiment or r.get('source_revision') != ledger.source or r[
            'document_id'] not in allowed or cellkey(r) in ix: raise ValueError('Duplicate/foreign analysis key')
        ix[cellkey(r)] = r

    def pair(doc, k, n, s, a, f):
        x = ix.get((doc, k, n, s, a));
        d = ix.get((doc, k, n, s, 'D'))
        if not x or not d or not valid[x['attempt_id']] or not valid[d['attempt_id']]: return None
        ca = x['P'] - (1 - f) * x['H'];
        cd = d['P'] - (1 - f) * d['H']
        if not all(math.isfinite(v) and v > 0 for v in (ca, cd)): raise ValueError('Nonpositive/nonfinite cost')
        dp = x['P'] - d['P'];
        dh = x['H'] - d['H']
        return dict(log_CAF=math.log(ca / cd), delta_P=dp, cache_term=-(1 - f) * dh, delta_H=dh, delta_U=dp - dh,
                    delta_C=dp - (1 - f) * dh, C_A=ca, C_D=cd)

    contrasts = []
    for n in (5, 20):
        for s in ('S', 'P', 'C'):
            for a in ('A', 'B'):
                for f in (.1, .25, .5):
                    included = [];
                    excluded = []
                    for doc in sorted(allowed):
                        pairs = [pair(doc, k, n, s, a, f) for k in (1, 2, 3)]
                        if all(p is not None for p in pairs):
                            included.append(dict(document_id=doc, repeat_pairs=pairs,
                                                 document_mean_log=statistics.mean(p['log_CAF'] for p in pairs),
                                                 **{m: statistics.mean(p[m] for p in pairs) for m in
                                                    ('delta_P', 'cache_term', 'delta_H', 'delta_U', 'delta_C')}))
                        else:
                            excluded.append(dict(document_id=doc, reason='requires_all3of3_same_cell_pairs',
                                                 missing_repeats=[k + 1 for k, p in enumerate(pairs) if p is None]))
                    contrasts.append(dict(N=n, schedule=s, arm=a, f=f, documents=included, excluded=excluded,
                                          CAF=bootstrap([d['document_mean_log'] for d in included], True),
                                          differences={m: bootstrap([d[m] for d in included]) for m in
                                                       ('delta_P', 'cache_term', 'delta_H', 'delta_U', 'delta_C')},
                                          secondary_descriptive_uncorrected=True))
    primary = next(c for c in contrasts if (c['N'], c['schedule'], c['arm'], c['f']) == (20, 'C', 'A', .1))
    interaction = [];
    excluded = []
    for doc in sorted(allowed):
        p = [(pair(doc, k, 20, 'C', 'A', .1), pair(doc, k, 20, 'S', 'A', .1)) for k in (1, 2, 3)]
        if all(c is not None and s is not None for c, s in p):
            interaction.append(dict(document_id=doc, repeat_log_interaction=[c['log_CAF'] - s['log_CAF'] for c, s in p],
                                    document_mean_log=statistics.mean(c['log_CAF'] - s['log_CAF'] for c, s in p)))
        else:
            excluded.append(dict(document_id=doc, reason='requires_all3of3_four_arm_groups'))
    ci = primary['CAF']['CI95'];
    direction = 'not_estimable' if ci is None else 'increase' if ci[0] > 1 else 'decrease' if ci[
                                                                                                  1] < 1 else 'direction_not_supported'
    counts = ledger.counts();
    coverage = collections.defaultdict(collections.Counter)
    for e in ledger.events:
        if e['event'] == 'finished':
            v = read(ledger.root / e['validity_path']);
            coverage[(e['N'], e['schedule'], e['arm'])][v['overlap_status']] += 1
    return dict(phase='formal', model='llama', R=3, planned_document_count=30, primary=primary,
                primary_direction=direction, interaction=dict(documents=interaction, excluded=excluded, log=bootstrap(
            [d['document_mean_log'] for d in interaction]), exp_ratio=bootstrap(
            [d['document_mean_log'] for d in interaction], True), descriptive_uncorrected=True),
                all_cost_contrasts=contrasts, counts=counts,
                overlap_all_attempts=[dict(N=k[0], schedule=k[1], arm=k[2], states=dict(v)) for k, v in
                                      sorted(coverage.items())],
                selection_limitation='complete_successful_paired_document_subset; condition-related missingness may make full-planned-population effect not estimable',
                planned_population_estimate_claim=False, validity_lock_sha256=sha(lock_path), no_pilot_records=True)


class Runner:
    """Real coordinator used by production and synthetic CPU backends alike."""

    def __init__(self, root, manifest, cells, source, backend, guard):
        self.root = Path(root);
        self.m = manifest;
        self.cells = cells;
        self.source = source;
        self.backend = backend;
        self.guard = guard;
        self.ledger = Ledger(root, manifest['experiment_id'], cells, source);
        self.ledger.plan()

    async def run(self, segment, retry_decisions=None, resume_review=None):
        retry_decisions = retry_decisions or {};
        self.guard()
        if self.ledger.unfinished(): raise RuntimeError(
            'Unfinished attempt requires evidence-bound terminal recovery, never resend')
        if set(retry_decisions) - set(self.ledger.cells): raise ValueError('Unknown retry cell')
        verify_resume(self.ledger, resume_review)
        for cell in self.cells:
            self.ledger.reload();
            k = logical(self.m['experiment_id'], cell);
            ends = [e for e in self.ledger.events if e['event'] == 'finished' and e.get('logical_id') == k]
            if ends and k not in retry_decisions: continue
            self.guard();
            info = self.ledger.start(cell, segment, retry_decisions.get(k));
            dest = self.root / 'rounds' / info['attempt_id'];
            dest.mkdir(parents=True)
            row = None
            try:
                row, requests, isolation, expected, reference = await self.backend.execute(cell, info, dest, self.guard)
                row = dict(row, **{k: info[k] for k in
                                   ('experiment_id', 'phase', 'model', 'source_revision', 'attempt_id', 'logical_id',
                                    'document_id', 'repeat_id', 'N', 'schedule', 'arm', 'attempt_index', 'retry_of',
                                    'retry_reason', 'segment_id')})
                if not (dest / 'result.json').exists():
                    save(dest / 'result.json', row)
                else:
                    original = read(dest / 'result.json')
                    if original != row: raise ValueError('Backend result bytes do not contain frozen lineage')
                verdict = validate_round(info, row, requests, isolation, expected, reference)
                save(dest / 'validity.json', verdict);
                self.ledger.finish(info, dest / 'result.json', dest / 'validity.json')
                if verdict['reason'] in TECHNICAL or (
                        info['attempt_index'] == 2 and not verdict['cost_eligible']): raise RuntimeError(
                    'Persisted technical failure; pause for reviewed whole-round retry')
                await self.backend.drain_after(cell, row)
            except BaseException as exc:
                self.ledger.reload();
                done = any(
                    e['event'] == 'finished' and e.get('attempt_id') == info['attempt_id'] for e in self.ledger.events)
                if not done:
                    if (dest / 'validity.json').exists():
                        save(self.root / 'segments' / segment / (info['attempt_id'] + '-pause.json'),
                             dict(reason=str(exc), attempt_id=info['attempt_id'], terminal_persisted=False,
                                  no_automatic_resume=True, requires_terminal_recovery=True))
                        raise
                    detail = self.backend.failure_snapshot() if hasattr(self.backend, 'failure_snapshot') else {}
                    terminal = dict(info, complete=False, P=None, H=None, U=None, tool_call_count=None,
                                    evidence={'backend_overlap_status': 'unknown'},
                                    failure_class=detail.get('failure_class', 'engine_failure'),
                                    failure={'type': type(exc).__name__, 'message': str(exc)},
                                    sent_count_known=detail.get('sent_count_known', False),
                                    sent_requests=detail.get('sent_requests'))
                    terminal.pop('event', None);
                    save(dest / 'terminal-failure.json', terminal)
                    v = dict(attempt_id=info['attempt_id'], logical_id=info['logical_id'], cost_eligible=False,
                             reason=detail.get('failure_class', 'engine_failure'), errors=['exception'], complete=False,
                             overlap_status='unknown', overlap_is_not_cost_filter=True, request_mapping_fraction=None,
                             time_coverage=None, sent_count_known=detail.get('sent_count_known', False),
                             sent_requests=detail.get('sent_requests'), completed_requests=None, failed_requests=None,
                             not_sent_requests=None, locked_without_effect=True)
                    save(dest / 'validity.json', v);
                    self.ledger.finish(info, dest / 'terminal-failure.json', dest / 'validity.json')
                save(self.root / 'segments' / segment / (info['attempt_id'] + '-pause.json'),
                     dict(reason=str(exc), attempt_id=info['attempt_id'], terminal_persisted=True,
                          no_automatic_resume=True))
                raise
        return self.ledger.counts()


def recover_unfinished(ledger, cleanup_evidence, review_reference, cause_reviews=None):
    if not review_reference or cleanup_evidence.get('experiment_id') != ledger.experiment or cleanup_evidence.get(
        'source_revision') != ledger.source or cleanup_evidence.get(
        'G_service_stopped') is not True or cleanup_evidence.get('own_port_closed') is not True or cleanup_evidence.get(
        'owned_GPU_idle') is not True or cleanup_evidence.get('cleanup_errors'): raise ValueError(
        'Recovery requires same-source verified owned cleanup and reviewer')
    for info in ledger.unfinished():
        dest = ledger.root / 'rounds' / info['attempt_id'];
        dest.mkdir(parents=True, exist_ok=True)
        existing = [p for p in (dest / 'result.json', dest / 'terminal-failure.json') if p.exists()]
        if len(existing) > 1: raise ValueError('Ambiguous orphan result')
        if existing and (dest / 'validity.json').exists():
            rawrow = read(existing[0]);
            verdict = read(dest / 'validity.json')
            known = rawrow.get('failure_class', cleanup_evidence.get('interruption_cause'))
            if known in CAPACITY and verdict.get('reason') != known: raise ValueError(
                'Condition-related orphan terminal verdict differs')
            ledger.finish(info, existing[0], dest / 'validity.json');
            continue
        cause = (cause_reviews or {}).get(info['attempt_id'])
        reason = recovery_reason(ledger, info, cause)
        known = cleanup_evidence.get('interruption_cause')
        for rawpath in existing:
            rawrow = read(rawpath)
            if rawrow.get('failure_class') in CAPACITY: known = rawrow['failure_class']
        if known in CAPACITY and reason != known: raise ValueError(
            'Condition-related interruption cannot be reclassified for retry')
        p = dest / 'recovered-terminal.json'
        if p.exists():
            prior = read(p)
            if prior.get('cleanup_evidence') != cleanup_evidence or prior.get(
                'review_reference') != review_reference or prior.get('cause_review') != cause or prior.get(
                'failure_class') != reason: raise ValueError('Recovery artifact conflicts with reviewed terminal')
            # Revalidate prefix and raw evidence even on durable publication reentry.
            original = read(dest / 'recovered-cause-review.json')
            if original != cause: raise ValueError('Recovery cause record changed')
        save_exact(dest / 'recovered-cause-review.json', cause)
        save_exact(p, dict(info, complete=False, sent_count_known=False, sent_requests=None, failure_class=reason,
                           cleanup_evidence=cleanup_evidence, review_reference=review_reference, cause_review=cause))
        v = dict(attempt_id=info['attempt_id'], logical_id=info['logical_id'], cost_eligible=False, reason=reason,
                 errors=['interrupted_without_verified_terminal'], complete=False, overlap_status='unknown',
                 overlap_is_not_cost_filter=True, sent_count_known=False, sent_requests=None, completed_requests=None,
                 failed_requests=None, not_sent_requests=None, locked_without_effect=True)
        path = dest / 'recovered-validity.json';
        save_exact(path, v);
        ledger.finish(info, p, path)

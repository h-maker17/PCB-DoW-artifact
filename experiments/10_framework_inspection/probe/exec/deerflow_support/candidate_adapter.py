"""Candidate adapter only. Import does not run framework. Parent must supply admission evidence."""
from isolation import prepare, ROOT

prepare()
import asyncio, json, time


def make_config(model_base_url, retrieval_base_url, local_token, dataset_ids):
    from deerflow.config.app_config import AppConfig, set_app_config
    config = AppConfig.model_validate({'models': [
        {'name': 'scan-logging-stub', 'use': 'langchain_openai:ChatOpenAI', 'model': 'scan-logging-stub',
         'base_url': model_base_url, 'api_key': 'local-non-secret-model-stub'}],
                                       'sandbox': {'use': 'deerflow.sandbox.local:LocalSandboxProvider'},
                                       'knowledge_base': {'enabled': True, 'scope_selection_enabled': True},
                                       'tool_groups': [{'name': 'knowledge'}], 'tools': [
            {'name': 'knowledge_search', 'group': 'knowledge',
             'use': 'deerflow.community.ragflow.tools:knowledge_search_tool', 'base_url': retrieval_base_url,
             'api_key': local_token, 'datasets': dataset_ids, 'timeout': 30, 'page_size': 8,
             'similarity_threshold': 0.2, 'vector_similarity_weight': 0.3, 'top_k': 256, 'max_chars_per_chunk': 800,
             'max_total_chars': 8000}]})
    set_app_config(config)
    return config


def dispatch_native(config, task, replicas=4, *, parent_admission=None, artifacts=None, evidence_provider=None,
                    observer_context=None):
    """No body scheduler: submit all branches to original process admission/executor."""
    if not parent_admission or parent_admission.get('rank') != 4 or parent_admission.get('approved') is not True:
        raise PermissionError(
            'Parent admission for fixed rank4 is required; candidate never starts formal experiments automatically.')
    if artifacts is None or evidence_provider is None: raise ValueError(
        'Explicit isolated run/phase and branch evidence collector required before execution')
    from deerflow.subagents.registry import get_subagent_config
    from deerflow.subagents.executor import SubagentExecutor
    from deerflow.subagents.capacity import SubagentExecutionCapacity
    from deerflow.tools.tools import get_available_tools
    from deerflow.subagents.executor import get_background_task_result
    capacity = SubagentExecutionCapacity(config.subagent_runtime)
    assert config.subagent_runtime.model_dump() == {'max_running': 3, 'max_queued': 64, 'admission_policy': 'queue',
                                                    'queue_timeout_seconds': 300}
    tools = get_available_tools(app_config=config, subagent_enabled=True)
    builtin = get_subagent_config('general-purpose', app_config=config)
    assert builtin.max_turns == 150 and builtin.timeout_seconds == 1800
    executors = [SubagentExecutor(builtin, tools, app_config=config, parent_model='scan-logging-stub',
                                  thread_id=f'{artifacts.run_id}-{artifacts.phase}-r{i}',
                                  user_id='scan-deerflow-isolated', execution_capacity=capacity) for i in
                 range(replicas)]
    handles = []
    try:
        from contextlib import nullcontext
        for i, executor in enumerate(executors):
            with observer_context(i) if observer_context is not None else nullcontext(): handles.append(
                executor.execute_async(task))
    except BaseException as exc:
        artifacts.append(artifacts.new_log('submission-error'), {'error': repr(exc), 'branches': [
            {'replica': i, 'handle': h, 'native': snapshot_result(get_background_task_result(h))} for i, h in
            enumerate(handles)], 'unsubmitted_replicas': list(range(len(handles), replicas))})
        from deerflow.subagents.executor import cleanup_background_task, request_cancel_background_task
        for h in handles:
            result = get_background_task_result(h)
            if result is not None and result.status.is_terminal:
                cleanup_background_task(h)
            elif result is not None:
                request_cancel_background_task(h)
        raise
    return {'executors': executors, 'handles': handles, 'capacity': capacity, 'artifacts': artifacts,
            'evidence_provider': evidence_provider}


def ordinary_task(history, query):
    """Default isolated HumanMessage task content, not native history/snapshot."""
    if not isinstance(history, str) or not isinstance(query, str): raise TypeError('history/query must be strings')
    return history + '\n\n' + query


def register_common_stub(ctx):
    """Register the pre-frozen native tool name/query with the shared SCAN stub."""
    ctx.stub_tool_call = {'name': 'knowledge_search', 'arguments': {'query': ctx.query}}


class CandidateTerminalError(RuntimeError):
    def __init__(self, results, reasons=None):
        self.results = results;
        self.reasons = reasons or []
        super().__init__(
            str(self.reasons or [(r.status.value, r.error, r.stop_reason) if r else None for r in results]))


def snapshot_result(result):
    import dataclasses, copy, datetime, enum
    if result is None: return None
    lock = getattr(result, '_state_lock', None)

    def capture():
        out = {}
        for field in dataclasses.fields(result):
            if field.name in ('_state_lock', 'cancel_event'): continue
            value = getattr(result, field.name)
            out[field.name] = copy.deepcopy(value)
        return out

    if lock is not None:
        with lock:
            out = capture()
    else:
        out = capture()

    def serial(x):
        if isinstance(x, enum.Enum): return x.value
        if isinstance(x, datetime.datetime): return x.isoformat()
        if isinstance(x, dict): return {k: serial(v) for k, v in x.items()}
        if isinstance(x, (list, tuple)): return [serial(v) for v in x]
        return x

    return serial(out)


def validate_terminal_evidence(result, evidence, replica, *, artifacts=None):
    """Fail closed on absent/unknown branch attribution or fabricated terminal success.
    Evidence provider must return live archived records from the shared instrument;
    fixtures only test this validator and never establish actual executor completion.
    """
    reasons = []
    if result is None: return ['missing native result']
    if result.status.value != 'completed' or result.stop_reason or result.error or result.admission_failure: reasons.append(
        'native status/error/cap/admission failure')
    if not isinstance(result.result, str) or not result.result.strip(): reasons.append('empty native final answer')
    models = evidence.get('model', []) if isinstance(evidence, dict) else []
    retrieval = evidence.get('retrieval', []) if isinstance(evidence, dict) else []
    if not models or not retrieval: return reasons + ['missing model/tool or HTTP evidence']
    for record in [*models, *retrieval]:
        if artifacts is not None and (
                record.get('run_id') != artifacts.run_id or record.get('phase') != artifacts.phase): reasons.append(
            'stale or mismatched run/phase evidence')
        if str(record.get('replica')) != str(replica): reasons.append('missing or incorrect branch attribution')
        raw = record.get('raw_body_text')
        try:
            parsed = {} if raw == '' and record.get('method') == 'GET' else json.loads(raw)
            if not isinstance(raw, str) or parsed != record.get('request', record.get('body')): reasons.append(
                'raw HTTP request absent or inconsistent')
        except (ValueError, TypeError):
            reasons.append('raw HTTP request invalid')
    calls = [c for r in models for c in r.get('response_message', {}).get('tool_calls', []) if
             c.get('function', {}).get('name') == 'knowledge_search' and c.get('id') == 'call_scan_fixed']
    toolmsgs = [m for r in models for m in r.get('request', {}).get('messages', []) if
                m.get('role') == 'tool' and m.get('tool_call_id') == 'call_scan_fixed' and isinstance(m.get('content'),
                                                                                                      str) and '#knowledge-' in
                m['content'] and not m['content'].startswith('Error:')]
    successes = [r for r in retrieval if r.get('method') == 'POST' and r.get('path') == '/api/v1/retrieval' and r.get(
        'status') == 200 and r.get('response', {}).get('code') == 0 and r.get('response', {}).get('data', {}).get(
        'chunks')]
    if not calls or not toolmsgs or not successes: reasons.append(
        'actual native knowledge_search call/result or successful indexed HTTP return absent')
    chunks = [c for r in successes for c in r['response']['data']['chunks']]
    if not any(
        isinstance(c.get('content'), str) and c['content'] and c['content'] in m['content'] for c in chunks for m in
        toolmsgs): reasons.append('indexed HTTP content absent from actual native ToolMessage')
    queries = set()
    for c in calls:
        try:
            queries.add(json.loads(c['function']['arguments'])['query'])
        except (ValueError, KeyError, TypeError):
            reasons.append('malformed knowledge_search arguments')
    if not queries or any(r.get('body', {}).get('question') not in queries for r in successes): reasons.append(
        'tool query and retrieval HTTP query mismatch')
    final = [r.get('response_message', {}).get('content') for r in models if r.get('finish_reason') == 'stop']
    if not any(isinstance(x, str) and x.strip() and x == result.result for x in final): reasons.append(
        'final native answer lacks matching actual model completion')
    if result.tool_receipts:
        from deerflow.agents.middlewares.tool_receipt import is_valid_receipt
        good = [r for r in result.tool_receipts if
                is_valid_receipt({k: v for k, v in r.items() if k != 'id'}) and r.get(
                    'tool_name') == 'knowledge_search' and r.get('tool_call_id') == 'call_scan_fixed' and r.get(
                    'status') == 'success' and r.get('output_bytes', 0) > 0]
        if not good: reasons.append('native tool receipts malformed or unsuccessful')
    return reasons


async def collect_native(dispatch):
    from deerflow.subagents.executor import get_background_task_result, cleanup_background_task, \
        request_cancel_background_task
    artifacts = dispatch.get('artifacts');
    provider = dispatch.get('evidence_provider')
    if artifacts is None or not callable(provider): raise ValueError(
        'Explicit run/phase artifacts and actual per-branch evidence provider required')
    log = artifacts.new_log('all-branch-status');
    dispatch['status_log'] = log
    capacity_log = artifacts.new_log('native-capacity');
    last_capacity = None
    deadline = time.monotonic() + 1800 + 300;
    results = []

    def archive(stage, error=None):
        records = [];
        failures = []
        for i, h in enumerate(dispatch['handles']):
            r = get_background_task_result(h)
            try:
                e = provider(i, h)
            except Exception as exc:
                e = {'collector_error': repr(exc)};failures.append(repr(exc))
            records.append({'replica': i, 'handle': h, 'native': snapshot_result(r), 'evidence': e})
        artifacts.append(log, {'stage': stage, 'error': error, 'branches': records})
        return records, failures

    try:
        while time.monotonic() < deadline:
            if dispatch.get('capacity') is not None:
                import dataclasses
                observed = dataclasses.asdict(dispatch['capacity'].snapshot())
                if observed != last_capacity: artifacts.append(capacity_log, {'snapshot': observed,
                                                                              'monotonic': time.monotonic()});last_capacity = observed
            results = [get_background_task_result(h) for h in dispatch['handles']]
            if all(r is not None and r.status.is_terminal for r in results):
                records, failures = archive('terminal-before-validation')
                reasons = [{'replica': i,
                            'reasons': validate_terminal_evidence(r, records[i]['evidence'], i, artifacts=artifacts)}
                           for i, r in enumerate(results)]
                reasons = [x for x in reasons if x['reasons']]
                if reasons or failures: raise CandidateTerminalError(results, reasons + failures)
                return results
            await asyncio.sleep(0.05)
        raise TimeoutError('Original native execution/queue horizon exceeded; no repair or classification.')
    except BaseException as exc:
        archive('exception-before-cleanup', repr(exc))
        raise
    finally:
        # Persist all fields/branches before any registry deletion or cancellation.
        archive('finally-before-cleanup')
        for h in dispatch['handles']:
            result = get_background_task_result(h)
            if result is not None and result.status.is_terminal:
                cleanup_background_task(h)
            elif result is not None:
                request_cancel_background_task(h)
        archive('after-cleanup-or-cancel')

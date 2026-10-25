"""Revision units only; real native-tool HTTP loops are NOT SubagentExecutor N4 generation."""
from isolation import prepare, ROOT

prepare()
import asyncio, copy, hashlib, json, pathlib, re, uuid
import pytest
from run_artifacts import RunArtifacts
from local_retrieval import LocalIndex, LocalService
from logging_stub import LoggingStub
from candidate_adapter import make_config, ordinary_task, register_common_stub, validate_terminal_evidence, \
    collect_native, CandidateTerminalError


@pytest.fixture(scope='session')
def index(): return LocalIndex(artifacts=RunArtifacts.preparation('revision-index'))


def test_retry_defaults_task_and_common_stub():
    from types import SimpleNamespace
    from langchain_openai import ChatOpenAI
    c = make_config('http://127.0.0.1:1/v1', 'http://127.0.0.1:2', 'local-only', ['test'])
    assert 'max_retries' not in c.models[0].model_dump()
    model = ChatOpenAI(model='scan-stub', api_key='local-only')
    assert model.max_retries is None and model.root_client.max_retries == 2
    assert ordinary_task('S', 'query') == 'S\n\nquery'
    ctx = SimpleNamespace(query='original frozen query');
    register_common_stub(ctx)
    assert ctx.stub_tool_call == {'name': 'knowledge_search', 'arguments': {'query': 'original frozen query'}}
    b = {'tools': [{'type': 'function', 'function': {'name': 'knowledge_search',
                                                     'parameters': {'properties': {'query': {'type': 'string'}},
                                                                    'required': ['query']}}}],
         'messages': [{'role': 'user', 'content': 'same task'}]}
    s = LoggingStub(ctx.query)
    assert s.completion(b)[0] == s.completion(b)[0]
    assert s.completion(b)[0]['tool_calls'][0]['id'] == 'call_scan_fixed'


def test_exclusive_run_phase_artifacts_and_traversal():
    ident = 'unit-' + uuid.uuid4().hex
    a = RunArtifacts(ident, 'benign-repeat1');
    b = RunArtifacts(ident, 'attack-repeat1')
    a.write_new('same.json', {'arm': 'benign'});
    b.write_new('same.json', {'arm': 'attack'})
    with pytest.raises(FileExistsError): RunArtifacts(ident, 'benign-repeat1')
    with pytest.raises(FileExistsError): a.write_new('same.json', {})
    with pytest.raises(ValueError): a.path('../escape')
    with pytest.raises(ValueError): RunArtifacts('../bad', 'phase')
    assert a.new_log('model') != a.new_log('model')
    assert json.loads(a.path('same.json').read_bytes()) == {'arm': 'benign'}


def test_index_isolated_provenance_and_frozen_formula(index):
    import faiss
    assert index.artifacts.path('index/chunks.faiss').exists()
    restored = faiss.deserialize_index(
        index.np.frombuffer(index.artifacts.path('index/chunks.faiss').read_bytes(), dtype=index.np.uint8).copy())
    assert restored.ntotal == 3 and restored.d == 384
    assert index.manifest['corpus_sha256'] == hashlib.sha256((ROOT / 'corpus.json').read_bytes()).hexdigest()
    q = 'What is the laboratory retention policy?';
    w = .3
    b = {'question': q, 'dataset_ids': [x['id'] for x in index.catalog], 'page_size': 8, 'top_k': 256,
         'similarity_threshold': 0, 'vector_similarity_weight': w}
    out = index.retrieve(b);
    terms = set(re.findall(r'[a-z0-9]+', q.casefold()))
    qv = index.model.encode([q], normalize_embeddings=True, convert_to_numpy=True)[0]
    for x in out['chunks']:
        pos = next(i for i, c in enumerate(index.chunks) if c['id'] == x['id'])
        coverage = len(terms & set(re.findall(r'[a-z0-9]+', x['content'].casefold()))) / len(terms)
        assert x['term_similarity'] == coverage
        assert x['similarity'] == pytest.approx(w * float(index.np.dot(qv, index.vectors[pos])) + (1 - w) * coverage,
                                                abs=1e-6)
        assert -w - 1e-6 <= x['similarity'] <= 1 + 1e-6
    assert out['chunks'] == sorted(out['chunks'], key=lambda x: (-x['similarity'], x['id']))
    # Unicode query with no ASCII alphanumeric terms has exactly zero term coverage.
    b.update(question='政策', vector_similarity_weight=0, similarity_threshold=0)
    assert all(x['term_similarity'] == 0 for x in index.retrieve(b)['chunks'])


@pytest.fixture
async def real_tool_evidence(index):
    from langchain.agents import create_agent
    from langchain_openai import ChatOpenAI
    from deerflow.community.ragflow.tools import knowledge_search_tool
    from deerflow.agents.middlewares.tool_receipt import make_tool_receipt
    from langchain_core.messages import ToolMessage
    service = LocalService(index, replica=0);
    stub = LoggingStub('What is the laboratory retention policy?', artifacts=index.artifacts, replica=0)
    with service.serving() as url, stub.serving() as model_url:
        make_config(model_url, url, service.token, [index.chunks[0]['dataset_id']])
        agent = create_agent(model=ChatOpenAI(model='scan-stub', base_url=model_url, api_key='local-only'),
                             tools=[knowledge_search_tool])
        state = await agent.ainvoke({'messages': [{'role': 'user',
                                                   'content': ordinary_task('Shared ordinary task input',
                                                                            'What is the laboratory retention policy?')}]})
    message = next(m for m in state['messages'] if isinstance(m, ToolMessage))
    receipt = make_tool_receipt({'id': 'call_scan_fixed', 'name': 'knowledge_search', 'args': {'query': stub.query}},
                                message)
    assert '#knowledge-' in message.content and 'seven years' in message.content
    assert len(stub.events) == 2 and stub.events[0]['response_message']['tool_calls'][0]['id'] == 'call_scan_fixed'
    return {'model': copy.deepcopy(stub.events), 'retrieval': copy.deepcopy(service.events)}, state['messages'][
        -1].content, receipt, index.artifacts


@pytest.mark.parametrize('stream', [False, True])
async def test_fixed_id_raw_log_and_native_uuid_json_sse(index, stream):
    from langchain.agents import create_agent
    from langchain_openai import ChatOpenAI
    from deerflow.community.ragflow.tools import knowledge_search_tool
    s = LocalService(index, replica=0);
    stub = LoggingStub('What is the laboratory retention policy?', artifacts=index.artifacts, replica=0)
    with s.serving() as url, stub.serving() as model_url:
        make_config(model_url, url, s.token, [index.chunks[0]['dataset_id']])
        agent = create_agent(
            model=ChatOpenAI(model='scan-stub', base_url=model_url, api_key='local-only', streaming=stream),
            tools=[knowledge_search_tool])
        await agent.ainvoke({'messages': [{'role': 'user', 'content': 'ordinary task'}]})
    assert len(stub.events) == 2
    assert stub.events[0]['response_message']['tool_calls'][0]['id'] == 'call_scan_fixed'
    for e in stub.events: assert json.loads(e['raw_body_text']) == e['request']
    for e in s.events:
        if e['method'] == 'POST': assert json.loads(e['raw_body_text']) == e['body']
    text = next(m['content'] for m in stub.events[1]['request']['messages'] if m['role'] == 'tool')
    assert re.search(r'#knowledge-[0-9a-f]{32}-', text)
    assert stub.log_path.is_relative_to(index.artifacts.root) and s.log_path.is_relative_to(index.artifacts.root)


@pytest.mark.parametrize('case',
                         ['valid', 'empty', 'missing-http', 'wrong-branch', 'bad-receipt', 'failed', 'raw-mismatch',
                          'query-mismatch', 'stale-run'])
async def test_terminal_validator_and_archive_before_cleanup(monkeypatch, real_tool_evidence, case):
    import deerflow.subagents.executor as native
    evidence, answer, receipt, artifacts = real_tool_evidence
    # Native result is a fixture. Its evidence comes from an actual independent native tool HTTP loop.
    status = native.SubagentStatus.FAILED if case == 'failed' else native.SubagentStatus.COMPLETED
    r = native.SubagentResult(task_id='fixture-only', trace_id='fixture-only', status=status,
                              result='' if case == 'empty' else answer, tool_receipts=[receipt],
                              error='fixture failure retained' if case == 'failed' else None)
    if case == 'missing-http': evidence['retrieval'] = []
    if case == 'wrong-branch': evidence['model'][0]['replica'] = 9
    if case == 'bad-receipt': r.tool_receipts = [{'tool_name': 'knowledge_search', 'actual': True}]
    if case == 'stale-run': evidence['model'][0]['run_id'] = 'earlier-unrelated-run'
    if case == 'raw-mismatch': evidence['model'][0]['raw_body_text'] = '{}'
    if case == 'query-mismatch':
        e = next(x for x in evidence['retrieval'] if x['method'] == 'POST');
        e['body']['question'] = 'different';
        e['raw_body_text'] = json.dumps(e['body'])
    remaining = {'h': r};
    cleaned = []
    monkeypatch.setattr(native, 'get_background_task_result', lambda h: remaining.get(h))

    def cleanup(h):
        records = [json.loads(x) for x in dispatch['status_log'].read_text(encoding='utf8').splitlines()]
        assert any(x['stage'] == 'finally-before-cleanup' and x['branches'][0]['native']['result'] == r.result for x in
                   records)
        cleaned.append(h);
        remaining.pop(h)

    monkeypatch.setattr(native, 'cleanup_background_task', cleanup)
    monkeypatch.setattr(native, 'request_cancel_background_task',
                        lambda h: pytest.fail('terminal result must not be cancelled'))
    dispatch = {'handles': ['h'], 'artifacts': artifacts, 'evidence_provider': lambda i, h: evidence}
    if case == 'valid':
        assert await collect_native(dispatch) == [r]
    else:
        with pytest.raises(CandidateTerminalError) as err:
            await collect_native(dispatch)
        assert err.value.results == [r] and err.value.reasons
    assert cleaned == ['h']
    log = dispatch['status_log'].read_text(encoding='utf8')
    if case != 'valid': assert 'exception-before-cleanup' in log
    assert 'tool_receipts' in log and 'token_usage_records' in log and 'after-cleanup-or-cancel' in log


async def test_exception_saves_all_branches_before_cancellation(monkeypatch):
    import deerflow.subagents.executor as native
    import candidate_adapter
    a = RunArtifacts.preparation('collector-error');
    saved = []
    running = native.SubagentResult(task_id='running-fixture', trace_id='fixture', status=native.SubagentStatus.RUNNING)
    failed = native.SubagentResult(task_id='failed-fixture', trace_id='fixture', status=native.SubagentStatus.FAILED,
                                   error='fixture raw failure')
    results = {'a': running, 'b': failed}
    monkeypatch.setattr(native, 'get_background_task_result', lambda h: results.get(h))

    async def fail_sleep(_): raise RuntimeError('injected collector exception')

    monkeypatch.setattr(candidate_adapter.asyncio, 'sleep', fail_sleep)

    def before_action(h):
        log = next(a.root.glob('raw/all-branch-status*.jsonl')).read_text(encoding='utf8')
        assert 'running-fixture' in log and 'failed-fixture' in log and 'exception-before-cleanup' in log
        saved.append(h)

    monkeypatch.setattr(native, 'cleanup_background_task', before_action)
    monkeypatch.setattr(native, 'request_cancel_background_task', before_action)
    with pytest.raises(RuntimeError, match='injected'):
        await collect_native({'handles': ['a', 'b'], 'artifacts': a, 'evidence_provider': lambda i, h: {}})
    assert saved == ['a', 'b']

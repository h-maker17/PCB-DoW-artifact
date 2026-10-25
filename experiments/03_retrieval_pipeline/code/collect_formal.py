from collections import Counter
from common import *


def main():
    cfg = read(R / 'config/formal-protocol.json');
    freeze = read(R / 'evidence/formal-freeze.json');
    assert digest((R / 'config/formal-protocol.json').read_bytes()) == freeze['protocol_sha256']
    for name, h in freeze['code_sha256'].items(): assert digest((R / 'code' / name).read_bytes()) == h
    docs = read(R / 'datasets/formal-queries.json');
    expected = {(d['task'], d['id'], N, mode) for d in docs for N in [5, 20] for mode in cfg['formal_plan']['arms']};
    results = [];
    salts = set();
    counts = Counter();
    prediction_differences = []
    for p in sorted((R / 'data/formal').glob('*/result.json')):
        d = read(p);
        key = (d['task'], d['id'], d['N'], d['mode']);
        assert key in expected;
        expected.remove(key);
        assert d['cache_salt'] not in salts;
        salts.add(d['cache_salt']);
        calls = read(p.parent / 'tool-calls.json');
        assert len(calls) == d['N']
        native = d['mode'].startswith('functiontool');
        prefixes = []
        for i, w in enumerate(d['workers'], 1):
            turns = [1, 2] if native else [None]
            for turn in turns:
                raw = read(p.parent / (f'{i:04d}-{turn}-raw.json' if native else f'{i:04d}-raw.json'));
                q, s = raw['request'], raw['response'];
                u = s['usage'];
                stage = w['stages'][turn - 1] if native else w;
                ids = s['prompt_token_ids'];
                assert digest(ids) == stage['native_token_sha256'] and len(ids) == u['prompt_tokens'] == stage[
                    'prompt'] and u['prompt_tokens_details']['cached_tokens'] == stage['cached'];
                assert q['cache_salt'] == d['cache_salt'] and q['temperature'] == 0 and q['seed'] == 42 and q[
                    'max_tokens'] == 2048
                assert s['choices'][0]['finish_reason'] in (['stop', 'tool_calls'] if native and turn == 1 else ['stop',
                                                                                                                 'length'] if native and turn == 2 else [
                    'stop'])
                if native and turn == 1:
                    generated = s['choices'][0]['message']['tool_calls'];
                    assert len(generated) == 1 and generated[0]['function'][
                        'name'] == 'search_documents' and json.loads(generated[0]['function']['arguments']) == {}
                if i == 1 and turn in [None, 1]: assert stage['cached'] == 0
                if not native:
                    assert stage['cached'] == stage['block_prediction']
                elif stage['prediction_difference']:
                    prediction_differences.append(
                        {'key': key, 'replica': i, 'turn': turn, 'difference': stage['prediction_difference']})
                if native and turn == 2:
                    tools = [m for m in q['messages'] if m['role'] == 'tool'];
                    assert len(tools) == 1 and tools[0]['content'] == calls[i - 1]['returned_text'];
                    assert q['messages'][0]['content'].startswith(read(R / 'datasets/shared-history.json')['text'])
                if native and turn == 1: assert q['tool_choice'] == {'type': 'function',
                                                                     'function': {'name': 'search_documents'}}
                if not native:
                    text = q['messages'][0]['content'];
                    assert w['rendered_grounding'] in text and w['grounding_before_shared_history'] and calls[i - 1][
                        'finished_ns'] <= w['assembly_ns'] <= w['model_started_ns'];
                    prefixes.append(text)
                    if d['mode'] == 'static-deploy-variance':
                        rawreturn = calls[i - 1]['returned_text'];
                        ending = '[Passage ID: ' + calls[i - 1]['top_k_ids'][0] + ']';
                        at = rawreturn.index(ending) + len(ending);
                        assert w['rendered_grounding'] == rawreturn[
                            :at] + f'\n[deployment-render replica={i}]' + rawreturn[at:]
                    else:
                        assert w['rendered_grounding'] == calls[i - 1]['returned_text']
                counts['model_requests'] += 1;
                counts['completion_tokens'] += u['completion_tokens']
                if native and turn == 2 and s['choices'][0]['finish_reason'] == 'length': counts[
                    'native_answer_completion_failures'] += 1
            if i > 1 and not native: assert w['prefix_bytes_equal'] and w['tool_boundary_divergence_verified']
        assert len({digest(c['top_k_ids']) for c in calls}) == 1 and len({c['embedding_sha256'] for c in calls}) == 1
        if d['mode'] in ['benign', 'static-neutral', 'det-tool-neutral', 'functiontool-benign',
                         'static-deploy-variance']:
            assert d['unique_tool_return_bytes'] == 1
        else:
            assert d['unique_tool_return_bytes'] == d['N']
        d['workers'] = [{k: v for k, v in w.items() if k != 'rendered_grounding'} for w in d['workers']];
        results.append(d);
        counts['rounds'] += 1;
        counts['retrieval_calls'] += d['N'];
        counts['native_rounds' if native else 'pretrieval_rounds'] += 1
    assert not expected and counts['rounds'] == 960 and counts['model_requests'] == 15000
    diagnostics = []
    for folder in ['interrupted', 'superseded-required']:
        for p in (R / 'data' / folder).glob('*/*raw.json'):
            raw = read(p);
            response = raw['response'];
            diagnostics.append({'path': str(p), 'sha256': digest(p.read_bytes()),
                                'successful_model_request': bool(response.get('usage')), 'model': response.get('model'),
                                'request_tool_choice': raw['request'].get('tool_choice'),
                                'finish_reason': (response.get('choices') or [{}])[0].get('finish_reason'),
                                'error': response.get('error'), 'category': folder})
    for rec in read(R / 'datasets/formal-query-provenance.json'): assert digest(Path(rec['path']).read_bytes()) == rec[
        'sha256']
    idx = read(R / 'evidence/index-build.json')
    for name, h in idx['index_sha256'].items(): assert digest(
        (R / 'datasets/smoke-index' / f'{name}.faiss').read_bytes()) == h
    h = read(R / 'datasets/shared-history.json');
    assert digest(Path(h['source_path']).read_bytes()) == h['source_sha256']
    audit = {'passed': True, 'counts': dict(counts), 'cold_rounds_verified': 960, 'unique_salts': len(salts),
             'index_unchanged': True, 'query_source_hashes_unchanged': True, 'protocol_and_frozen_code_unchanged': True,
             'diagnostic_model_requests_excluded': sum(d['successful_model_request'] for d in diagnostics),
             'diagnostic_http_errors': sum(not d['successful_model_request'] for d in diagnostics),
             'native_prediction_differences': prediction_differences,
             'limits': 'native prediction uses observed input+generated tokens and is diagnostic; actual engine cache counters remain primary'}
    save(R / 'evidence/formal-audit.json', audit);
    save(R / 'evidence/diagnostic-requests.json', diagnostics);
    save(R / 'data/compact-formal.json', {'results': results, 'audit': audit});
    print(json.dumps(audit, ensure_ascii=False)[:2500])


if __name__ == '__main__': main()

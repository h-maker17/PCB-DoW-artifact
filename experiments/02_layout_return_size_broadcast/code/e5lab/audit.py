from .common import *


def check(phase):
    results = [];
    salts = set();
    requests = 0;
    block_errors = []
    for p in sorted((ROOT / 'data' / phase).glob('*/result.json')):
        result = read(p);
        d = p.parent;
        n = result['N'];
        assert result['cache_salt'] not in salts;
        salts.add(result['cache_salt'])
        calls = read(d / 'tool-calls.json');
        assert len(calls) == result['tool_calls'] == (1 if result['mode'] == 'broadcast' else n)
        assert [c['invocation'] for c in calls] == list(range(1, len(calls) + 1)) and all(
            c['arguments'] == {} for c in calls)
        b = read(ROOT / 'datasets/blocks.json');
        s = b['S'];
        g = b['G'][str(result['g_tokens'])]['text']
        for worker in range(1, n + 1):
            stem = f'{worker:04d}-worker_{worker}';
            req = read(d / (stem + '.request.json'));
            res = read(d / (stem + '.response.json'));
            tk = read(d / (stem + '.tokenize.json'))
            assert res['prompt_token_ids'] == tk['tokens'];
            assert req['cache_salt'] == result['cache_salt'];
            assert req['seed'] == 42 and req['temperature'] == 0 and req['top_p'] == 1 and req['max_tokens'] == 16 and \
                   req['stream'] is False and not req.get('tools')
            output = calls[worker - 1]['returned_text'] if result['mode'] != 'broadcast' else calls[0]['returned_text']
            assert output == '\u200b' * (worker if result['mode'] == 'nonce-tool' else 1) + '\n' + g
            assert output in req['messages'][1]['content'] and s in req['messages'][1]['content']
            assert digest(output) == result['workers'][worker - 1]['tool_return_sha256']
            if worker == 1: assert res['usage']['prompt_tokens_details']['cached_tokens'] == 0
            assert res['choices'][0]['finish_reason'] == 'stop'
            block_errors.append(result['workers'][worker - 1]['engine_minus_block_prediction']);
            requests += 1
        if result['mode'] == 'const-tool': assert result['reusing_hit_rate'] > .95
        results.append(result)
    for r in results:
        if r['mode'] == 'broadcast':
            baseline = next(x for x in results if
                            (x['g_tokens'], x['N'], x['repeat'], x['layout'], x['mode']) == (r['g_tokens'], r['N'],
                                                                                             r['repeat'], r['layout'],
                                                                                             'const-tool'))
            assert r['prompt_tokens'] == baseline['prompt_tokens'] and r['cached_tokens'] == baseline['cached_tokens']
        if r['mode'] == 'nonce-tool' and r['layout'] == 'append':
            d = ROOT / 'data' / phase / r['name'];
            base = d.parent / r['name'].replace('nonce-tool', 'const-tool')
            a = read(d / '0002-worker_2.response.json')['prompt_token_ids'];
            b = read(base / '0002-worker_2.response.json')['prompt_token_ids']
            assert lcp(a, b) > 2000, 'Shared S token prefix not retained'
    return {'passed': True, 'runs': len(results), 'model_requests': requests, 'unique_salts': len(salts),
            'tool_calls': sum(x['tool_calls'] for x in results),
            'block_alignment_max_abs_error': max(map(abs, block_errors), default=0),
            'broadcast_exactly_matches_benign_usage': True, 'append_S_token_prefix_matched': True,
            'completed_utc': now()}


def audit_sanity():
    result = check('sanity');
    assert result['runs'] == 18 and result['model_requests'] == 90
    save(ROOT / 'evidence/sanity-audit.json', result);
    print('SANITY_PASSED', result, flush=True)


def audit_formal():
    for item in read(ROOT / 'evidence/formal-start-files.json'):
        assert hashlib.sha256((ROOT / item['path']).read_bytes()).hexdigest() == item['sha256'], item['path']
    result = check('formal');
    assert result['runs'] == 180 and result['model_requests'] == 2250
    save(ROOT / 'evidence/final-audit.json', result);
    return result


if __name__ == '__main__': print(audit_formal())

from common import *
from collections import Counter


def main():
    results = [];
    counts = Counter();
    seen = set();
    mismatches = []
    for p in sorted((R / 'data/formal').glob('*/result.json')):
        d = read(p);
        raw = [json.loads(x) for x in (p.parent / 'requests-responses.jsonl').read_text(encoding='utf-8').splitlines()];
        assert len(raw) == d['N'] and d['cold_cached'] == 0
        assert len({x['request']['cache_salt'] for x in raw}) == 1;
        key = raw[0]['request']['cache_salt'];
        assert key not in seen;
        seen.add(key)
        for row, w in zip(raw, d['workers']):
            req = row['request'];
            res = row['response'];
            ids = res['prompt_token_ids'];
            u = res['usage'];
            assert digest(ids) == w['input_sha256'] and u['prompt_tokens'] == len(ids) == w['prompt'] and \
                   u['prompt_tokens_details']['cached_tokens'] == w['cached'];
            assert req['temperature'] == 0 and req['seed'] == 42 and req['max_tokens'] == 16
            counts['formal_round_requests'] += 1
            if w['cached'] != w['block_prediction']: mismatches.append(
                {'key': d['key'], 'cached': w['cached'], 'prediction': w['block_prediction']})
        if d['phase'] == 'H1' and d['marker'] == 'zwsp' and not d['normalized']:
            plan = read(B / 'B_cross_engine/datasets/replay-plan.json');
            s = next(x for x in plan['runs'] if
                     x['phase'] == 'B3' and x['g_tokens'] == 2000 and x['N'] == 5 and x['layout'] == 'prepend' and x[
                         'mode'] == 'nonce-tool' and x['repeat'] == d['repeat']);
            assert [x['input_sha256'] for x in d['workers']] == [x['input_sha256'] for x in s['workers']]
        for s in d.get('sources', []):
            assert hashlib.sha256(Path(s['source_request']).read_bytes()).hexdigest() == s[
                'source_request_sha256'] and hashlib.sha256(Path(s['source_response']).read_bytes()).hexdigest() == s[
                       'source_response_sha256']
        compact = {k: v for k, v in d.items() if k != 'tool_returns'}
        if 'tool_returns' in d: compact['normalization_token_change'] = [
            {'replica': x['replica'], 'before_tokens': len(x['before_tokens']), 'after_tokens': len(x['after_tokens']),
             'byte_changed': x['before'] != x['after']} for x in d['tool_returns']]
        results.append(compact);
        counts[d['phase'] + '_runs'] += 1
    for p in sorted((R / 'data/token-mass').glob('*/result.json')):
        d = read(p);
        raw = [json.loads(x) for x in (p.parent / 'requests-responses.jsonl').read_text(encoding='utf-8').splitlines()];
        assert len(raw) == 3 and d['cached'][0] == 0
        salt = raw[0]['request']['cache_salt'];
        assert salt not in seen;
        seen.add(salt)
        for row in raw:
            assert row['request']['cache_salt'] == salt and row['request']['temperature'] == 0 and row['request'][
                'seed'] == 42
            ids = row['response']['prompt_token_ids'];
            assert len(ids) == row['response']['usage']['prompt_tokens'];
            assert row['tool_return']['tool_call_id'] == row['response']['choices'][0]['message']['tool_calls'][0]['id']
            counts['token_mass_turn_requests'] += 1
        counts['token_mass_sessions'] += 1
    ds = read(R / 'datasets/H2-dataset.json');
    groups = {s: {x['group'] for x in ds if x['split'] == s} for s in ['train', 'calibration', 'test']};
    assert not (groups['train'] & groups['calibration'] or groups['train'] & groups['test'] or groups['calibration'] &
                groups['test'])
    for d in ds:
        if d.get('source') == 'archived-vLLM':
            for w in d['workers']: assert hashlib.sha256(Path(w['response_path']).read_bytes()).hexdigest() == w[
                'source_sha256']
    freeze = hashlib.sha256((R / 'config/frozen-detector.json').read_bytes()).hexdigest();
    assert freeze == read(R / 'evidence/stage2-process.json')['detector_sha256_before_evasion']
    counts['cold_valid_rounds'] = len(results);
    counts['unique_round_and_session_salts'] = len(seen);
    counts['new_model_requests'] = counts['formal_round_requests'] + counts['token_mass_turn_requests']
    audit = {'passed': True, 'counts': dict(counts), 'block_lcp_differences': mismatches, 'group_disjoint': True,
             'frozen_detector_unchanged': True, 'old_sources_hashes_unchanged': True}
    save(R / 'data/compact-results.json', {'results': results, 'audit': audit});
    save(R / 'evidence/formal-audit.json', audit);
    print(json.dumps(audit, ensure_ascii=False)[:2000])


if __name__ == '__main__': main()

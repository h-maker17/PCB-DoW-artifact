"""Audit raw responses and export compact results; no model or source mutation."""
from pathlib import Path
import json, hashlib, collections

R = Path(__file__).resolve().parents[1]


def read(p): return json.loads(p.read_text(encoding='utf-8'))


plan = {r['key']: r for r in read(R / 'datasets/replay-plan.json')['runs']}
results = [];
checks = collections.Counter();
issues = [];
seen_keys = set()
for result in sorted((R / 'data/formal').glob('*/*/result.json')):
    d = read(result);
    src = plan[d['key']];
    raw = [json.loads(line) for line in
           (result.parent / 'native-responses.jsonl').read_text(encoding='utf-8').splitlines()]
    assert len(raw) == src['N'] == len(d['workers'])
    assert d['attempt_valid'] and d['cold_replica_cached'] == 0
    assert len({e['request']['extra_key'] for e in raw}) == 1
    key = raw[0]['request']['extra_key'];
    assert key not in seen_keys;
    seen_keys.add(key)
    for archived, entry, w in zip(src['workers'], raw, d['workers']):
        ids = entry['request']['input_ids'];
        meta = entry['response']['meta_info'];
        assert ids == archived['input_ids']
        digest = hashlib.sha256(json.dumps(ids, separators=(',', ':')).encode()).hexdigest()
        assert digest == archived['input_sha256'] == w['input_sha256'] == entry['input_sha256']
        assert meta['prompt_tokens'] == len(ids) == w['prompt_tokens'] and meta['cached_tokens'] == w['cached_tokens']
        details = meta.get('cached_tokens_details')
        if details is not None: assert details.get('device', 0) + details.get('host', 0) == meta['cached_tokens']
        assert meta.get('total_retractions',
                        0) == 0, 'scheduler retraction requires explicit review before parity aggregation'
        assert entry['request']['sampling_params']['sampling_seed'] == 42 and entry['request']['sampling_params'][
            'temperature'] == 0
        assert entry['request']['sampling_params']['max_new_tokens'] == archived['sampling']['max_tokens']
        checks['token_exact_requests'] += 1
        checks[f'page{d["page_size"]}_token_exact_requests'] += 1
        if w['cached_minus_lcp'] != 0: issues.append({'cell': d['key'], 'page': d['page_size'], 'worker': w['worker'],
                                                      'cached_minus_lcp': w['cached_minus_lcp']})
    assert raw[0]['response']['meta_info']['cached_tokens'] == 0
    checks['cold_valid_runs'] += 1
    checks['unique_extra_keys'] = len(seen_keys)
    d['data_directory'] = str(result.parent);
    results.append(d)
payload = {'results': results, 'audit': dict(checks), 'cached_vs_exact_lcp_differences': issues,
           'preregistration': read(R / 'config/preregistration.json')}
(R / 'data/compact-results.json').write_text(json.dumps(payload, ensure_ascii=False, separators=(',', ':')),
                                             encoding='utf-8')
(R / 'evidence/formal-audit.json').write_text(json.dumps(
    {'audit': dict(checks), 'cached_vs_exact_lcp_differences': issues,
     'formal_source_input_hash': read(R / 'datasets/replay-manifest.json')['plan_sha256']}, indent=2))
print('AUDIT', dict(checks), 'cache-vs-lcp differences', len(issues), 'result_runs', len(results))

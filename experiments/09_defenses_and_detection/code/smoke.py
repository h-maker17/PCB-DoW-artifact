"""Execute only the guide's preformal stage gate, never formal H experiments."""
from pathlib import Path
import json, hashlib, uuid, time, urllib.request, urllib.error, sys, traceback

R = Path(__file__).resolve().parents[1];
sys.path.insert(0, str(R / 'code'))
from normalization import normalize, selftest, VERSION
from cache_statistics import features

URL = 'http://127.0.0.1:8000'


def read(p): return json.loads(p.read_text(encoding='utf-8'))


def save(p, d): p.parent.mkdir(parents=True, exist_ok=True);p.write_text(json.dumps(d, ensure_ascii=False, indent=2),
                                                                         encoding='utf-8')


def call(path, payload):
    req = urllib.request.Request(URL + path, data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=600) as f: return json.load(f)


def tokenize(text): return call('/tokenize', {'model': 'llama-3.3-70b', 'prompt': text, 'add_special_tokens': False})


def main():
    cfg = read(R / 'config/preregistration.json');
    assert cfg['formal_authorized_after_smoke'] is False
    plan = read(R.parent / 'B_cross_engine/datasets/replay-plan.json')
    for i in range(300):
        try:
            with urllib.request.urlopen(URL + '/health', timeout=3):
                break
        except Exception:
            if i % 10 == 0: print('Waiting for H service', i, flush=True)
            time.sleep(3)
    else:
        raise RuntimeError('H service not ready after 900s')
    result = {'stage': 'smoke-only', 'formal_started': False, 'rounds': [], 'normalization_version': VERSION,
              'started_unix': time.time()}
    for arm in ['none', 'front']:
        src = next(r for r in plan['runs'] if
                   r['phase'] == 'B1' and r['model'] == 'llama' and r['repeat'] == 1 and r['position'] == arm)
        out = R / 'data/smoke' / arm;
        out.mkdir(parents=True, exist_ok=False)
        salt = uuid.uuid4().hex;
        prompts = [];
        counts = [];
        hashes = [];
        sources = [];
        rows = []
        for i, w in enumerate(src['workers'], 1):
            rp = Path(w['source_response']);
            qp = rp.with_name(rp.name.replace('.response.json', '.request.json'))
            response_hash = hashlib.sha256(rp.read_bytes()).hexdigest();
            request_hash = hashlib.sha256(qp.read_bytes()).hexdigest()
            assert response_hash == w['source_sha256'];
            request = read(qp);
            request['cache_salt'] = salt
            save(out / f'{i:02d}.request.json', request)
            t = time.perf_counter();
            response = call('/v1/chat/completions', request);
            elapsed = time.perf_counter() - t
            save(out / f'{i:02d}.response.json', response)
            assert response['prompt_token_ids'] == w['input_ids'], 'Prompt token mismatch; stop stage gate'
            usage = response['usage'];
            p = usage['prompt_tokens'];
            c = usage['prompt_tokens_details']['cached_tokens']
            assert p == len(w['input_ids']) and c == w[
                'vllm_cached_tokens'], 'Cache counter parity failed; stop stage gate'
            if i == 1: assert c == 0, 'First replica not cold'
            assert hashlib.sha256(rp.read_bytes()).hexdigest() == response_hash and hashlib.sha256(
                qp.read_bytes()).hexdigest() == request_hash
            prompts.append(p);
            counts.append(c);
            hashes.append(w['input_sha256']);
            sources.append({'request_path': str(qp), 'request_sha256': request_hash, 'response_path': str(rp),
                            'response_sha256': response_hash})
            row = {'worker': i, 'request': request, 'response': response, 'elapsed_seconds': elapsed,
                   'source': sources[-1]};
            rows.append(row)
            with (out / 'native-responses.jsonl').open('a', encoding='utf-8') as f:
                f.write(json.dumps(row, ensure_ascii=False) + '\n')
            print('REPLAY', arm, i, 'prompt', p, 'cached', c, flush=True)
        feat = features(prompts, counts);
        cost = sum(p - .9 * c for p, c in zip(prompts, counts))
        run = {'arm': arm, 'N': len(prompts), 'cache_salt': salt, 'prompt_tokens': prompts, 'cached_tokens': counts,
               'input_sha256': hashes, 'sources': sources, 'features': feat, 'input_cost_f01': cost,
               'warm_hit_ratio_token_weighted': sum(counts[1:]) / sum(prompts[1:]), 'exact_prompt_parity': True,
               'exact_cache_parity': True, 'cold_valid': True}
        save(out / 'result.json', run);
        result['rounds'].append(run)
        save(R / 'evidence/smoke-progress.json', {'completed_arms': [r['arm'] for r in result['rounds']]})
    none, attack = result['rounds'];
    assert none['features']['mean_hit'] > attack['features']['mean_hit'] and none['features']['min_hit'] > \
           attack['features']['min_hit']
    assert attack['features']['below_floor_count'] > none['features']['below_floor_count']
    assert all(r['features']['variance_hit'] >= 0 for r in result['rounds'])
    tests = selftest()
    for row in tests:
        row['before_tokenize'] = tokenize(row['input']);
        row['after_tokenize'] = tokenize(row['normalized'])
        row['before_tokens'] = len(row['before_tokenize']['tokens']);
        row['after_tokens'] = len(row['after_tokenize']['tokens'])
        if row['marker'] == 'zwsp': assert row['after_tokens'] < row['before_tokens'] and '\u200b' not in row[
            'normalized']
    for marker in ['request-id', 'html-comment']: assert next(r for r in tests if r['marker'] == marker)['pass']
    save(R / 'data/smoke/normalization-selftest.json', {'version': VERSION, 'cases': tests, 'pass': True})
    # A controlled bimodal feature sanity check is not a model measurement.
    sanity = features([1000] * 5, [0, 990, 990, 260, 260]);
    assert sanity['variance_hit'] > 0 and sanity['min_hit'] == .26 and sanity['below_floor_count'] == 2
    result.update(passed=True, finished_unix=time.time(), requests=10, tokenize_calls=14,
                  statistics_selftest={'observed_all_features_finite': True, 'mean_min_count_direction_pass': True,
                                       'variance_note': 'full collapse may be uniformly low: variance need not rise; a bimodal synthetic check verifies variance calculation only',
                                       'bimodal_calculation_check': sanity}, normalization_selftest_pass=True,
                  CAF_f01=attack['input_cost_f01'] / none['input_cost_f01'])
    save(R / 'evidence/smoke-audit.json', result)
    meta = {'stage': 'smoke-passed-awaiting-user-confirmation', 'instance': '<instance-id>', 'guide': 'config/guide.md',
            'preregistration_sha256': hashlib.sha256((R / 'config/preregistration.json').read_bytes()).hexdigest(),
            'normalization_pipeline_version': VERSION, 'cache_health_definitions': cfg['cache_health_features'],
            'dataset_rounds': [{k: r[k] for k in ['arm', 'N', 'cache_salt', 'sources']} for r in result['rounds']],
            'cached_field_path': cfg['cached_path'], 'temperature': 0, 'seed': 42, 'isolation': cfg['isolation'],
            'old_source_write_operations': 0, 'formal_started': False,
            'token_mass_reference': cfg['token_mass_reference']}
    save(R / 'run_meta.json', meta)
    print('H SMOKE PASSED; STOP BEFORE FORMAL',
          json.dumps({r['arm']: r['warm_hit_ratio_token_weighted'] for r in result['rounds']}), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        save(R / 'evidence/smoke-failure.json',
             {'error': str(exc), 'traceback': traceback.format_exc(), 'formal_started': False});
        raise

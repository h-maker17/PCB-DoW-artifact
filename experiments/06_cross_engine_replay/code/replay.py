"""Run one model/phase, or --key a single cell; preserve original decode caps."""
from pathlib import Path
import argparse, json, urllib.request, uuid, hashlib, time, os

R = Path(__file__).resolve().parents[1]


def read(p): return json.loads(p.read_text(encoding='utf-8'))


def save(p, x): p.parent.mkdir(parents=True, exist_ok=True);p.write_text(json.dumps(x, ensure_ascii=False, indent=2),
                                                                         encoding='utf-8')


def call(path, payload=None):
    req = urllib.request.Request('http://127.0.0.1:30000' + path,
                                 data=None if payload is None else json.dumps(payload, separators=(',', ':')).encode(),
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=600) as f: return f.read().decode()


def digest(ids): return hashlib.sha256(json.dumps(ids, separators=(',', ':')).encode()).hexdigest()


def run_cell(run, page=1, tag=None):
    label = tag or f'page{page}'
    out = R / ('data/reruns' if tag else 'data/formal') / label / run['key'];
    out.mkdir(parents=True, exist_ok=True)
    if (out / 'result.json').exists(): return read(out / 'result.json')
    assert not (
                out / 'native-responses.jsonl').exists(), 'partial cell requires new attempt directory, not silent append'
    key = uuid.uuid4().hex
    flush = call('/flush_cache');
    (out / 'flush.txt').write_text(flush)
    state = {k: v for k, v in run.items() if k != 'workers'};
    state.update(page_size=page, extra_key=key, backend='triton', sampling_seed=42, attempt_valid=False)
    save(out / 'manifest.json', state);
    workers = []
    for i, w in enumerate(run['workers'], 1):
        archived = read(Path(w['source_response']));
        ids = w['input_ids']
        assert archived['prompt_token_ids'] == ids and digest(ids) == w['input_sha256']
        assert hashlib.sha256(Path(w['source_response']).read_bytes()).hexdigest() == w['source_sha256']
        sp = {'temperature': w['sampling'].get('temperature', 0), 'sampling_seed': w['sampling'].get('seed', 42),
              'max_new_tokens': w['sampling']['max_tokens']}
        for param in ['top_p', 'top_k']:
            if param in w['sampling']: sp[param] = w['sampling'][param]
        assert sp['temperature'] == 0 and sp['sampling_seed'] == 42
        payload = {'input_ids': ids, 'sampling_params': sp, 'extra_key': key}
        save(out / f'{i:04d}.request.json', payload)
        raw = call('/generate', payload);
        (out / f'{i:04d}.response.json').write_text(raw)
        response = json.loads(raw);
        m = response['meta_info'];
        p = m['prompt_tokens'];
        c = m['cached_tokens']
        record = {'worker': i, 'input_sha256': digest(ids), 'source_response': w['source_response'],
                  'source_sha256': w['source_sha256'], 'request': payload, 'response': response}
        with (out / 'native-responses.jsonl').open('a', encoding='utf-8') as f:
            f.write(json.dumps(record, ensure_ascii=False, separators=(',', ':')) + '\n');f.flush();os.fsync(f.fileno())
        assert p == len(ids) and 0 <= c <= p and isinstance(c, int)
        if i == 1 and c != 0:
            save(out / 'invalid.json', {'reason': 'cold replica is not cold', 'cached': c});
            raise RuntimeError('cache isolation failed; discard attempt and rerun')
        workers.append({'worker': i, 'prompt_tokens': p, 'cached_tokens': c, 'uncached_tokens': p - c,
                        'completion_tokens': m['completion_tokens'], 'vllm_cached_tokens': w['vllm_cached_tokens'],
                        'vllm_prompt_tokens': w['vllm_prompt_tokens'], 'lcp_prediction': w['prompt_lcp_prediction'],
                        'input_sha256': digest(ids), 'cached_minus_lcp': c - w['prompt_lcp_prediction']})
        save(R / 'evidence/formal-progress.json',
             {'phase': run['phase'], 'model': run['model'], 'page_size': page, 'cell': run['key'], 'worker': i,
              'N': run['N'], 'updated_unix': time.time()})
    result = dict(state, workers=workers, attempt_valid=True, cold_replica_cached=0, completed_unix=time.time(),
                  token_exact_verified=True)
    for prefix in ['sglang', 'vllm']:
        p = sum(w['prompt_tokens'] for w in workers);
        c = sum(w['cached_tokens'] if prefix == 'sglang' else w['vllm_cached_tokens'] for w in workers)
        part = workers[1:];
        cp = sum(w['cached_tokens'] if prefix == 'sglang' else w['vllm_cached_tokens'] for w in part)
        result[prefix] = {'prompt_tokens': p, 'cached_tokens': c, 'uncached_tokens': p - c, 'aggregate_hit_rate': c / p,
                          'reusing_hit_rate': cp / sum(w['prompt_tokens'] for w in part), 'reusing_replica_mean': sum(
                (w['cached_tokens'] if prefix == 'sglang' else w['vllm_cached_tokens']) / w['prompt_tokens'] for w in
                part) / len(part), 'costs': {str(f): (p - c) + f * c for f in [.1, .25, .5]}}
    save(out / 'result.json', result)
    print('DONE', label, run['key'], 'hit', round(result['sglang']['reusing_hit_rate'], 6), flush=True)
    return result


def main():
    ap = argparse.ArgumentParser();
    ap.add_argument('--phase', choices=['B1', 'B2', 'B3']);
    ap.add_argument('--model', choices=['qwen', 'llama']);
    ap.add_argument('--key');
    ap.add_argument('--page', type=int, default=1);
    ap.add_argument('--small-g-only', action='store_true');
    ap.add_argument('--tag');
    args = ap.parse_args()
    runs = read(R / 'datasets/replay-plan.json')['runs']
    runs = [r for r in runs if
            (not args.phase or r['phase'] == args.phase) and (not args.model or r['model'] == args.model) and (
                        not args.key or r['key'] == args.key) and (not args.small_g_only or r.get('g_tokens') == 50)]
    assert runs
    for run in sorted(runs, key=lambda r: (r['repeat'], r['key'])): run_cell(run, args.page, args.tag)
    print('PHASE COMPLETE', args.phase, args.model, len(runs), flush=True)


if __name__ == '__main__': main()

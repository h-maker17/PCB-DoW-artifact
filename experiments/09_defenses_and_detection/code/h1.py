from common import *
from normalization import normalize, VERSION
import difflib, ast, unicodedata


def marker(kind, i):
    return {'none': '', 'zwsp': '\u200b' * i + '\n', 'zwj': '\u200d' * i + '\n', 'nbsp': '\u00a0' * i + '\n',
            'whitespace': ' ' * i + '\t\n', 'request-id': f'[request-id:{i:04d}]\n',
            'timestamp': f'[2026-10-07T12:00:{i:02d}Z]\n', 'html-comment': f'<!--nonce:{i:04d}-->\n'}[kind]


def collateral():
    cases = [('python', 'def f():\n    if True:\n        return 1\n'),
             ('yaml', 'parent:\n  child: value\n  list:\n    - one\n    - two\n'), ('persian-zwnj', 'می\u200cروم'),
             ('devanagari-zwj', 'क्\u200dष'), ('emoji-zwj', '👩\u200d👩\u200d👧\u200d👦'),
             ('nbsp-typography', '10\u00a0kg'), ('ordinary-prose', 'The report contains two facts.'),
             ('nonsemantic-whitespace', 'The   report\tcontains two facts.')]
    rows = []
    for kind, text in cases:
        normalized = normalize(text);
        before = tokenize(text);
        after = tokenize(normalized);
        matched = sum(b.size for b in difflib.SequenceMatcher(a=before, b=after, autojunk=False).get_matching_blocks())
        r = {'fixture': kind, 'original': text, 'normalized': normalized, 'before_tokens': before,
             'after_tokens': after, 'original_token_changed_fraction': 1 - matched / len(before),
             'changed': text != normalized}
        if kind == 'python':
            try:
                ast.parse(normalized);r['structural_oracle'] = 'parseable'
            except SyntaxError:
                r['structural_oracle'] = 'Python syntax broken'
        elif kind == 'yaml':
            try:
                import yaml
                r['structural_oracle'] = 'same parsed data' if yaml.safe_load(text) == yaml.safe_load(
                    normalized) else 'YAML data changed'
            except ModuleNotFoundError:
                r['structural_oracle'] = 'YAML parser unavailable; parse oracle not yet evaluated'
            except Exception as e:
                r['structural_oracle'] = 'YAML parse failed: ' + str(e)
        else:
            r['structural_oracle'] = 'token/byte alteration only; no automatic semantic equivalence claim'
        rows.append(r)
    save(R / 'data/ancillary/h1-collateral.json', rows)


def main():
    plan = read(B / 'B_cross_engine/datasets/replay-plan.json');
    timings = [];
    runs = []
    for rep in range(1, 6):
        src = next(r for r in plan['runs'] if
                   r['phase'] == 'B3' and r['g_tokens'] == 2000 and r['N'] == 5 and r['layout'] == 'prepend' and r[
                       'mode'] == 'nonce-tool' and r['repeat'] == rep)
        root = Path(src['source_run']);
        tool = read(root / 'tool-calls.json')
        for kind in ['none', 'zwsp', 'zwj', 'nbsp', 'whitespace', 'request-id', 'timestamp', 'html-comment']:
            for on in [False, True]:
                requests = [];
                returns = [];
                provenance = []
                for i, (w, t) in enumerate(zip(src['workers'], tool), 1):
                    rp = Path(w['source_response']);
                    qp = rp.with_name(rp.name.replace('.response.json', '.request.json'));
                    req = read(qp);
                    original = t['returned_text'];
                    assert original.startswith('\u200b' * i + '\n');
                    grounding = original[i + 1:];
                    ret = marker(kind, i) + grounding
                    before = ret
                    if on:
                        for _ in range(100):
                            start = time.perf_counter_ns();
                            ret = normalize(before);
                            timings.append({'marker': kind, 'ns': time.perf_counter_ns() - start, 'chars': len(before)})
                    user = req['messages'][1]['content'];
                    assert user.count(original) == 1;
                    req['messages'][1]['content'] = user.replace(original, ret, 1);
                    req.pop('cache_salt', None)
                    requests.append(req);
                    returns.append(
                        {'replica': i, 'before': before, 'after': ret, 'source_tool_return_sha256': digest(original),
                         'before_tokens': tokenize(before), 'after_tokens': tokenize(ret)})
                    provenance.append({'source_request': str(qp),
                                       'source_request_sha256': hashlib.sha256(qp.read_bytes()).hexdigest(),
                                       'source_response': str(rp), 'source_response_sha256': w['source_sha256']})
                key = f'H1-r{rep}-{kind}-' + ('ON' if on else 'OFF');
                run = run_round(key, requests,
                                {'phase': 'H1', 'model': 'llama', 'marker': kind, 'normalized': on, 'repeat': rep,
                                 'tool_returns': returns, 'sources': provenance,
                                 'source_group': 'E5-GovReport-first-doc', 'normalization_version': VERSION})
                if kind == 'zwsp' and not on:
                    assert run['prompt'] == [w['vllm_prompt_tokens'] for w in src['workers']] and run['cached'] == [
                        w['vllm_cached_tokens'] for w in src['workers']]
                runs.append(run)
    save(R / 'data/ancillary/h1-cpu-timing.json', timings);
    collateral();
    save(R / 'evidence/H1-state.json', {'status': 'complete', 'runs': len(runs), 'requests': sum(r['N'] for r in runs)})
    print('H1 COMPLETE', len(runs), flush=True)


if __name__ == '__main__': main()

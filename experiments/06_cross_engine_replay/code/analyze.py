"""Recompute all reported input-cache costs from audited raw-result exports."""
from pathlib import Path
import json, csv, hashlib, sys, statistics
import numpy as np

R = Path(__file__).resolve().parents[1]


def read(p): return json.loads(p.read_text(encoding='utf-8'))


def write(p, x): p.parent.mkdir(parents=True, exist_ok=True);p.write_text(json.dumps(x, ensure_ascii=False, indent=2),
                                                                          encoding='utf-8')


def stats(values, label):
    a = np.asarray(values, dtype=float);
    assert len(a) > 0
    seed = 42 + int(hashlib.sha256(label.encode()).hexdigest()[:8], 16)
    rng = np.random.default_rng(seed);
    boot = a[rng.integers(0, len(a), size=(10000, len(a)))].mean(axis=1)
    lo, hi = np.quantile(boot, [.025, .975]);
    return {'mean': float(a.mean()), 'lo': float(lo), 'hi': float(hi), 'n': len(a)}


def metric(row, name, values):
    s = stats(values, str(sorted(row.items())) + name);
    row.update({name + '_' + k: v for k, v in s.items() if k != 'n'})


def csvout(name, rows):
    (R / 'tables').mkdir(exist_ok=True)
    fields = list(dict.fromkeys(k for r in rows for k in r))
    with (R / 'tables' / name).with_suffix('.csv').open('w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fields);
        w.writeheader();
        w.writerows(rows)


def latexout(name, rows, fields, caption, label, footnote=''):
    def esc(s):
        return str(s).replace('_', r'\_').replace('%', r'\%').replace('&', r'\&')

    headers = {'model': 'Model', 'position': 'Position', 'engine': 'Engine', 'independent_repeats': r'$n$',
               'reusing_hit_rate_mean': r'Hit (\%)', 'uncached_tokens_mean': 'Uncached tok.', 'N': r'$N$', 'f': r'$f$',
               'sglang_CAF_mean': 'CAF (SG)', 'vllm_CAF_mean': 'CAF (vLLM)',
               'CAF_relative_parity_error': r'Parity err. (\%)', 'G': r'$|G|$', 'layout': 'Layout', 'arm': 'Arm',
               'sglang_hit_mean': r'Hit (\%)', 'sglang_CAF_f0.1_mean': 'CAF (SG)', 'vllm_CAF_f0.1_mean': 'CAF (vLLM)',
               'sglang_loss_mean': 'Lost tok.', 'granularity': 'Page/block', 'loss_mean': 'Lost tok.',
               'loss_prediction': 'Pred. tok.', 'loss_approx_relative_error': r'Loss err. (\%)',
               'CAF_actual_mean': 'CAF obs.', 'CAF_full_model_mean': 'CAF model',
               'CAF_full_model_relative_error': r'CAF err. (\%)', 'fixed_floor': r'Floor (\%)',
               'none_hit_mean': r'None (\%)', 'front_hit_mean': r'Front (\%)', 'CAF_N20_f01_mean': r'CAF$_{20,.1}$'}
    pct = {'reusing_hit_rate_mean', 'CAF_relative_parity_error', 'sglang_hit_mean', 'loss_approx_relative_error',
           'CAF_full_model_relative_error', 'fixed_floor', 'none_hit_mean', 'front_hit_mean'}

    def fmt(v, k):
        if isinstance(v, float): return f'{100 * v:.3f}' if k in pct else (
            f'{v:.0f}' if k in ['uncached_tokens_mean', 'sglang_loss_mean', 'loss_mean',
                                'loss_prediction'] else f'{v:.4f}')
        return esc(
            {'qwen': 'Qwen3-8B', 'llama': 'Llama70B', 'sglang': 'SGLang', 'const-tool': 'Det.', 'nonce-tool': 'Nonce',
             'broadcast': 'Bcast.', 'marker-at-end': 'Marker end'}.get(str(v), v))

    lines = [r'\begin{table*}[t]', r'\centering', r'\caption{' + caption + r'}', r'\label{' + label + r'}',
             r'\scriptsize', r'\begin{tabular}{' + 'l' * len(fields) + '}', r'\hline',
             ' & '.join(headers.get(x, esc(x)) for x in fields) + r' \\', r'\hline']
    for r in rows: lines.append(' & '.join(fmt(r.get(k, ''), k) for k in fields) + r' \\')
    lines += [r'\hline', r'\end{tabular}']
    if footnote: lines.append(r'\par\smallskip\parbox{0.98\linewidth}{\scriptsize ' + footnote + '}')
    lines += [r'\end{table*}']
    (R / 'tables' / name).with_suffix('.tex').write_text('\n'.join(lines) + '\n', encoding='utf-8')


def main():
    data = read(R / 'data/compact-results.json');
    allr = data['results'];
    runs = [r for r in allr if r['page_size'] == 1]
    assert len(runs) == 300 and data['audit']['token_exact_requests'] >= 3210
    assert all(r['attempt_valid'] and r['cold_replica_cached'] == 0 and r['token_exact_verified'] for r in runs)
    idx = {(r['page_size'], r['key']): r for r in allr}
    pred = read(R / 'config/preregistration.json');
    b1 = [];
    b2 = [];
    b3 = [];
    b4 = [];
    core = [];
    contrasts = [];
    slopes = [];
    perrun = []

    def select(phase, model, **kw):
        return sorted([r for r in runs if
                       r['phase'] == phase and r['model'] == model and all(r.get(k) == v for k, v in kw.items())],
                      key=lambda r: r['repeat'])

    def pair(r):
        return idx[(r['page_size'], r['baseline_key'])]

    def caf(r, e, f):
        return r[e]['costs'][str(f)] / pair(r)[e]['costs'][str(f)]

    def loss(r, e):
        return pair(r)[e]['cached_tokens'] - r[e]['cached_tokens']

    def full_model(r, e, f=.1):
        # Token-defined P,H frozen by the archived input; no engine measurement fit.
        # H is the mean maximal common prefix of attacking warm replicas.
        b = pair(r);
        n = r['N'];
        P = statistics.mean(w['lcp_prediction'] for w in b['workers'][1:]);
        s = b[e]['prompt_tokens'] / n - P
        H = statistics.mean(w['lcp_prediction'] for w in r['workers'][1:]);
        D = P - H
        Cb = (P + s) + (n - 1) * (s + f * P);
        Ca = (P + s) + (n - 1) * (D + s + f * (P - D))
        overhead = r[e]['prompt_tokens'] - b[e]['prompt_tokens']
        return {'P': P, 's': s, 'H': H, 'D': D, 'CAF': Ca / Cb, 'prompt_overhead': overhead,
                'CAF_with_prompt_overhead': (Ca + overhead) / Cb}

    for model in ['qwen', 'llama']:
        for pos in ['none', 'front', 'middle', 'marker-at-end']:
            part = select('B1', model, position=pos);
            assert len(part) == 5
            for e in ['sglang', 'vllm']:
                n = part[0]['reference_n'] if e == 'vllm' else 5
                measured = part if n == 5 else part[:1]
                row = {'model': model, 'position': pos, 'engine': e, 'independent_repeats': n,
                       'reference_n': part[0]['reference_n']}
                for m in ['reusing_hit_rate', 'reusing_replica_mean', 'aggregate_hit_rate', 'uncached_tokens']: metric(
                    row, m, [r[e][m] for r in measured])
                if n == 1:
                    for k in list(row):
                        if k.endswith('_lo') or k.endswith('_hi'): row[k] = ''
                row['vllm_reference_note'] = 'vLLM reference n=1; B replay n=5' if part[0][
                                                                                       'reference_n'] == 1 else 'paired archived runs n=5'
                if e == 'sglang':
                    metric(row, 'hit_parity_difference_pp',
                           [100 * (r['sglang']['reusing_hit_rate'] - r['vllm']['reusing_hit_rate']) for r in part])
                b1.append(row)
        for n in [3, 5, 10, 20]:
            part = select('B2', model, N=n, position='front');
            assert len(part) == 5
            for f in [.1, .25, .5]:
                row = {'model': model, 'N': n, 'f': f, 'repeats': 5, 'parity_tolerance': .05}
                for e in ['sglang', 'vllm']:
                    vals = [caf(r, e, f) for r in part];
                    metric(row, e + '_CAF', vals)
                    metric(row, e + '_uncached_ratio',
                           [r[e]['uncached_tokens'] / pair(r)[e]['uncached_tokens'] for r in part])
                row['CAF_relative_parity_error'] = abs(row['sglang_CAF_mean'] - row['vllm_CAF_mean']) / row[
                    'vllm_CAF_mean']
                row['parity_pass'] = row['CAF_relative_parity_error'] <= .05
                # Joint repeat-level bootstrap of paired engine CAF differences.
                a = np.array([caf(r, 'sglang', f) for r in part]);
                b = np.array([caf(r, 'vllm', f) for r in part]);
                rng = np.random.default_rng(42);
                j = rng.integers(0, 5, (10000, 5));
                err = (a[j].mean(1) - b[j].mean(1)) / b[j].mean(1)
                row.update(relative_parity_signed_ci_lo=float(np.quantile(err, .025)),
                           relative_parity_signed_ci_hi=float(np.quantile(err, .975)))
                b2.append(row)
        for e in ['sglang', 'vllm']:
            row = {'engine': e, 'model': model, 'fixed_floor': pred['floor_prediction'][model],
                   'cache_granularity': 1 if e == 'sglang' else 16}
            for pos in ['none', 'front']:
                part = select('B1', model, position=pos);
                metric(row, pos + '_hit', [r[e]['reusing_replica_mean'] for r in part])
            metric(row, 'CAF_N20_f01', [caf(r, e, .1) for r in select('B2', model, N=20, position='front')]);
            core.append(row)
    for g in [50, 2000, 4000]:
        for n in [5, 20]:
            for layout in ['prepend', 'append']:
                for mode in ['const-tool', 'nonce-tool', 'broadcast']:
                    part = select('B3', 'llama', g_tokens=g, N=n, layout=layout, mode=mode);
                    assert len(part) == 5
                    row = {'G': g, 'S': 2000, 'N': n, 'layout': layout, 'arm': mode, 'repeats': 5}
                    expected = (n - 1) * (g + (2000 if layout == 'prepend' else 0)) if mode == 'nonce-tool' else 0
                    row['loss_prediction'] = expected
                    for e in ['sglang', 'vllm']:
                        metric(row, e + '_hit', [r[e]['reusing_hit_rate'] for r in part]);
                        metric(row, e + '_uncached', [r[e]['uncached_tokens'] for r in part]);
                        metric(row, e + '_loss', [loss(r, e) for r in part])
                        for f in [.1, .25, .5]:
                            metric(row, e + '_CAF_f' + str(f), [caf(r, e, f) for r in part])
                            metric(row, e + '_deltaC_f' + str(f),
                                   [r[e]['costs'][str(f)] - pair(r)[e]['costs'][str(f)] for r in part])
                            metric(row, e + '_deltaC_minus_prompt_overhead_f' + str(f), [
                                r[e]['costs'][str(f)] - pair(r)[e]['costs'][str(f)] - (
                                            r[e]['prompt_tokens'] - pair(r)[e]['prompt_tokens']) for r in part])
                        row[e + '_loss_relative_error'] = abs(
                            row[e + '_loss_mean'] - expected) / expected if expected else ''
                    for r in part:
                        q = {'key': r['key'], 'page': r['page_size'], 'G': g, 'S': 2000, 'N': n, 'layout': layout,
                             'arm': mode, 'repeat': r['repeat'], 'cold_cached': r['cold_replica_cached'],
                             'loss_prediction': expected,
                             'input_overhead_vs_benign': r['sglang']['prompt_tokens'] - pair(r)['sglang'][
                                 'prompt_tokens']}
                        for e in ['sglang', 'vllm']:
                            q[e + '_loss'] = loss(r, e)
                            for f in [.1, .25, .5]: q[e + '_CAF_f' + str(f)] = caf(r, e, f)
                        perrun.append(q)
                    b3.append(row)
    for n in [5, 20]:
        for g in [50, 2000, 4000]:
            pre = select('B3', 'llama', g_tokens=g, N=n, layout='prepend', mode='nonce-tool');
            app = select('B3', 'llama', g_tokens=g, N=n, layout='append', mode='nonce-tool')
            for e in ['sglang', 'vllm']:
                row = {'engine': e, 'G': g, 'N': n, 'S': 2000, 'expected_prepend_minus_append': (n - 1) * 2000}
                metric(row, 'measured_contrast', [loss(p, e) - loss(a, e) for p, a in zip(pre, app)]);
                row['relative_error'] = abs(row['measured_contrast_mean'] - row['expected_prepend_minus_append']) / row[
                    'expected_prepend_minus_append'];
                contrasts.append(row)
        for layout in ['prepend', 'append']:
            small = select('B3', 'llama', g_tokens=2000, N=n, layout=layout, mode='nonce-tool');
            large = select('B3', 'llama', g_tokens=4000, N=n, layout=layout, mode='nonce-tool')
            for e in ['sglang', 'vllm']:
                row = {'engine': e, 'N': n, 'layout': layout, 'expected_slope': n - 1};
                metric(row, 'measured_slope', [(loss(b, e) - loss(a, e)) / 2000 for a, b in zip(small, large)]);
                slopes.append(row)
    for page, e in [(1, 'vllm'), (1, 'sglang'), (16, 'sglang')]:
        for n in [5, 20]:
            part = sorted([r for r in allr if
                           r['page_size'] == page and r['phase'] == 'B3' and r.get('g_tokens') == 50 and r['N'] == n and
                           r['layout'] == 'append' and r['mode'] == 'nonce-tool'], key=lambda r: r['repeat'])
            if not part: continue
            assert len(part) == 5
            row = {'engine': e, 'granularity': 16 if e == 'vllm' else page, 'G': 50, 'N': n, 'layout': 'append',
                   'repeats': 5, 'loss_prediction': (n - 1) * 50}
            metric(row, 'loss', [loss(r, e) for r in part]);
            row['loss_approx_relative_error'] = abs(row['loss_mean'] - row['loss_prediction']) / row['loss_prediction']
            metric(row, 'CAF_actual', [caf(r, e, .1) for r in part]);
            metric(row, 'CAF_full_model', [full_model(r, e)['CAF'] for r in part]);
            row['CAF_full_model_relative_error'] = abs(row['CAF_actual_mean'] - row['CAF_full_model_mean']) / row[
                'CAF_full_model_mean']
            for k in ['P', 's', 'H', 'D']: row[k] = statistics.mean(full_model(r, e)[k] for r in part)
            row['input_prompt_overhead_vs_benign'] = statistics.mean(full_model(r, e)['prompt_overhead'] for r in part)
            metric(row, 'CAF_model_with_prompt_overhead', [full_model(r, e)['CAF_with_prompt_overhead'] for r in part]);
            row['CAF_model_with_prompt_overhead_relative_error'] = abs(
                row['CAF_actual_mean'] - row['CAF_model_with_prompt_overhead_mean']) / row[
                                                                       'CAF_model_with_prompt_overhead_mean']
            b4.append(row)
    csvout('B-I', b1);
    csvout('B-II', b2);
    csvout('B-III', b3);
    csvout('B-IV', b4);
    csvout('cross-engine', core);
    csvout('B3-layout-contrast', contrasts);
    csvout('B3-G-slope', slopes);
    csvout('B3-per-run', perrun)
    latexout('B-I', b1,
             ['model', 'position', 'engine', 'independent_repeats', 'reusing_hit_rate_mean', 'uncached_tokens_mean'],
             'Marker-position token-exact replay', 'tab:B-I',
             r'Qwen middle/marker-at-end: vLLM reference $n=1$, SGLang isolated replay $n=5$. CSV contains bootstrap 95\% confidence intervals; $n=1$ reference has no CI.')
    latexout('B-II', b2, ['model', 'N', 'f', 'sglang_CAF_mean', 'vllm_CAF_mean', 'CAF_relative_parity_error'],
             'Paired input-cost amplification and cross-engine parity', 'tab:B-II',
             r'CAF uses uncached $+f\cdot$cached input tokens and matched benign runs. Fixed parity tolerance is 5\%.')
    latexout('B-III', b3, ['G', 'N', 'layout', 'arm', 'sglang_hit_mean', 'sglang_CAF_f0.1_mean', 'vllm_CAF_f0.1_mean',
                           'sglang_loss_mean'], 'Original E5 layout and tool-arm replay', 'tab:B-III',
             r'S is the archived shared document plus task preamble; G is the archived GovReport slice. No source tokens were rebuilt.')
    latexout('B-IV', b4, ['engine', 'granularity', 'N', 'loss_mean', 'loss_prediction', 'loss_approx_relative_error',
                          'CAF_actual_mean', 'CAF_full_model_mean', 'CAF_full_model_relative_error'],
             'Small-G loss approximation and full-CAF errors reported separately', 'tab:B-IV',
             r'The historical 28\% belongs to lost-token error, not CAF error. Full CAF uses token-defined P, H and s; marker prompt overhead is recorded separately.')
    latexout('cross-engine', core,
             ['engine', 'model', 'fixed_floor', 'none_hit_mean', 'front_hit_mean', 'CAF_N20_f01_mean'],
             'Cross-engine generality at token-identical inputs', 'tab:B-cross-engine',
             r'Floors were fixed from archived tokens before SGLang observations. Native replay measures the cache layer, not AutoGen-on-SGLang integration.')
    h1 = all(r['reusing_hit_rate_mean'] >= .95 for r in b1 if
             r['engine'] == 'sglang' and r['position'] in ['none', 'marker-at-end']) and all(
        abs(r['reusing_replica_mean_mean'] - pred['floor_prediction'][r['model']]) <= .01 for r in b1 if
        r['engine'] == 'sglang' and r['position'] == 'front')
    h1 = h1 and all(next(r['reusing_hit_rate_mean'] for r in b1 if
                         r['engine'] == 'sglang' and r['model'] == m and r['position'] == 'front') < next(
        r['reusing_hit_rate_mean'] for r in b1 if
        r['engine'] == 'sglang' and r['model'] == m and r['position'] == 'middle') < next(
        r['reusing_hit_rate_mean'] for r in b1 if
        r['engine'] == 'sglang' and r['model'] == m and r['position'] == 'none') for m in ['qwen', 'llama'])
    mono = all(
        all(a < b for a, b in zip(vals, vals[1:])) for model in ['qwen', 'llama'] for e in ['sglang', 'vllm'] for f in
        [.1, .25, .5] for vals in
        [[next(r[e + '_CAF_mean'] for r in b2 if r['model'] == model and r['N'] == n and r['f'] == f) for n in
          [3, 5, 10, 20]]])
    h2 = all(r['parity_pass'] for r in b2) and mono
    large = all(r['sglang_loss_relative_error'] <= .05 for r in b3 if r['arm'] == 'nonce-tool' and r['G'] >= 2000)
    contrastok = all(r['relative_error'] <= .05 for r in contrasts if r['engine'] == 'sglang')
    controlok = all(r['sglang_hit_mean'] >= .99 and abs(r['sglang_CAF_f0.1_mean'] - 1) < 1e-6 for r in b3 if
                    r['arm'] in ['const-tool', 'broadcast'])
    h3 = large and contrastok and controlok
    base = next(r for r in b4 if r['engine'] == 'vllm' and r['N'] == 5);
    fine = next(r for r in b4 if r['engine'] == 'sglang' and r['granularity'] == 1 and r['N'] == 5)
    h4 = fine['loss_approx_relative_error'] < base['loss_approx_relative_error']
    h4_all_N = all(next(r['loss_approx_relative_error'] for r in b4 if
                        r['engine'] == 'sglang' and r['granularity'] == 1 and r['N'] == n) < next(
        r['loss_approx_relative_error'] for r in b4 if r['engine'] == 'vllm' and r['N'] == n) for n in [5, 20])
    h4_label = 'supported' if h4_all_N else (
        'partially supported (N=5 primary contrast only)' if h4 else 'not supported')
    coarse = next((r for r in b4 if r['engine'] == 'sglang' and r['granularity'] == 16 and r['N'] == 5), None)
    conclusions = {'H-B1': {'supported': h1}, 'H-B2': {'supported': h2, 'parity_max_relative_error': max(
        r['CAF_relative_parity_error'] for r in b2), 'both_engines_monotonic': mono},
                   'H-B3': {'supported': h3, 'large_G_loss_pass': large, 'layout_contrast_pass': contrastok,
                            'benign_broadcast_pass': controlok},
                   'H-B4': {'supported': h4, 'vllm_loss_error': base['loss_approx_relative_error'],
                            'sglang_page1_loss_error': fine['loss_approx_relative_error'],
                            'page16_directional_support': None if coarse is None else coarse[
                                                                                          'loss_approx_relative_error'] >
                                                                                      fine[
                                                                                          'loss_approx_relative_error']},
                   'audit': data['audit'],
                   'cache_vs_lcp_differences_count': len(data['cached_vs_exact_lcp_differences'])}
    write(R / 'reports/conclusions.json', conclusions)
    conclusions['H-B4'].update(supported=h4_all_N, primary_N5_supported=h4, all_N_supported=h4_all_N, verdict=h4_label,
                               N20_sglang_loss_error=next(r['loss_approx_relative_error'] for r in b4 if
                                                          r['engine'] == 'sglang' and r['granularity'] == 1 and r[
                                                              'N'] == 20), N20_vllm_loss_error=next(
            r['loss_approx_relative_error'] for r in b4 if r['engine'] == 'vllm' and r['N'] == 20))
    conclusions.update(
        page1_cache_vs_lcp_differences_count=sum(x['page'] == 1 for x in data['cached_vs_exact_lcp_differences']),
        page16_rounding_differences_count=sum(x['page'] == 16 for x in data['cached_vs_exact_lcp_differences']))
    write(R / 'reports/conclusions.json', conclusions)
    lines = ['# Cross-engine replay (vLLM vs SGLang)', '',
             '| Engine | Model | Fixed floor | None hit | Front hit | CAF N=20 f=0.1 |',
             '|---|---|---:|---:|---:|---:|']
    for r in core: lines.append(
        f"| {r['engine']} | {r['model']} | {r['fixed_floor']:.4%} | {r['none_hit_mean']:.4%} | {r['front_hit_mean']:.4%} | {r['CAF_N20_f01_mean']:.4f} |")
    lines += ['', f'H-B1 supported: {h1}',
              f"H-B2 supported: {h2}; max CAF parity error {conclusions['H-B2']['parity_max_relative_error']:.3%}",
              f'H-B3 supported: {h3}', f'H-B4: {h4_label}']
    if coarse: lines.append(f"Page-size-16 attribution: G50,N5 loss error={coarse['loss_approx_relative_error']:.2%}")
    ancillary = read(R / 'evidence/ancillary-state.json');
    temperature = read(R / 'data/ancillary/temperature-check.json');
    calibration = read(R / 'data/ancillary/page16-calibration.json')
    assert ancillary['status'] == 'complete' and temperature['pass'] and calibration['pass']
    (R / 'reports').mkdir(exist_ok=True)
    (R / 'reports/B_full_report.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(json.dumps(conclusions, ensure_ascii=False, indent=2))


if __name__ == '__main__': main()

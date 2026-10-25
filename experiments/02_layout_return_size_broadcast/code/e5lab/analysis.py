import statistics
from .common import *


def analyze():
    from .audit import audit_formal
    audit = audit_formal();
    data = [read(p) for p in (ROOT / 'data/formal').glob('*/result.json')];
    rows = [];
    summary = []
    for r in data:
        baseline = next(x for x in data if
                        (x['g_tokens'], x['N'], x['repeat'], x['layout'], x['mode']) == (r['g_tokens'], r['N'],
                                                                                         r['repeat'], r['layout'],
                                                                                         'const-tool'))
        expected_loss = (r['N'] - 1) * (r['g_tokens'] + (2000 if r['layout'] == 'prepend' else 0)) if r[
                                                                                                          'mode'] == 'nonce-tool' else 0
        delta_prompt = r['prompt_tokens'] - baseline['prompt_tokens'];
        cache_lost = baseline['cached_tokens'] - r['cached_tokens']
        row = {k: r[k] for k in
               ['name', 'g_tokens', 'S_tokens', 'N', 'repeat', 'layout', 'mode', 'tool_calls', 'prompt_tokens',
                'cached_tokens', 'uncached_tokens', 'reusing_hit_rate']}
        row.update(benign_run=baseline['name'], lcp_worker2=r['workers'][1]['lcp_with_worker1'],
                   cached_worker2=r['workers'][1]['cached_tokens'],
                   header_or_history_prediction_hit=sum(w['block_prediction'] for w in r['workers'][1:]) / sum(
                       w['prompt_tokens'] for w in r['workers'][1:]), extra_prompt_tokens_vs_benign=delta_prompt,
                   lost_reused_tokens=cache_lost, formula_lost_tokens=expected_loss,
                   loss_relative_error=(cache_lost - expected_loss) / expected_loss if expected_loss else 0,
                   engine_block_max_abs_error=max(abs(w['engine_minus_block_prediction']) for w in r['workers']))
        for f in [.1, .25, .5]:
            delta = r['costs'][str(f)] - baseline['costs'][str(f)]
            row[f'CAF_f{f}'] = r['costs'][str(f)] / baseline['costs'][str(f)]
            row[f'deltaC_f{f}'] = delta;
            row[f'deltaC_prompt_overhead_corrected_f{f}'] = delta - delta_prompt;
            row[f'formula_deltaC_f{f}'] = (1 - f) * expected_loss
        rows.append(row)
    csvwrite(ROOT / 'tables/append-long-runs.csv', sorted(rows, key=lambda r: r['name']))
    for g in [50, 2000, 4000]:
        for n in [5, 20]:
            for l in ['prepend', 'append']:
                for m in ['const-tool', 'nonce-tool', 'broadcast']:
                    part = [r for r in rows if (r['g_tokens'], r['N'], r['layout'], r['mode']) == (g, n, l, m)];
                    assert len(part) == 5
                    q = {'g_tokens': g, 'S_tokens': 2000, 'N': n, 'layout': l, 'mode': m, 'repeats': 5}
                    for key in ['reusing_hit_rate', 'uncached_tokens', 'lcp_worker2', 'cached_worker2',
                                'header_or_history_prediction_hit', 'lost_reused_tokens', 'formula_lost_tokens',
                                'loss_relative_error', 'CAF_f0.1', 'CAF_f0.25', 'CAF_f0.5', 'deltaC_f0.1',
                                'deltaC_prompt_overhead_corrected_f0.1', 'formula_deltaC_f0.1']:
                        vals = [r[key] for r in part];
                        q[key + '_mean'] = statistics.mean(vals);
                        q[key + '_sd'] = statistics.stdev(vals)
                    q['cache_verdict'] = 'Go' if (m != 'nonce-tool' or (all(r['CAF_f0.1'] > 1 for r in part) and max(
                        r['engine_block_max_abs_error'] for r in part) <= 32 and (g == 50 or max(
                        abs(r['loss_relative_error']) for r in part) <= .05))) else 'No-Go'
                    if l == 'append' and g == 50 and m == 'nonce-tool' and q['reusing_hit_rate_mean'] <= .95: q[
                        'cache_verdict'] = 'No-Go'
                    summary.append(q)
    csvwrite(ROOT / 'tables/append-long-summary.csv', summary)
    comparisons = []
    for n in [5, 20]:
        for l in ['prepend', 'append']:
            for rep in range(1, 6):
                low = next(r for r in rows if
                           (r['g_tokens'], r['N'], r['repeat'], r['layout'], r['mode']) == (2000, n, rep, l,
                                                                                            'nonce-tool'))
                high = next(r for r in rows if
                            (r['g_tokens'], r['N'], r['repeat'], r['layout'], r['mode']) == (4000, n, rep, l,
                                                                                             'nonce-tool'))
                comparisons.append({'N': n, 'layout': l, 'repeat': rep,
                                    'delta_lost_tokens_G4000_minus_G2000': high['lost_reused_tokens'] - low[
                                        'lost_reused_tokens'], 'formula_increment': (n - 1) * 2000,
                                    'measured_slope_per_G_token': (high['lost_reused_tokens'] - low[
                                        'lost_reused_tokens']) / 2000, 'formula_slope': n - 1})
    csvwrite(ROOT / 'tables/G-size-linear-contrast.csv', comparisons)
    from .plots import plot
    plot()
    lines = ['# Layout, return size, and broadcast (N=5/20)', '',
             '| G tokens | N | Layout | Arm | Hit rate | CAF f=0.1 | Lost reused tokens | Structural prediction | Check |',
             '|---:|---:|---|---|---:|---:|---:|---:|---|']
    for q in summary:
        lines.append(
            f"|{q['g_tokens']}|{q['N']}|{q['layout']}|{q['mode']}|{q['reusing_hit_rate_mean']:.4f}|{q['CAF_f0.1_mean']:.4f}|{q['lost_reused_tokens_mean']:.1f}|{q['formula_lost_tokens_mean']:.1f}|{q['cache_verdict']}|")
    go = all(q['cache_verdict'] == 'Go' for q in summary)
    lines += ['', f'All cells pass the pre-specified cache checks: {go}.']
    (ROOT / 'reports').mkdir(exist_ok=True)
    (ROOT / 'reports/E5_summary.md').write_text('\n'.join(lines) + '\n')
    save(ROOT / 'evidence/status.json',
         {'phase': 'complete', 'formal_runs': 180, 'formal_model_requests': 2250, 'Go': go, 'updated_utc': now()});
    print('COMPLETE', go, audit, flush=True)


if __name__ == '__main__': analyze()

from pathlib import Path
import json, csv, math, time, hashlib, statistics
import numpy as np
from detector import score, decisions
from fit_detector import auc

R = Path(__file__).resolve().parents[1]


def read(p): return json.loads(p.read_text(encoding='utf-8'))


def save(p, x): p.parent.mkdir(parents=True, exist_ok=True);p.write_text(json.dumps(x, ensure_ascii=False, indent=2),
                                                                         encoding='utf-8')


def table(name, rows, fields=None, caption=None, note=''):
    (R / 'tables').mkdir(exist_ok=True);
    fields = fields or list(dict.fromkeys(k for r in rows for k in r))
    with (R / 'tables' / f'{name}.csv').open('w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fields);
        w.writeheader();
        w.writerows([{k: r.get(k) for k in fields} for r in rows])


def tex(name, rows, fields, caption, note=''):
    labels = {'OFF_hit': 'OFF hit', 'ON_hit': 'ON hit', 'OFF_CAF': 'OFF CAF', 'ON_CAF': 'ON CAF',
              'bypass_after_normalization': 'Bypass', 'original_token_changed_fraction': 'Changed fraction',
              'structural_oracle': 'Structure check', 'target_FPR': 'Target FPR', 'test_TPR': 'TPR', 'test_FPR': 'FPR',
              'test_AUC': 'AUC', 'negative_rounds': 'Neg. rounds', 'positive_rounds': 'Pos. rounds',
              'joint_TPR_at_fixed_threshold': 'Joint TPR', 'feasible_rounds': 'Feasible',
              'max_feasible_CAF_sampled_grid': 'Grid max CAF', 'PCB_TPR': 'PCB TPR', 'token_mass_TPR': 'Mass TPR',
              'PCB_test_rounds': 'PCB n', 'token_mass_sessions': 'Mass n', 'extra_model_requests': 'Extra calls',
              'broadcast_CAF_f01': 'CAF', 'broadcast_hit_mean': 'Hit', 'independent_calls': 'Independent calls',
              'broadcast_calls': 'Broadcast calls', 'independent_calls_lost': 'Lost calls',
              'tool_return_log_bytes': 'Tool log bytes', 'minimal_counter_log_bytes': 'Counter bytes',
              'byte_comparison_cpu_median_us': 'Byte CPU (us)', 'health_statistics_cpu_median_us': 'Health CPU (us)'}

    def fmt(v):
        if isinstance(v, float): return f'{v:.4g}'
        return str(v).replace('_', r'\_').replace('%', r'\%').replace('&', r'\&')

    lines = [r'% Requires graphicx for resizebox.', r'\begin{table*}[t]', r'\centering', r'\caption{' + caption + '}',
             r'\label{tab:' + name + '}', r'\small', r'\resizebox{\textwidth}{!}{%',
             r'\begin{tabular}{' + 'l' * len(fields) + '}', r'\hline',
             ' & '.join(fmt(labels.get(k, k)) for k in fields) + r' \\', r'\hline']
    lines += [' & '.join(fmt(r.get(k, '')) for k in fields) + r' \\' for r in rows];
    lines += [r'\hline', r'\end{tabular}', r'}', r'\par\smallskip\parbox{0.98\linewidth}{\scriptsize ' + note + '}',
              r'\end{table*}'];
    (R / 'tables' / f'{name}.tex').write_text('\n'.join(lines) + '\n', encoding='utf-8')


def roc(y, s):
    y = np.asarray(y);
    s = np.asarray(s);
    thresholds = [float('inf')] + sorted(set(float(x) for x in s), reverse=True) + [float('-inf')]
    return [{'threshold': t, 'FPR': float((s[y == 0] > t).mean()), 'TPR': float((s[y == 1] > t).mean())} for t in
            thresholds]


def interval(rows, model, threshold, metric, B=10000):
    groups = sorted({r['group'] for r in rows});
    rng = np.random.default_rng(42)
    if len(groups) < 2: return None, None
    if metric == 'auc':
        vals = [];
        block = {g: [r for r in rows if r['group'] == g] for g in groups}
        for pick in rng.integers(0, len(groups), size=(B, len(groups))):
            rs = [r for j in pick for r in block[groups[j]]];
            vals.append(auc([r['label'] for r in rs], [score(model, r) for r in rs]))
    else:
        label = int(metric == 'tpr');
        a = []
        for g in groups:
            rs = [r for r in rows if r['group'] == g and r['label'] == label]
            if rs: a.append(np.mean([score(model, r) > threshold for r in rs]))
        if len(a) < 2: return None, None
        a = np.asarray(a);
        vals = a[rng.integers(0, len(a), size=(B, len(a)))].mean(axis=1)
    lo, hi = np.quantile(vals, [.025, .975]);
    return float(lo), float(hi)


def aggregate(ds, frozen):
    def stream(rows): return [(p, c) for r in rows for p, c in zip(r['prompt'], r['cached'])]

    def windows(a, w=25): return [sum(c for p, c in a[i - 24:i + 1]) / sum(p for p, c in a[i - 24:i + 1]) for i in
                                  range(24, len(a))]

    cal = [r for r in ds if r['split'] == 'calibration' and r['label'] == 0];
    test = [r for r in ds if r['split'] == 'test' and r['label'] == 0];
    att = [r for r in ds if r['split'] == 'test' and r['label'] == 1]
    ca = windows(stream(cal));
    threshold = sorted(ca)[max(0, int(np.floor(.01 * len(ca))))];
    te = windows(stream(test));
    before = stream(test);
    after = stream(att);
    merged = before + after;
    ratios = windows(merged);
    transition = len(before)
    flags = [(i + 25, r < threshold) for i, r in enumerate(ratios)];
    hits = [i - transition for i, flag in flags if flag and i > transition];
    lag = min(hits) if hits else None
    rows = [{'request_end': i + 25, 'hit_ratio': h, 'alert': h < threshold, 'attack_started': i + 25 > transition} for
            i, h in enumerate(ratios)]
    out = {'window_requests': 25, 'threshold': threshold, 'calibration_window_count': len(ca),
           'calibration_FPR': sum(x < threshold for x in ca) / len(ca), 'test_benign_window_count': len(te),
           'test_FPR': sum(x < threshold for x in te) / len(te), 'detection_lag_requests': lag,
           'attack_transition_request': transition,
           'scope': 'offline chronological concatenation of real request telemetry; no round metadata supplied; overlapping windows correlated, no production wall-time claim'}
    table('H-aggregate-stream', rows);
    save(R / 'reports/aggregate-summary.json', out);
    return out


def main():
    for folder in ['reports', 'tables', 'figures']: (R / folder).mkdir(exist_ok=True)
    data = read(R / 'data/compact-results.json');
    runs = data['results'];
    audit = data['audit'];
    assert audit['passed'];
    cfg = read(R / 'config/frozen-detector.json');
    ds = read(R / 'datasets/H2-dataset.json');
    ev = read(R / 'data/ancillary/H2-evasion.json');
    mass = read(R / 'data/ancillary/H2-token-mass.json');
    H3 = read(R / 'data/ancillary/H3.json')
    h1 = []
    for marker in ['none', 'zwsp', 'zwj', 'nbsp', 'whitespace', 'request-id', 'timestamp', 'html-comment']:
        r = {'marker': marker, 'model': 'llama', 'N': 5, 'repeats': 5}
        for on in [False, True]:
            subset = [x for x in runs if x['phase'] == 'H1' and x['marker'] == marker and x['normalized'] == on];
            assert len(subset) == 5
            base = {x['repeat']: x for x in runs if
                    x['phase'] == 'H1' and x['marker'] == 'none' and x['normalized'] == on}
            tag = 'ON' if on else 'OFF';
            r[tag + '_hit'] = statistics.mean(x['warm_hit'] for x in subset);
            r[tag + '_CAF'] = statistics.mean(x['cost_f01'] / base[x['repeat']]['cost_f01'] for x in subset)
        r['bypass_after_normalization'] = marker in ['request-id', 'timestamp', 'html-comment'] and r['ON_hit'] < .95;
        h1.append(r)
    table('H-I', h1);
    tex('H-I', h1, ['marker', 'OFF_hit', 'ON_hit', 'OFF_CAF', 'ON_CAF', 'bypass_after_normalization'],
        'Normalization, cache recovery and surviving markers',
        'Five isolated replays of archived E5 grounding; point estimates for deterministic counts. ON/OFF use their own matched normalized/unnormalized benign baseline. Hit rates exclude the cold replica.')
    col = read(R / 'data/ancillary/h1-collateral.json');
    collateral = [{k: r[k] for k in ['fixture', 'changed', 'original_token_changed_fraction', 'structural_oracle']} | {
        'before_tokens': len(r['before_tokens']), 'after_tokens': len(r['after_tokens'])} for r in col];
    table('H-I-collateral', collateral);
    tex('H-I-collateral', collateral, ['fixture', 'original_token_changed_fraction', 'structural_oracle'],
        'Collateral effects on legitimate content',
        'Fixed fixtures, not a language-population estimate. Token alteration is not itself proof of semantic harm; code and YAML have separate structure checks.')
    tm = read(R / 'data/ancillary/h1-cpu-timing.json');
    ns = np.asarray([r['ns'] for r in tm]);
    cpu = {'normalizations': len(ns), 'median_ms': float(np.median(ns) / 1e6),
           'p95_ms': float(np.quantile(ns, .95) / 1e6),
           'scope': 'normalization CPU-path wall time, excludes tokenization/network/model; repeated same tool returns, scheduling noise included'};
    save(R / 'reports/h1-cpu-summary.json', cpu)
    test = [r for r in ds if r['split'] == 'test'];
    metrics = [];
    curves = []
    models = {'joint-selected': (cfg['selected_model'], cfg['joint_thresholds'])}
    models.update({name: (r['model'], r['thresholds']) for name, r in cfg['single_models'].items()})
    models.update({c['name']: (c['model'], c['thresholds']) for c in cfg['candidates']})
    for task in ['all', 'hotpotqa', 'triviaqa', 'gov_report']:
        subset = [r for r in test if task == 'all' or r['task'] == task];
        y = [r['label'] for r in subset];
        neg = sum(v == 0 for v in y);
        pos = len(y) - neg
        for name, (model, ths) in models.items():
            scores = [score(model, r) for r in subset];
            A = auc(y, scores)
            for alpha in [.01, .05]:
                th = ths[str(alpha)];
                pred = np.asarray(scores) > th;
                y0 = np.asarray(y);
                row = {'model': 'llama', 'task': task, 'detector': name, 'target_FPR': alpha, 'threshold': th,
                       'test_TPR': float(pred[y0 == 1].mean()), 'test_FPR': float(pred[y0 == 0].mean()), 'test_AUC': A,
                       'negative_rounds': neg, 'positive_rounds': pos,
                       'document_groups': len({r['group'] for r in subset}), 'negative_FPR_resolution': 1 / neg,
                       'threshold_source': 'held-out calibration, frozen before evasion'}
                # Primary joint/per-statistic intervals, grouped on the original documents.
                if name in ['joint-selected', 'mean', 'minimum', 'below-floor', 'variance']:
                    for metric in ['tpr', 'fpr']:
                        lo, hi = interval(subset, model, th, metric);
                        row[metric + '_CI_lo'] = lo;
                        row[metric + '_CI_hi'] = hi
                    if alpha == .01: lo, hi = interval(subset, model, th, 'auc');row['AUC_CI_lo'] = lo;row[
                        'AUC_CI_hi'] = hi
                metrics.append(row)
            if task == 'all' and name in ['joint-selected', 'mean', 'minimum', 'below-floor',
                                          'variance']: curves.extend({'detector': name, **r} for r in roc(y, scores))
    table('H-II', metrics);
    table('H-ROC', curves);
    primary = [r for r in metrics if r['detector'] == 'joint-selected'];
    tex('H-II', primary,
        ['task', 'target_FPR', 'test_TPR', 'test_FPR', 'test_AUC', 'negative_rounds', 'positive_rounds'],
        'Frozen strongest preregistered joint cache-health detector',
        'All four cache features are offered jointly. Calibration targets are not guaranteed test FPR. CSV contains per-statistic, candidate, grouped-bootstrap intervals and finite-sample resolution. One percent is below 1/90 test-negative resolution in the pooled stratum.')
    anchors = read(R / 'data/ancillary/H2-anchors.json');
    anchor = []
    for model in ['qwen', 'llama']:
        rs = [r for r in anchors if r['model'] == model];
        ys = [r['label'] for r in rs];
        ss = [score(cfg['selected_model'], r) for r in rs];
        pr = np.asarray(ss) > cfg['joint_thresholds']['0.01'];
        yy = np.asarray(ys)
        anchor.append({'model': model, 'task': 'synthetic-anchor', 'AUC': auc(ys, ss),
                       'joint_TPR_transfer': float(pr[yy == 1].mean()), 'joint_FPR_transfer': float(pr[yy == 0].mean()),
                       'archived_runs': len(rs), 'document_groups': 1,
                       'CI': 'not estimated with a single semantic document group',
                       'scope': 'supplementary frozen Llama-detector transfer; not a new model-specific operating-point calibration'})
    table('H-II-anchor-transfer', anchor)
    frontier = [];
    per_stat = []
    for r in ev:
        d = decisions(cfg, r);
        assert d == r['decision'];
        assert abs(
            r['CAF_f01'] - r['cost_f01'] / next(x['cost_f01'] for x in runs if x['key'] == r['baseline_key'])) < 1e-10
        base_cost = next(x['cost_f01'] for x in runs if x['key'] == r['baseline_key']);
        floor_bound = (r['prompt'][0] + .145 * sum(r['prompt'][1:])) / base_cost
        if r['frontier_feasible']: assert r['CAF_f01'] <= floor_bound + 1e-9
        frontier.append({'task': r['task'], 'group': r['group'], 'N': r['N'], 'axis': r['axis'],
                         'parameter': r.get('k', r.get('remaining_requested')), 'CAF': r['CAF_f01'],
                         'joint_detected': d['detected_1pct'],
                         'min_warm_hit': min(c / p for c, p in zip(r['cached'][1:], r['prompt'][1:])),
                         'mean_hit': r['features']['mean_hit'], 'variance': r['features']['variance_hit'],
                         'floor_pass': d['floor_constraint'], 'mean_band_pass': d['mean_band_constraint'],
                         'feasible': r['frontier_feasible'], 'per_replica_floor_CAF_upper_bound': floor_bound})
        for name, (model, ths) in models.items():
            if name not in ['joint-selected', 'mean', 'minimum', 'below-floor', 'variance']: continue
            per_stat.append({'task': r['task'], 'group': r['group'], 'N': r['N'], 'axis': r['axis'],
                             'parameter': r.get('k', r.get('remaining_requested')), 'detector': name,
                             'score': score(model, r), 'detected_at_target_FPR1': score(model, r) > ths['0.01'],
                             'CAF': r['CAF_f01']})
    table('H-frontier-per-round', frontier);
    table('H-evasion-per-statistic', per_stat);
    frontiers = []
    for axis in ['uniform', 'partial']:
        for N in [5, 20]:
            rr = [r for r in frontier if r['axis'] == axis and r['N'] == N];
            feas = [r for r in rr if r['feasible']]
            groups = sorted({r['group'] for r in rr});
            maxima = np.asarray([max([r['CAF'] for r in feas if r['group'] == g] or [1.0]) for g in groups]);
            tprs = np.asarray([statistics.mean(r['joint_detected'] for r in rr if r['group'] == g) for g in groups]);
            rng = np.random.default_rng(42);
            indices = rng.integers(0, len(groups), size=(10000, len(groups)));
            maxci = np.quantile(maxima[indices].max(axis=1), [.025, .975]);
            tprci = np.quantile(tprs[indices].mean(axis=1), [.025, .975])
            frontiers.append({'axis': axis, 'N': N, 'attack_rounds': len(rr), 'document_groups': len(groups),
                              'joint_TPR_at_fixed_threshold': statistics.mean(r['joint_detected'] for r in rr),
                              'TPR_CI_lo': float(tprci[0]), 'TPR_CI_hi': float(tprci[1]), 'feasible_rounds': len(feas),
                              'max_feasible_CAF_sampled_grid': max([r['CAF'] for r in feas] or [1.0]),
                              'max_CAF_CI_lo': float(maxci[0]), 'max_CAF_CI_hi': float(maxci[1]),
                              'none_feasible': not feas, 'mean_constraint_lower': cfg['mean_benign_band'][0],
                              'floor': .95})
    table('H-frontier-summary', frontiers);
    tex('H-frontier-summary', frontiers,
        ['axis', 'N', 'joint_TPR_at_fixed_threshold', 'feasible_rounds', 'max_feasible_CAF_sampled_grid'],
        'Constrained sampled-grid evasion frontier',
        'Feasible requires every warm replica hit at least 0.95, mean in the frozen one-percent-FPR benign band, and no joint-detector alert. Maximum is over the evaluated grid, not a global optimum. A no-feasible-row maximum of 1 is the no-attack reference.')
    massrows = [];
    paired = []
    for r in mass:
        massrows.append({k: r[k] for k in ['key', 'task', 'group', 'mode', 'max_relative_delta', 'H_delta_detected',
                                           'replica_health_detected', 'replica_probe_warm_hit', 'input_cost_f01',
                                           'CAFtok_source_definition']} | {'min_adjacent_delta': min(r['delta']),
                                                                           'original_D2_strict': any(
                                                                               t['trigger'] for t in
                                                                               r['original_D2']['strict']),
                                                                           'original_D2_moderate': any(
                                                                               t['trigger'] for t in
                                                                               r['original_D2']['moderate']),
                                                                           'original_D2_relaxed': any(
                                                                               t['trigger'] for t in
                                                                               r['original_D2']['relaxed'])})
    for r in mass:
        if r['mode'] != 'attack-Full': continue
        benign = next(s for s in mass if s['group'] == r['group'] and s['mode'] == 'benign-Full');
        drop = next(s for s in mass if s['group'] == r['group'] and s['mode'] == 'attack-Drop');
        paired.append({'task': r['task'], 'group': r['group'], 'attack_full_prompt_sum': sum(r['prompt']),
                       'attack_drop_prompt_sum': sum(drop['prompt']),
                       'full_vs_drop_input_ratio': sum(r['prompt']) / sum(drop['prompt']),
                       'full_vs_benign_input_ratio': sum(r['prompt']) / sum(benign['prompt']),
                       'full_vs_benign_cache_input_cost_ratio': r['input_cost_f01'] / benign['input_cost_f01'],
                       'full_vs_drop_cache_input_cost_ratio': r['input_cost_f01'] / drop['input_cost_f01']})
    table('H-token-mass-sessions', massrows);
    table('H-token-mass-paired', paired)
    pcb = [r for r in test if r['label'] == 1];
    negative_pcb = [r for r in test if r['label'] == 0];
    positive_mass = [r for r in massrows if r['mode'] == 'attack-Full'];
    negative_mass = [r for r in massrows if r['mode'] == 'benign-Full'];
    two = []
    for name in ['H-calibrated-relative-delta', 'persistbill-D2-strict', 'persistbill-D2-moderate',
                 'persistbill-D2-relaxed', 'replica-cache-health-joint']:
        def pcb_flag(r):
            delta = max([max(0, p - q) for q, p in zip(r['prompt'], r['prompt'][1:])] or [0]);
            rel = delta / r['prompt'][0]
            if name == 'H-calibrated-relative-delta': return rel > cfg['H_relative_delta_threshold']
            if name == 'replica-cache-health-joint': return score(cfg['selected_model'], r) > cfg['joint_thresholds'][
                '0.01']
            absolute, ratio, minimum = \
            {'persistbill-D2-strict': (5000, 2, 2000), 'persistbill-D2-moderate': (10000, 3, 2000),
             'persistbill-D2-relaxed': (50000, 10, 2000)}[name];
            return delta > absolute or rel > ratio and delta > minimum

        if name == 'H-calibrated-relative-delta':
            mflag = [r['H_delta_detected'] for r in positive_mass];nflag = [r['H_delta_detected'] for r in
                                                                            negative_mass]
        elif name == 'replica-cache-health-joint':
            mflag = [r['replica_health_detected'] for r in positive_mass];nflag = [r['replica_health_detected'] for r in
                                                                                   negative_mass]
        else:
            mflag = [r['original_D2_' + name.rsplit('-', 1)[1]] for r in positive_mass];nflag = [
                r['original_D2_' + name.rsplit('-', 1)[1]] for r in negative_mass]
        two.append({'monitor': name, 'PCB_TPR': statistics.mean(pcb_flag(r) for r in pcb),
                    'token_mass_TPR': statistics.mean(mflag),
                    'PCB_benign_FPR': statistics.mean(pcb_flag(r) for r in negative_pcb),
                    'mass_matched_benign_FPR': statistics.mean(nflag), 'PCB_test_rounds': len(pcb),
                    'token_mass_sessions': len(positive_mass), 'matched_benign_sessions': len(negative_mass),
                    'scope': 'delta on original adjacent inputs; health on same-terminal-history redundant probes, not incomparable growing turns; H relative threshold calibrated on replica variation, not temporal tool-growth population'})
    table('H-2x2', two);
    tex('H-2x2', two,
        ['monitor', 'PCB_TPR', 'token_mass_TPR', 'mass_matched_benign_FPR', 'PCB_test_rounds', 'token_mass_sessions'],
        'Complementary observables: prompt growth and cache reuse',
        'Original D2 presets and H-calibrated growth are separated. Matched benign temporal sessions expose cross-domain false positives of a replica-calibrated relative-growth rule; do not silently retune it. V0 returns were enlarged to 12000 tokens by approval. Healthy terminal probes do not erase recurring total-input growth. Source CAFtok and matched cache-input CAF differ.')
    neg = [r for r in test if r['label'] == 0];
    byte_tpr = statistics.mean(r['byte_divergent'] for r in pcb);
    byte_fpr = statistics.mean(r['byte_divergent'] for r in neg);
    jointrow = next(
        r for r in metrics if r['task'] == 'all' and r['detector'] == 'joint-selected' and r['target_FPR'] == .01)
    dual = [{'monitor': 'cache-health-joint', 'observability': 'engine cached-token counters plus round grouping',
             'test_TPR': jointrow['test_TPR'], 'test_FPR': jointrow['test_FPR'], 'extra_model_requests': 0,
             'cost': 'feature computation only; grouping required',
             'evasion': 'uniform shortened invalidation can remain below threshold',
             'false_positive_source': 'legitimate low reuse and persona-tail variation'},
            {'monitor': 'raw-tool-byte-divergence',
             'observability': 'per-replica tool-return byte capture and comparison', 'test_TPR': byte_tpr,
             'test_FPR': byte_fpr, 'extra_model_requests': 0,
             'cost': 'N tool-return captures plus byte comparison; raw log volume required',
             'evasion': 'one changed tool return suffices for divergence flag in tested grid',
             'false_positive_source': 'legitimate per-call request metadata; cannot infer maliciousness from raw inequality'}]
    table('H-monitor-comparison', dual);
    tex('H-monitor-comparison', dual, ['monitor', 'test_TPR', 'test_FPR', 'extra_model_requests'],
        'Distinct monitoring observability and false-positive profiles',
        'Tool bytes for archived T rows are reconstructed from the controlled context-marker protocol; new H negative rows have full recorded prompts and known tool boundaries. Divergence is not proof of malicious content. CPU/log-volume measurements are separate.')
    monitorcost = read(R / 'data/ancillary/monitor-cost.json');
    table('H-monitor-cost', monitorcost);
    tex('H-monitor-cost', monitorcost,
        ['task', 'N', 'tool_return_log_bytes', 'minimal_counter_log_bytes', 'byte_comparison_cpu_median_us',
         'health_statistics_cpu_median_us'], 'Monitoring CPU paths and required byte-log volume',
        'Nine held-out fixed-buffer cases; CPU logic excludes capture, I/O, tokenization and network. Full tool-return volume versus minimal cached/prompt counter serialization does not measure production storage bills.')
    agg = aggregate(ds, cfg);
    table('H-III', H3);
    tex('H-III', H3,
        ['N', 'G', 'layout', 'broadcast_CAF_f01', 'broadcast_hit_mean', 'independent_calls', 'broadcast_calls',
         'independent_calls_lost'], 'Prevention cost: archived broadcast immunity',
        'Cache effects reference existing E5, with no H GPU rerun. N-to-one calls save work but sacrifice independent tool samples. Bernoulli-error values in CSV are an illustrative analytic model, not a QA quality measurement. Pin-KV is optional and not measured.')
    peraxes = [r for r in frontier if r['axis'] == 'partial'];
    uniformfeasible = [r for r in frontier if r['axis'] == 'uniform' and r['feasible']]
    hypotheses = {'H-H1': {'supported': all(
        abs(r['ON_hit'] - h1[0]['ON_hit']) <= .01 for r in h1 if r['marker'] in ['zwsp', 'zwj', 'nbsp', 'whitespace'])},
                  'H-H1b': {'supported': all(r['bypass_after_normalization'] for r in h1 if
                                             r['marker'] in ['request-id', 'timestamp', 'html-comment'])},
                  'H-H2': {'supported': jointrow['test_TPR'] >= .95 and jointrow['test_FPR'] <= .05,
                           'test_TPR': jointrow['test_TPR'], 'test_FPR': jointrow['test_FPR'],
                           'test_AUC': jointrow['test_AUC'],
                           'target_1pct_operating_point_met_on_test': jointrow['test_FPR'] <= .01},
                  'H-H2b': {'supported': bool(uniformfeasible) and max(r['CAF'] for r in uniformfeasible) > 1,
                            'max_sampled_grid_CAF': max([r['CAF'] for r in uniformfeasible] or [1.0]),
                            'partial_axis_constraint_feasible_count': sum(r['feasible'] for r in peraxes),
                            'partial_axis_joint_TPR': statistics.mean(r['joint_detected'] for r in peraxes)}, 'H-H2c': {
            'supported': next(r for r in two if r['monitor'] == 'H-calibrated-relative-delta')[
                             'token_mass_TPR'] >= .95 and
                         next(r for r in two if r['monitor'] == 'H-calibrated-relative-delta')['PCB_TPR'] <= .05 and
                         jointrow['test_TPR'] >= .95,
            'original_moderate_mass_TPR': next(r for r in two if r['monitor'] == 'persistbill-D2-moderate')[
                'token_mass_TPR'],
            'cache_health_mass_TPR': next(r for r in two if r['monitor'] == 'replica-cache-health-joint')[
                'token_mass_TPR'],
            'note': 'health is redundancy-layer specific, no general assertion that growing-turn cache ratios stay high'},
                  'H-H2d': {'supported': agg['detection_lag_requests'] is not None, 'aggregate': agg,
                            'round_grouping_assumption': True},
                  'H-H3': {'supported': all(abs(r['broadcast_CAF_f01'] - 1) < 1e-12 for r in H3), 'new_gpu_requests': 0,
                           'lost_independent_calls': [4, 19],
                           'independence_quality_estimate': 'analytic hypothetical Bernoulli model only'}}
    original = next(r for r in two if r['monitor'] == 'persistbill-D2-moderate');
    relative = next(r for r in two if r['monitor'] == 'H-calibrated-relative-delta');
    health = next(r for r in two if r['monitor'] == 'replica-cache-health-joint')
    hypotheses['H-H2c'].update(supported=original['token_mass_TPR'] >= .95 and original['PCB_TPR'] <= .05 and original[
        'mass_matched_benign_FPR'] <= .05 and health['PCB_TPR'] >= .95 and health['token_mass_TPR'] <= .05,
                               primary_growth_monitor='original persistbill D2 moderate, matched benign checked',
                               original_moderate_matched_benign_FPR=original['mass_matched_benign_FPR'],
                               H_relative_temporal_benign_FPR=relative['mass_matched_benign_FPR'],
                               transfer_boundary='H relative delta calibrated on within-round prompt variation must not be transplanted to temporal tool growth without new legitimate-session calibration; original D2 min-growth gate is retained')
    save(R / 'reports/conclusions.json', hypotheses);
    save(R / 'reports/analysis-context.json',
         {'audit': audit, 'selected_detector': cfg['selected_model']['name'], 'mean_band': cfg['mean_benign_band'],
          'joint_threshold': cfg['joint_thresholds'], 'h1_cpu': cpu, 'frontier': frontiers, 'joint_primary': jointrow,
          'dual_monitor': dual, 'aggregate': agg,
          'token_mass_pair_means': {k: statistics.mean(r[k] for r in paired) for k in
                                    ['full_vs_drop_input_ratio', 'full_vs_benign_input_ratio',
                                     'full_vs_benign_cache_input_cost_ratio']}})
    print(json.dumps(hypotheses, ensure_ascii=False, indent=2))


if __name__ == '__main__': main()

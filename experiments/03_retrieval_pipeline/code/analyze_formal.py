from pathlib import Path
import json, csv, collections, math
import numpy as np

R = Path(__file__).resolve().parents[1]


def read(p): return json.loads(p.read_text(encoding='utf-8'))


def save(p, x): p.write_text(json.dumps(x, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def table(name, rows):
    keys = list(dict.fromkeys(k for r in rows for k in r));
    p = R / 'tables' / f'{name}.csv';
    p.parent.mkdir(exist_ok=True)
    with p.open('w', encoding='utf-8', newline='') as f: w = csv.DictWriter(f, keys);w.writeheader();w.writerows(rows)


def norm(s, task):
    import re, string
    exclude = string.punctuation + ('‘’´`' if task == 'triviaqa' else '')
    s = ''.join((' ' if task == 'triviaqa' else '') if c in exclude else c for c in s.lower());
    return ' '.join(re.sub(r'\b(a|an|the)\b', ' ', s).split())


def score(pred, golds, task):
    out = []
    for gold in golds:
        p, g = norm(pred, task), norm(gold, task);
        em = float(p == g);
        a, b = p.split(), g.split();
        common = sum((collections.Counter(a) & collections.Counter(b)).values())
        if task == 'hotpotqa' and p != g and (p in ['yes', 'no', 'noanswer'] or g in ['yes', 'no', 'noanswer']):
            f1 = 0.
        elif not a or not b:
            f1 = float(a == b)
        elif not common:
            f1 = 0.
        else:
            precision = common / len(a);recall = common / len(b);f1 = 2 * precision * recall / (precision + recall)
        out.append((em, f1))
    return {'em': max(x[0] for x in out), 'f1': max(x[1] for x in out)}


def ci(v):
    a = np.asarray(v, dtype=float);
    rng = np.random.default_rng(42);
    sample = a[rng.integers(0, len(a), size=(10000, len(a)))].mean(axis=1);
    return [float(x) for x in np.quantile(sample, [.025, .975])]


def main():
    data = read(R / 'data/compact-formal.json');
    assert data['audit']['passed'];
    runs = data['results'];
    queries = {(d['task'], d['id']): d for d in read(R / 'datasets/formal-queries.json')};
    lookup = {(r['task'], r['id'], r['N'], r['mode']): r for r in runs};
    pairs = [];
    agent = []
    for r in runs:
        native = r['mode'].startswith('functiontool');
        base = lookup[(r['task'], r['id'], r['N'], 'functiontool-benign' if native else 'benign')];
        d = queries[(r['task'], r['id'])];
        quality = score(r['aggregate_answer'], d['answers'], r['task']);
        baseline_quality = score(base['aggregate_answer'], d['answers'], r['task'])
        row = {'task': r['task'], 'query_id': r['id'], 'N': r['N'], 'arm': r['mode'], 'warm_hit': r['warm_hit'],
               'reflection_warm_hit': r.get('reflection_warm_hit'), 'prompt_tokens': r['prompt_tokens'],
               'cached_tokens': r['cached_tokens'], 'model_requests': r.get('model_requests', r['N']),
               'retrieval_calls': r['tool_calls'], 'CAF_f01': r['costs']['0.1'] / base['costs']['0.1'],
               'CAF_f025': r['costs']['0.25'] / base['costs']['0.25'],
               'CAF_f05': r['costs']['0.5'] / base['costs']['0.5'], 'em': quality['em'], 'f1': quality['f1'],
               'paired_em_difference': quality['em'] - baseline_quality['em'],
               'paired_f1_difference': quality['f1'] - baseline_quality['f1'],
               'aggregate_answer': r['aggregate_answer'],
               'answer_changed': norm(r['aggregate_answer'], r['task']) != norm(base['aggregate_answer'], r['task']),
               'original_context_retrieved': any(':' + r['id'] + ':' in x for x in r['top_k_ids']),
               'unique_tool_return_bytes': r['unique_tool_return_bytes'],
               'unique_rendered_bytes': r.get('unique_rendered_bytes'),
               'matched_denominator': 'functiontool-benign, two-request pipeline' if native else 'benign, application pre-retrieval pipeline'}
        pairs.append(row)
        for w in r['workers']: agent.append(
            {'task': r['task'], 'query_id': r['id'], 'N': r['N'], 'arm': r['mode'], 'replica': w['replica'],
             'answer': w['answer'], **score(w['answer'], d['answers'], r['task'])})
    for row in pairs: row['aggregate_UNKNOWN'] = norm(row['aggregate_answer'], row['task']) == 'unknown'
    table('E-paired-query-results', pairs);
    table('E-agent-quality', agent);
    summaries = [];
    qualityrows = [];
    neutral = []
    for task in ['hotpotqa', 'triviaqa', 'all']:
        for N in [5, 20]:
            for mode in read(R / 'config/formal-protocol.json')['formal_plan']['arms']:
                rs = [r for r in pairs if r['N'] == N and r['arm'] == mode and (task == 'all' or r['task'] == task)];
                row = {'task': task, 'N': N, 'arm': mode, 'query_groups': len(rs),
                       'model_requests': sum(r['model_requests'] for r in rs),
                       'warm_hit_mean': float(np.mean([r['warm_hit'] for r in rs])),
                       'warm_hit_CI_low': ci([r['warm_hit'] for r in rs])[0],
                       'warm_hit_CI_high': ci([r['warm_hit'] for r in rs])[1],
                       'CAF_mean': float(np.mean([r['CAF_f01'] for r in rs])),
                       'CAF_CI_low': ci([r['CAF_f01'] for r in rs])[0],
                       'CAF_CI_high': ci([r['CAF_f01'] for r in rs])[1], 'CAF_min': min(r['CAF_f01'] for r in rs),
                       'CAF_max': max(r['CAF_f01'] for r in rs), 'original_context_retrieved_fraction': float(
                        np.mean([r['original_context_retrieved'] for r in rs])),
                       'controlled_index': '132 passages / 3 contexts',
                       'S_scope': 'controlled application history (2000-token synthetic shared history)'};
                summaries.append(row)
                if mode in ['static-neutral', 'det-tool-neutral']: neutral.append(
                    {k: v for k, v in row.items()} | {'CAF_CI_crosses_1': row['CAF_CI_low'] <= 1 <= row['CAF_CI_high'],
                                                      'CI_method': 'percentile bootstrap, query groups, 10000 resamples, seed42',
                                                      'posthoc_adjustment': False})
                qr = {'task': task, 'N': N, 'arm': mode, 'query_groups': len(rs),
                      'changed_aggregate_fraction': float(np.mean([r['answer_changed'] for r in rs])),
                      'aggregate_UNKNOWN_fraction': float(np.mean([r['aggregate_UNKNOWN'] for r in rs])),
                      'original_context_retrieved_fraction': float(
                          np.mean([r['original_context_retrieved'] for r in rs]))}
                for m in ['em', 'f1']:
                    qr[m + '_mean'] = float(np.mean([r[m] for r in rs]));
                    v = [r['paired_' + m + '_difference'] for r in rs];
                    lo, hi = ci(v);
                    qr[m + '_paired_difference'] = float(np.mean(v));
                    qr[m + '_difference_CI_low'] = lo;
                    qr[m + '_difference_CI_high'] = hi;
                    qr[m + '_CI_excludes_zero'] = lo > 0 or hi < 0
                qualityrows.append(qr)
    table('E-I', summaries);
    table('E-neutral-CI', neutral);
    table('E-IV-quality-paired', qualityrows)
    # Actual native boundary separately; its identity precedes G and already invalidates late G even benign.
    native = [r for r in summaries if r['arm'].startswith('functiontool')];
    table('E-VI-native-boundary', native)
    qs = [q for q in qualityrows if q['arm'] in ['pretrieval-zerowidth', 'pretrieval-metadata']];
    strong = [r for r in summaries if r['arm'] in ['pretrieval-zerowidth', 'pretrieval-metadata']];
    e5 = [r for r in summaries if r['arm'] == 'static-deploy-variance'];
    ns = [r for r in neutral if r['task'] != 'all']
    conclusions = {
        'E-E1a': {'all_neutral_CIs_cross_1': all(r['CAF_CI_crosses_1'] for r in ns if r['arm'] == 'static-neutral'),
                  'interpretation': 'report actual CI and small block/input-length effects; no forced centering'},
        'E-E1b': {'all_neutral_CIs_cross_1': all(r['CAF_CI_crosses_1'] for r in ns if r['arm'] == 'det-tool-neutral')},
        'E-E2': {'warm_hit_range': [min(r['warm_hit_mean'] for r in strong), max(r['warm_hit_mean'] for r in strong)],
                 'CAF_mean_range': [min(r['CAF_mean'] for r in strong), max(r['CAF_mean'] for r in strong)],
                 'scope': 'one controlled grounding-first independent-retrieval mode cited in section III, no prevalence inference'},
        'E-E3': {'audit_passed': True, 'native_midgeneration_append': True},
        'E-E4': {'any_paired_CI_excludes_zero': any(q['em_CI_excludes_zero'] or q['f1_CI_excludes_zero'] for q in qs),
                 'limits': 'low coverage controlled index, greedy; bounds are not equivalence; no task competence claim from low baseline accuracy'},
        'E-E5': {
            'scope': 'deployment rendering conditional only, raw static retrieval return remains identical; no attacker-created rendering variance',
            'warm_hit_range': [min(r['warm_hit_mean'] for r in e5), max(r['warm_hit_mean'] for r in e5)]}, 'E-E6': {
            'scope': 'named single-Tool policy, actual model-generated native invocation plus reflection; default auto no-call separately retained',
            'denominator': 'own two-request benign',
            'boundary': 'S retained; initial replica identity precedes appended G even benign, so nonce may add no loss; do not call this default autonomous tool success'}}
    (R / 'reports').mkdir(exist_ok=True);
    save(R / 'reports/formal-conclusions.json', conclusions);
    save(R / 'reports/analysis-context.json',
         {'audit': data['audit'], 'summary': summaries, 'neutral': neutral, 'quality': qualityrows,
          'conclusions': conclusions});
    print(json.dumps(conclusions, ensure_ascii=False, indent=2))


if __name__ == '__main__': main()

from common import *
from detector import decisions


def main():
    frozen = read(R / 'config/frozen-detector.json');
    docs = read(R / 'datasets/H2-documents.json');
    chosen = []
    for task in ['hotpotqa', 'triviaqa', 'gov_report']: chosen.extend(
        [d for d in docs if d['task'] == task and d['split'] == 'test'][:3])
    results = []
    for j, doc in enumerate(chosen):
        ctxids = tokenize(doc['context'])[:2048];
        ctx = detokenize(ctxids);
        Ns = [5, 20] if j % 3 == 0 else [5]
        for N in Ns:
            def build(tools):
                return [request('<CONTEXT>\n' + t + '\n</CONTEXT>\nReplica ' + str(
                    i) + '; independently verify the same reference.') for i, t in enumerate(tools, 1)]

            stem = f"H2-evasion-{doc['task']}-{doc['id']}-N{N}"
            base = run_round(stem + '-none', build([ctx] * N),
                             {'phase': 'H2-evasion', 'model': 'llama', 'task': doc['task'], 'group': doc['group'],
                              'axis': 'baseline', 'label': 0, 'byte_divergent': False})
            for remaining in [0, 8, 16, 32, 64, 96, 128, 192, 256, 512, 1024, 2048]:
                r = min(remaining, len(ctxids));
                pos = len(ctxids) - r;
                pre = detokenize(ctxids[:pos]) if pos else '';
                post = detokenize(ctxids[pos:]) if r else '';
                assert pre + post == ctx, 'Token-slice roundtrip changed grounding'
                tools = [pre + '\u200b' * i + '\n' + post for i in range(1, N + 1)]
                row = run_round(stem + f'-uniform-{remaining}', build(tools),
                                {'phase': 'H2-evasion', 'model': 'llama', 'task': doc['task'], 'group': doc['group'],
                                 'axis': 'uniform', 'remaining_requested': remaining, 'remaining_context_tokens': r,
                                 'label': 1, 'byte_divergent': True, 'tool_returns_sha256': [digest(t) for t in tools],
                                 'baseline_key': base['key']})
                row['CAF_f01'] = row['cost_f01'] / base['cost_f01'];
                row['decision'] = decisions(frozen, row);
                row['frontier_feasible'] = all(
                    [row['decision']['floor_constraint'], row['decision']['mean_band_constraint'],
                     not row['decision']['detected_1pct']]);
                results.append(row)
            for k in (range(1, N) if N == 5 else [1, 5, 10, 19]):
                tools = [ctx] + [('\u200b' * i + '\n' + ctx if i <= k + 1 else ctx) for i in range(2, N + 1)]
                row = run_round(stem + f'-partial-{k}', build(tools),
                                {'phase': 'H2-evasion', 'model': 'llama', 'task': doc['task'], 'group': doc['group'],
                                 'axis': 'partial', 'k': k, 'label': 1, 'byte_divergent': True,
                                 'tool_returns_sha256': [digest(t) for t in tools], 'baseline_key': base['key']})
                row['CAF_f01'] = row['cost_f01'] / base['cost_f01'];
                row['decision'] = decisions(frozen, row);
                row['frontier_feasible'] = all(
                    [row['decision']['floor_constraint'], row['decision']['mean_band_constraint'],
                     not row['decision']['detected_1pct']]);
                results.append(row)
            save(R / 'data/ancillary/H2-evasion.json', results);
            save(R / 'evidence/stage2-progress.json',
                 {'stage': 'evasion', 'documents_done': j + 1, 'N': N, 'attack_rows': len(results)})
    save(R / 'evidence/H2-evasion-state.json', {'status': 'complete', 'rows': len(results),
                                                'detector_sha256': hashlib.sha256(
                                                    (R / 'config/frozen-detector.json').read_bytes()).hexdigest()});
    print('EVASION COMPLETE', len(results), flush=True)


if __name__ == '__main__': main()

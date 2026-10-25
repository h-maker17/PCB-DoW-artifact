from common import *
import math


def main():
    plan = read(B / 'B_cross_engine/datasets/replay-plan.json');
    rows = []
    for N in [5, 20]:
        majority = sum(math.comb(N, k) * .1 ** k * .9 ** (N - k) for k in range(N // 2 + 1, N + 1))
        if N % 2 == 0: majority += .5 * math.comb(N, N // 2) * .1 ** (N // 2) * .9 ** (N // 2)
        for layout in ['append', 'prepend']:
            for G in [50, 2000, 4000]:
                sources = [r for r in plan['runs'] if
                           r['phase'] == 'B3' and r['mode'] == 'broadcast' and r['N'] == N and r['g_tokens'] == G and r[
                               'layout'] == layout]
                assert len(sources) == 5
                for s in sources:
                    b = next(r for r in plan['runs'] if r['key'] == s['baseline_key']);
                    cost = lambda r: sum(w['vllm_prompt_tokens'] - .9 * w['vllm_cached_tokens'] for w in r['workers']);
                    assert abs(cost(s) / cost(b) - 1) < 1e-12
                hit = lambda r: sum(w['vllm_cached_tokens'] for w in r['workers'][1:]) / sum(
                    w['vllm_prompt_tokens'] for w in r['workers'][1:])
                rows.append({'N': N, 'G': G, 'layout': layout, 'broadcast_CAF_f01': 1.0,
                             'broadcast_hit_mean': statistics.mean(hit(s) for s in sources), 'independent_calls': N,
                             'broadcast_calls': 1, 'independent_calls_lost': N - 1,
                             'cache_evidence': 'archived E5, no H rerun',
                             'source_rounds': [s['source_run'] for s in sources], 'hypothetical_tool_error_p': .1,
                             'independent_majority_error_analytic': majority, 'broadcast_majority_error_analytic': .1,
                             'independence_scope': 'illustrative Bernoulli independent tool error model, not measured QA or evidence of this tool having random error'})
    save(R / 'data/ancillary/H3.json', rows);
    save(R / 'evidence/H3-state.json', {'status': 'complete', 'source_rounds': 60, 'new_gpu_requests': 0,
                                        'prefill_pin': 'optional, not implemented; no claimed pin-KV measurement'})


if __name__ == '__main__': main()

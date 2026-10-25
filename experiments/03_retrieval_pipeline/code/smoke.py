import asyncio, inspect
from common import *
from retrieval import RetrievalService
from pipeline import *


async def main():
    assert read(R / 'config/preregistration.json')['formal_authorized'] is False
    from importlib.metadata import version
    assert version('autogen-agentchat') == version('autogen-ext') == '0.7.5'
    docs = read(R / 'datasets/smoke-index/queries.json');
    doc = docs[0];
    history = read(R / 'datasets/shared-history.json')['text']
    cfg = read(R / 'config/preregistration.json');
    source_hash = digest((R / 'config/preregistration.json').read_bytes());
    results = []
    for mode in cfg['smoke']['generation_arms']: results.append(await run_round(mode, doc))
    base = results[0];
    neutral = []
    for r in results[1:]:
        CAF = r['costs']['0.1'] / base['costs']['0.1'];
        hitdiff = r['warm_hit'] - base['warm_hit'];
        assert r['unique_tool_return_bytes'] == 1 and abs(hitdiff) <= cfg['smoke'][
            'neutrality_hit_absolute_tolerance'] and cfg['smoke']['neutrality_CAF_interval'][0] <= CAF <= \
               cfg['smoke']['neutrality_CAF_interval'][1]
        neutral.append({'mode': r['mode'], 'hit': r['warm_hit'], 'CAF_f01': CAF, 'hit_difference': hitdiff,
                        'top_k_equal_to_benign': r['top_k_ids'] == base['top_k_ids']});
        assert neutral[-1]['top_k_equal_to_benign']
    assert base['warm_hit'] >= cfg['smoke']['benign_warm_hit_minimum']
    delivery = []
    for mode in cfg['smoke']['attack_delivery_only']:
        service = RetrievalService(mode, doc['question']);
        texts = [];
        ids = [];
        prefixes = []
        for replica in range(1, 6):
            returned = service.retrieve();
            system = assemble(returned, history);
            texts.append(system)
            tok = api('/tokenize', {'model': MODEL, 'messages': [{'role': 'system', 'content': system}, {'role': 'user',
                                                                                                         'content': doc[
                                                                                                                        'question'] + f'\nReplica number: {replica}'}]})[
                'tokens'];
            ids.append(tok)
            decoded = api('/detokenize', {'model': MODEL, 'tokens': tok})['prompt'];
            assert returned in decoded and history in decoded and decoded.index(returned) < decoded.index(history)
        for i in range(1, 5):
            char = lcp(texts[0], texts[i]);
            a = service.calls[0]['returned_text'];
            b = service.calls[i]['returned_text'];
            expected = len(HEADER) + lcp(a, b)
            assert char == expected and texts[0][:char].encode() == texts[i][:char].encode()
            assert HEADER + service.calls[i]['returned_text'] + SEPARATOR + history + POLICY == texts[i]
            prefixes.append(
                {'replica': i + 1, 'assembled_common_chars': char, 'expected_tool_return_divergence_chars': expected,
                 'prefix_bytes_equal': True, 'native_lcp_with_first': lcp(ids[0], ids[i]),
                 'native_divergence_token_context': ids[i][max(0, lcp(ids[0], ids[i]) - 4):lcp(ids[0], ids[i]) + 8]})
        assert len({digest(x['top_k_ids']) for x in service.calls}) == 1 and len(
            {x['returned_sha256'] for x in service.calls}) == 5
        rec = {'mode': mode, 'calls': service.calls, 'assembled_system_messages': texts, 'prompt_token_ids': ids,
               'delivery_checks': prefixes, 'generation_requests': 0,
               'tool_only_preflight_not_formal_attack_measurement': True}
        save(R / f'evidence/{mode}-delivery-preflight.json', rec);
        delivery.append({'mode': mode, 'top_k_constant': True, 'distinct_tool_return_bytes': 5,
                         'prompt_divergence_matches_tool_return': True, 'prefix_bytes_identical': True,
                         'generation_requests': 0})
    # Same query twice, independent embed + exact retrieval calls, rather than memoized retrieval.
    r = RetrievalService('benign', doc['question']);
    a = r.retrieve();
    b = r.retrieve();
    assert a == b and r.calls[0]['top_k_ids'] == r.calls[1]['top_k_ids'] and r.calls[0]['embedding_sha256'] == \
           r.calls[1]['embedding_sha256']
    save(R / 'evidence/retrieval-determinism.json',
         {'same_query_two_independent_calls': r.calls, 'byte_identical': True, 'top_k_order_identical': True,
          'embedding_bit_identical': True, 'no_query_memoization': True})
    for rec in read(R / 'datasets/smoke-index/provenance.json'): assert digest(Path(rec['path']).read_bytes()) == rec[
        'sha256']
    h = read(R / 'datasets/shared-history.json');
    assert digest(Path(h['source_path']).read_bytes()) == h['source_sha256']
    audit = {'passed': True, 'formal_started': False, 'formal_attack_model_requests': 0, 'new_model_requests': 15,
             'cold_first_replicas': 3, 'N': 5, 'query': doc['question'], 'task': doc['task'],
             'benign_warm_hit': base['warm_hit'], 'neutral_controls': neutral, 'tool_only_attack_delivery': delivery,
             'A2a_independent_calls': True, 'A2b_before_generation': True, 'A3_grounding_first': True,
             'retrieval_determinism': True, 'native_token_and_block_cache_counts_verified': True,
             'old_asset_hashes_unchanged': True, 'preregistration_sha256': source_hash,
             'source_hashes': {f: digest((R / 'code' / f).read_bytes()) for f in
                               ['retrieval.py', 'pipeline.py', 'smoke.py']},
             'stage': 'smoke-passed-awaiting-user-confirmation',
             'limits': ['one real QA query, three source contexts, not formal quality or generality evidence',
                        'shared history is controlled synthetic E5 S, not real-user conversation',
                        'tool-only nonce preflight is not a generated formal E-E2 attack',
                        'single pipeline does not measure deployment prevalence']}
    save(R / 'evidence/smoke-audit.json', audit);
    save(R / 'run_meta.json',
         {'stage': audit['stage'], 'instance': '<instance-id>', 'formal_authorized': False, 'protocol': cfg,
          'client_environment': read(R / 'evidence/client-environment.json'),
          'index': read(R / 'evidence/index-build.json'), 'service_launch': read(R / 'evidence/service-launch.json'),
          'audit': audit})
    print(json.dumps(audit, ensure_ascii=False, indent=2), flush=True)


if __name__ == '__main__':
    import traceback

    try:
        asyncio.run(main())
    except Exception as e:
        save(R / 'evidence/failure.json',
             {'error': str(e), 'traceback': traceback.format_exc(), 'formal_started': False});raise

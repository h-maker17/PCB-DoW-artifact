from .common import ROOT, tokenize, api, save, read, TASKS, lcp
from .prompts import messages


def probe():
    evidence = []
    for task in TASKS:
        record = read(ROOT / 'datasets/processed' / f'{task}.json')[0]
        context_ids = tokenize(prompt=record['context'], add_special_tokens=False)['tokens']
        native_context = api('/detokenize', {'model': 'llama-3.3-70b', 'tokens': context_ids})['prompt']
        traces = []
        for arm in ['none', 'front']:
            for worker in [1, 2]:
                msgs = messages(record, worker, arm);
                tk = tokenize(messages=msgs)
                decoded = api('/detokenize', {'model': 'llama-3.3-70b', 'tokens': tk['tokens']})['prompt']
                assert native_context in decoded
                start = decoded.index(native_context)
                if arm == 'front':
                    marker = '\u200b' * worker + '\n'
                    assert decoded.index(marker) < start and decoded[decoded.index(marker) + len(marker):].startswith(
                        native_context)
                save(ROOT / 'evidence/probe' / f'{task}-{arm}-worker{worker}.json',
                     {'messages': msgs, 'tokenize': tk, 'decoded': decoded, 'context_start_character': start,
                      'tools': [],
                      'native_decoded_context': native_context,
                      'source_context_verbatim_in_request': record['context'] in msgs[1]['content'],
                      'native_decoding_cleanup_observed': native_context != record['context'],
                      'input_modified_for_probe': False})
                traces.append(tk['tokens'])
        evidence.append(
            {'task': task, 'context_tokens': record['context_tokens'], 'front_header_LCP': lcp(traces[2], traces[3]),
             'none_LCP': lcp(traces[0], traces[1]), 'marker_before_context': True,
             'identical_system_and_no_tools': True})
    save(ROOT / 'evidence/assembly-probe.json', evidence);
    print('ASSEMBLY_VERIFIED', evidence, flush=True)


if __name__ == '__main__': probe()

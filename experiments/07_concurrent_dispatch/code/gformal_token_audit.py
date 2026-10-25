"""Readonly tokenizer audit of retained T30; zero model generations."""
import pathlib, json, os, sys, hashlib
from transformers import AutoTokenizer
from formal_core import read, save, canonical
from gcommon import prepare_arm, lcp


def audit(rev):
    rev = pathlib.Path(rev);
    docs = read(rev / 'data/documents.json');
    pilot = read(rev / 'data/pilot-documents.json');
    inputs = read(rev / 'data/inputs.json')
    if len(docs) != 30 or {d['id'] for d in docs} & {d['id'] for d in pilot}: raise ValueError(
        'Data counts or pilot overlap')
    tok = AutoTokenizer.from_pretrained(os.environ.get('PCB_LLAMA_TOKENIZER',
                                                       os.path.join(os.environ.get('PCB_MODEL_DIR', 'models'),
                                                                    'Llama-3.3-70B-Instruct-FP8')),
                                        local_files_only=True);
    rows = []
    for d in docs:
        if hashlib.sha256(d['G'].encode()).hexdigest() != d['G_sha256']: raise ValueError('G content differs')
        prepared = {a: prepare_arm(d, inputs['S'], inputs['markers'], 20, a) for a in ('D', 'A', 'B')};
        ids = {a: [tok.apply_chat_template(m, tokenize=True, add_generation_prompt=True) for m in v[0]] for a, v in
               prepared.items()}
        if len(prepared['B'][1]) != 1 or len(prepared['D'][1]) != 20 or len(prepared['A'][1]) != 20: raise ValueError(
            'Actual tool calls')
        if ids['D'] != ids['B'] or ids['A'][0] != ids['D'][0]: raise ValueError('D/B or A-first token mismatch')
        if any(len(a) != len(b) for a, b in zip(ids['A'], ids['D'])): raise ValueError(
            'Marker length increments not zero')
        if len({x['return'] for x in prepared['A'][1]}) != 20: raise ValueError('Marker/tool-return uniqueness')
        for a in ('D', 'A', 'B'):
            if prepare_arm(d, inputs['S'], inputs['markers'], 5, a)[0] != prepared[a][0][:5]: raise ValueError(
                'N5 not N20 prefix')
            # Scheduling never mutates messages; actual reinitialization for each S/P/C is checked.
            if any(prepare_arm(d, inputs['S'], inputs['markers'], 20, a)[0] != prepared[a][0] for _ in
                   ('S', 'P', 'C')): raise ValueError('Across-schedule payload differs')
        if any(len(x) + 16 > 8192 for arm in ids.values() for x in arm): raise ValueError(
            'Prompt+decode context overflow')
        rows.append(dict(document_id=d['id'], used_before=d.get('used_before'), G_sha256=d['G_sha256'],
                         full_prompt_tokens=[len(x) for x in ids['D']], D_B_exact=True, A_first_D_exact=True,
                         A_D_equal_length=True, N5_N20_prefix=True, A_S_P_C_equal=True,
                         actual_tool_calls={'D': 20, 'A': 20, 'B': 1},
                         A_D_LCP=[lcp(a, b) for a, b in zip(ids['A'], ids['D'])],
                         token_array_hashes={a: [canonical(x) for x in v] for a, v in ids.items()},
                         message_hashes={a: [canonical(m) for m in v[0]] for a, v in prepared.items()}))
    result = dict(passed=True, formal_documents=30, model='llama', tokenizer_type=type(tok).__name__,
                  chat_template_sha256=hashlib.sha256(tok.chat_template.encode()).hexdigest(),
                  data_sha256=hashlib.sha256((rev / 'data/documents.json').read_bytes()).hexdigest(),
                  inputs_sha256=hashlib.sha256((rev / 'data/inputs.json').read_bytes()).hexdigest(),
                  native_token_validation_required_on_each_formal_response=True, no_model_generation=True,
                  GPU_requests=0, rows=rows)
    save(rev / 'token-audit.json', result);
    return result


if __name__ == '__main__':
    r = audit(pathlib.Path(__file__).resolve().parent.parent);
    print(json.dumps({k: v for k, v in r.items() if k != 'rows'}, indent=2))

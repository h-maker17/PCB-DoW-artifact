"""Exact8ee30445 measurement/classification functions, no pipeline import."""
import re, json
from pathlib import Path

PROTOCOL = json.loads((Path(__file__).parent / "config/frozen_protocol.json").read_text(encoding="utf8"))


def locate(rendered, needle, offsets):
    starts = [m.start() for m in re.finditer(re.escape(needle), rendered)]
    if len(starts) != 1: return {'status': 'ambiguous', 'occurrences': len(starts)}
    start = starts[0];
    end = start + len(needle)
    tokens = [i for i, (a, b) in enumerate(offsets) if b > start and a < end and b > a]
    if not tokens: return {'status': 'ambiguous', 'reason': 'no token offsets'}
    return {'status': 'ok', 'chars': [start, end], 'tokens': [min(tokens), max(tokens) + 1]}


def token_lcp(sequences):
    limit = min(map(len, sequences))
    for i in range(limit):
        if len({seq[i] for seq in sequences}) != 1: return i
    return limit


def classify(benign, attacked):
    lb = benign['lcp'];
    la = attacked['lcp'];
    signed = lb - la
    result = {'L_benign': lb, 'L_attacked': la, 'signed_difference': signed, 'positive_loss': max(0, signed)}
    final = benign['final'];
    afinal = attacked['final']
    if any(x[z]['status'] != 'ok' for x in final + afinal for z in ['G', 'S']):
        return {**result, 'class': 'ambiguous', 'reason': 'G-split/missing/multiple or sentinel missing'}
    gs = [x['G']['tokens'] for x in final];
    ss = [x['S']['tokens'] for x in final]
    if any(g[0] < s[1] and s[0] < g[1] for g, s in zip(gs, ss)):
        return {**result, 'class': 'ambiguous', 'reason': 'G/S overlap'}
    if all(g[0] >= lb for g in gs):
        return {**result, 'class': 'immune', 'reason': 'G outside benign shared prefix (upstream divergence)'}
    if any(g[1] > lb for g in gs):
        return {**result, 'class': 'ambiguous',
                'reason': 'G only partially within benign shared prefix; cannot claim immunity'}
    if any(s[1] > lb for s in ss):
        return {**result, 'class': 'ambiguous', 'reason': 'shared history not fully shared in benign baseline'}
    if result['positive_loss'] == 0:
        return {**result, 'class': 'immune', 'reason': 'no positive paired shared-prefix loss'}
    if all(g[1] <= s[0] and g[0] - 1 <= la <= g[1] and la < s[0] for g, s in zip(gs, ss)):
        return {**result, 'class': 'full_collapse', 'reason': 'paired truncation in G before S'}
    if all(s[1] <= g[0] and g[0] - 1 <= la <= g[1] for g, s in zip(gs, ss)):
        return {**result, 'class': 'bounded_G', 'reason': 'paired truncation in G after complete S'}
    return {**result, 'class': 'ambiguous',
            'reason': 'truncation not in the measured G interval; possible template effect'}


def pair_integrity(b, a):
    def without_markers(data):
        if isinstance(data, str): return re.sub(PROTOCOL['marker_regex'], '', data)
        if isinstance(data, list): return [without_markers(x) for x in data]
        if isinstance(data, dict): return {k: without_markers(v) for k, v in data.items()}
        return data

    # Compare the complete actual LLM request payloads, not just the final requests.
    def per_replica(requests):
        return {i: [r for r in requests if r['replica'] == i] for i in range(PROTOCOL['N'])}

    # Preserve dict insertion order, since tool-schema serialization in the chat
    # template can depend on it. Archive and compare original HTTP body text too.
    return json.dumps(per_replica(b['requests']), ensure_ascii=False) == json.dumps(
        without_markers(per_replica(a['requests'])), ensure_ascii=False)


def repeat_invariant(result):
    return {k: v for k, v in result.items() if k != 'trace'}


def measure(ctx, tokenizer):
    n = PROTOCOL['N'];
    final = [];
    all_prompts = []
    raw_g = {r['raw'] for r in ctx.returns}
    g = next(iter(raw_g)) if len(raw_g) == 1 else None
    native_chunks = [r.get('raw_chunks') for r in ctx.returns if 'raw_chunks' in r]
    for i in range(n):
        requests = [r['body'] for r in ctx.requests if r['replica'] == i]
        if not requests: raise RuntimeError(f'Missing replica {i} LLM request')
        for j, req in enumerate(requests):
            text = tokenizer.apply_chat_template(req['messages'], tools=req.get('tools'), tokenize=False,
                                                 add_generation_prompt=True)
            enc = tokenizer(text, add_special_tokens=False, return_offsets_mapping=True)
            item = {'replica': i, 'request_round': j, 'messages': req['messages'], 'rendered': text,
                    'token_ids': enc['input_ids'], 'offsets': enc['offset_mapping'],
                    'G': locate(text, g, enc['offset_mapping']) if g else {'status': 'ambiguous',
                                                                           'reason': 'multiple benign G values'},
                    'S': locate(text, ctx.history, enc['offset_mapping'])}
            if native_chunks and item['G']['status'] != 'ok':
                chunk_sets = {tuple(c) for c in native_chunks}
                item['G']['native_chunk_locations'] = [locate(text, c, enc['offset_mapping']) for c in
                                                       next(iter(chunk_sets))] if len(chunk_sets) == 1 else []
                item['G'][
                    'reason'] = 'Native structured return was split/transformed/missing; no reconstructed prompt injected'
            all_prompts.append(item)
        final.append(item)
    return {'lcp': token_lcp([x['token_ids'] for x in final]), 'final': final, 'all_prompts': all_prompts}

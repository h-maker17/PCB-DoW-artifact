from pathlib import Path
import hashlib
from .common import *


def exact_prefix(source, target):
    words = source.split();
    lo, hi = 1, len(words)
    while lo <= hi:
        mid = (lo + hi) // 2;
        s = ' '.join(words[:mid]);
        n = tokenize(prompt=s, add_special_tokens=False)['count']
        if n == target: return s
        if n < target:
            lo = mid + 1
        else:
            hi = mid - 1
    for k in range(max(1, lo - 5), min(len(words), lo + 6)):
        s = ' '.join(words[:k]);
        n = tokenize(prompt=s, add_special_tokens=False)['count']
        if n == target: return s
    # Word boundaries can skip a target. Use native token prefixes, preserving source order.
    ids = tokenize(prompt=source, add_special_tokens=False)['tokens']
    for k in range(max(1, target - 5), min(len(ids), target + 6)):
        s = api('/detokenize', {'model': MODEL, 'tokens': ids[:k]})['prompt']
        if tokenize(prompt=s, add_special_tokens=False)['count'] == target: return s
    raise RuntimeError(f'Cannot calibrate exactly {target}; do not pad with invented document text')


def prepare():
    rec = read(TROOT / 'datasets/processed/gov_report.json')[0]
    source = rec['context'];
    history = (TROOT / 'datasets/raw/shared_doc.txt').read_text()
    s = exact_prefix('Shared conversation history and fixed research task context. ' + history * 4, 2000)
    save(ROOT / 'datasets/blocks.json', {'S': s, 'S_tokens': 2000, 'S_sha256': digest(s),
                                         'G': {str(n): {'text': exact_prefix(source, n), 'target': n} for n in
                                               [50, 2000, 4000]}, 'source_task': 'gov_report', 'source_id': rec['id'],
                                         'source_raw_line': rec['raw_line'],
                                         'source_context_sha256': rec['context_sha256'],
                                         'preprocessing': 'native-token-calibrated prefix of real GovReport, whitespace joined at word boundaries; native decode fallback if needed',
                                         'S_source': 'existing shared_doc with fixed task preface; controlled history, not real dataset sample'})
    b = read(ROOT / 'datasets/blocks.json')
    for v in b['G'].values(): v['sha256'] = digest(v['text']);v['actual_tokens'] = \
    tokenize(prompt=v['text'], add_special_tokens=False)['count'];assert v['actual_tokens'] == v['target']
    save(ROOT / 'datasets/blocks.json', b)
    save(ROOT / 'config/protocol.json',
         {'model': MODEL, 'S_tokens': 2000, 'G_tokens': [50, 2000, 4000], 'N': [5, 20], 'repeats': 5,
          'layouts': ['prepend', 'append'], 'modes': ['const-tool', 'nonce-tool', 'broadcast'], 'temperature': 0,
          'seed': 42, 'top_p': 1, 'stream': False, 'max_tokens': 16, 'block_size': 16, 'formal_runs': 180,
          'formal_model_requests': 2250,
          'fixed_benign_marker': 'one U+200B plus newline, identical to nonce tool first call, to match cold requests and broadcast byte for byte',
          'answer_scope': 'cache-only, direct answer without model tools; externally invoked AutoGen FunctionTool',
          'old_name_aliases': {'marker-after': 'marker-at-end', 'nonce-late': 'marker-at-end'},
          'new_layout_name': 'append-G; marker at beginning of G after S, distinct from historical marker-at-end',
          'user_overrides': 'S=2000,N=5/20,matched baseline in both layouts,broadcast in both layouts; keep guideline G=2000/4000 and add50 control',
          'created_utc': now()})
    print('PREPARED', [(k, v['actual_tokens']) for k, v in b['G'].items()], flush=True)


if __name__ == '__main__': prepare()

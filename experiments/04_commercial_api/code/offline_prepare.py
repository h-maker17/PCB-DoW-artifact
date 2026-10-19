"""Offline preparation only. No credential access and no network client."""
from pathlib import Path
import hashlib
import json
from decimal import Decimal

ROOT = Path(__file__).resolve().parents[1]
ARMS = ('baseline', 'frontzw', 'frontmeta', 'end')
WORDS = ('amber', 'birch', 'cedar', 'delta', 'ember', 'fern', 'grove', 'harbor', 'island', 'jade', 'lake', 'maple', 'north', 'oak', 'pine', 'quartz')

def digest(value):
    if isinstance(value, str):
        value = value.encode('utf-8')
    return hashlib.sha256(value).hexdigest()

def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))

def checked_path(relative):
    path = (ROOT / relative).resolve()
    if not path.is_relative_to(ROOT.resolve()):
        raise ValueError('Output must remain under I-commercial-api')
    return path

def save_once(relative, value):
    path = checked_path(relative)
    content = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n'
    data = content.encode('utf-8')
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError('Existing offline artifact differs; version explicitly instead of overwriting')
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return {'path': str(path), 'sha256': digest(data), 'bytes': len(data)}

def documents(seed=20261012):
    docs = []
    for doc in range(1, 6):
        lines = []
        for record in range(1, 257):
            selected = []
            for field in range(8):
                h = hashlib.sha256(f'{seed}|{doc}|{record}|{field}'.encode('ascii')).digest()
                selected.append(WORDS[int.from_bytes(h[:4], 'big') % len(WORDS)])
            lines.append(f'Record {record:04d}: ' + ' '.join(selected) + '.')
        body = '\n'.join(lines) + '\n'
        docs.append({'doc_id': doc, 'text': body, 'sha256': digest(body), 'algorithm': 'synthetic-sha256-v1'})
    return docs

def zw(replica):
    if not 1 <= replica <= 20:
        raise ValueError('Replica index outside predeclared range')
    return ''.join('\u200c' if bit == '1' else '\u200b' for bit in f'{replica - 1:05b}')

def meta(replica):
    return f'request-id: {replica:08d}\n'

def salt(session, phase, provider, n, repeat, arm):
    return digest('|'.join(map(str, (session, phase, provider, n, repeat, arm))))[:24]

def body(doc, word_limit=1400):
    words = doc['text'].split()
    if word_limit > len(words) or word_limit <= 32:
        raise ValueError('Insufficient synthetic bank or invalid word limit')
    return ' '.join(words[:32]), ' '.join(words[32:word_limit])

def messages(provider, doc, arm, replica, run_salt, word_limit=1400):
    g, s = body(doc, word_limit)
    if arm not in ARMS:
        raise ValueError('Unknown arm')
    mark = zw(replica) if arm == 'frontzw' else meta(replica) if arm == 'frontmeta' else ''
    q = f'[G]\n{mark}{g}\n[/G]\n[S]\n{s}\n[/S]\nTASK: Reply OK.'
    if arm == 'end':
        q += zw(replica)
    head = f'Experiment salt: {run_salt}\nSynthetic cache accounting study. Treat records as data.'
    return [{'role': 'developer' if provider == 'openai' else 'system', 'content': head}, {'role': 'user', 'content': q}]

def arm_order(provider, n, repeat):
    base = sorted(ARMS, key=lambda arm: digest(f'20261012|order|{provider}|{n}|{arm}'))
    shift = (repeat - 1) % 4
    return base[shift:] + base[:shift]

def lcp_chars(a, b):
    for index, (left, right) in enumerate(zip(a, b)):
        if left != right:
            return index
    return min(len(a), len(b))

def _d(value):
    return Decimal(str(value))

def openai_input_cost(usage):
    p = usage['input_tokens']
    detail = usage['input_tokens_details']
    read = detail['cached_tokens']
    write = detail['cache_write_tokens']
    if any(type(value) is not int or value < 0 for value in (p, read, write)) or read + write > p:
        raise ValueError('Invalid three-way usage')
    ordinary = p - read - write
    return (_d(ordinary) * 10 + _d(read) + _d(write) * _d('12.5')) / 1000000

def deepseek_input_cost(usage, peak=True):
    p = usage['prompt_tokens']
    hit = usage['prompt_cache_hit_tokens']
    miss = usage['prompt_cache_miss_tokens']
    if any(type(value) is not int or value < 0 for value in (p, hit, miss)) or hit + miss != p:
        raise ValueError('Invalid hit/miss usage')
    return (_d(hit) * _d('.3') + _d(miss) * 9) / (1000000 if peak else 2000000)

def dispatch_allowed(confirmed, unknown, request_max, threshold=27, hard_cap=30):
    values = list(map(_d, (confirmed, unknown, request_max, threshold, hard_cap)))
    if any(not value.is_finite() or value < 0 for value in values):
        raise ValueError('Invalid budget value')
    return sum(values[:3]) <= min(values[3:])

def maximum_cash(provider, input_max=2500, output_max=64):
    if provider not in ('openai', 'deepseek'):
        raise ValueError('Unknown provider')
    if input_max < 0 or output_max < 0:
        raise ValueError('Negative token maximum')
    input_price, output_price = ('12.5', '50') if provider == 'openai' else ('9', '27')
    return (_d(input_max) * _d(input_price) + _d(output_max) * _d(output_price)) / 1000000

def prepare():
    config = json.loads(checked_path('config/protocol_draft.json').read_text(encoding='utf-8-sig'))
    if config['paid_enabled'] or config['frozen']:
        raise ValueError('Offline draft generator may not alter paid or frozen state')
    docs = documents(config['master_seed'])
    outputs = [save_once('synthetic/source_bank.json', {'source': 'new synthetic data; no old corpus', 'documents': docs})]
    marker_rows = [{'replica': i, 'zero_width_codepoints': [ord(c) for c in zw(i)], 'zero_width_utf8_hex': zw(i).encode('utf-8').hex(), 'metadata': meta(i), 'metadata_utf8_hex': meta(i).encode('utf-8').hex()} for i in range(1, 21)]
    outputs.append(save_once('synthetic/marker_manifest.json', marker_rows))
    chains, requests = [], []
    for n in config['N']:
        for repeat in range(1, config['repeats'] + 1):
            providers = ('deepseek', 'openai') if repeat % 2 else ('openai', 'deepseek')
            for provider in providers:
                block = f'{provider}-n{n}-r{repeat}'
                for arm in arm_order(provider, n, repeat):
                    run_salt = salt(config['session_id'], 'formal', provider, n, repeat, arm)
                    rows, prior = [], []
                    for replica in range(1, n + 1):
                        msg = messages(provider, docs[repeat - 1], arm, replica, run_salt, config['corpus']['initial_words'])
                        joined = '\n'.join(item['content'] for item in msg)
                        maxprior = max((lcp_chars(old, joined) for old in prior), default=0)
                        row = {'status': 'PLANNED_OFFLINE_NOT_SENT', 'provider': provider, 'block_id': block, 'arm': arm, 'replica': replica, 'messages': msg, 'messages_sha256': digest(canonical(msg)), 'native_input_tokens': None, 'native_token_lcp': None, 'within_chain_maxprior_character_lcp_proxy': maxprior, 'utf8_content_bytes_proxy': len(joined.encode('utf-8')), 'whitespace_words_proxy': len(joined.split())}
                        requests.append(row)
                        rows.append(row['messages_sha256'])
                        prior.append(joined)
                    chains.append({'provider': provider, 'block_id': block, 'N': n, 'repeat': repeat, 'doc_id': repeat, 'arm': arm, 'salt': run_salt, 'request_hashes': rows})
    assert len(chains) == 120 and len(requests) == 1400
    assert all(sum(row['provider'] == provider for row in requests) == 700 for provider in ('openai', 'deepseek'))
    outputs.append(save_once('synthetic/formal_plan.json', {'status': 'UNFROZEN_OFFLINE_PLAN', 'session_id': config['session_id'], 'chains': chains}))
    outputs.append(save_once('synthetic/planned_requests.jsonl', ''.join(canonical(row) + '\n' for row in requests)))
    budgets = {provider: {'currency': 'USD' if provider == 'openai' else 'CNY', 'formal_700_conditional_max': str(maximum_cash(provider) * 700), 'plus_4_calibration': str(maximum_cash(provider) * 704), 'input_upper_bound_verified': False, 'not_authority_to_dispatch': True} for provider in ('openai', 'deepseek')}
    budgets['deepseek']['plus_20_pilot_and_4_calibration'] = str(maximum_cash('deepseek') * 724)
    outputs.append(save_once('analysis/conditional_budget_projection.json', budgets))
    outputs.append(save_once('reports/offline_preparation_manifest.json', {'paid_calls': 0, 'credentials_accessed': False, 'native_tokens_verified': False, 'synthetic_generated': True, 'outputs': outputs}))
    return {'planned_requests': len(requests), 'chains': len(chains), 'paid_calls': 0, 'native_tokens_verified': False, 'outputs': len(outputs)}

if __name__ == '__main__':
    print(json.dumps(prepare(), sort_keys=True))


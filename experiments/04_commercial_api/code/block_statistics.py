"""Complete finite matched blocks only; explicit scheduled-ID admission."""
import math
import random


def validate_blocks(blocks, expected_ids):
    if not blocks or len(expected_ids) != len(set(expected_ids)): raise ValueError('Invalid scheduled block inventory')
    ids = [b.get('block_id') for b in blocks]
    if len(ids) != len(set(ids)) or set(ids) != set(expected_ids): raise ValueError(
        'Missing or duplicate assigned block')
    for b in blocks:
        if b.get('complete') is not True: raise ValueError('Primary unavailable: incomplete assigned block')
        for field in ('baseline', 'attack'):
            value = b.get(field)
            if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(
                value) or value < 0: raise ValueError('Invalid finite cost')
    if sum(b['baseline'] for b in blocks) <= 0: raise ValueError('Undefined denominator')


def ratio_of_sums(blocks, expected_ids):
    validate_blocks(blocks, expected_ids)
    return sum(b['attack'] for b in blocks) / sum(b['baseline'] for b in blocks)


def paired_bootstrap(blocks, expected_ids, resamples, seed):
    validate_blocks(blocks, expected_ids)
    if type(resamples) is not int or resamples < 2: raise ValueError('Invalid resamples')
    rng = random.Random(seed);
    samples = []
    for _ in range(resamples):
        selected = [blocks[rng.randrange(len(blocks))] for _ in blocks]
        denominator = sum(b['baseline'] for b in selected)
        if denominator <= 0: raise ValueError('Bootstrap denominator zero; report unavailable')
        samples.append(sum(b['attack'] for b in selected) / denominator)
    samples.sort()
    return {'estimate': ratio_of_sums(blocks, expected_ids), 'percentile_low': samples[int(.025 * (resamples - 1))],
            'percentile_high': samples[int(.975 * (resamples - 1))], 'blocks': len(blocks),
            'exploratory': len(blocks) <= 5, 'resamples': resamples, 'unit': 'whole matched block'}


def primary_estimate(protocol, provider, blocks):
    if provider not in protocol['providers']: raise ValueError('Unknown provider')
    n = protocol['primary']['N']
    expected = [f'{provider}-n{n}-r{i}' for i in range(1, protocol['repeats'] + 1)]
    return ratio_of_sums(blocks, expected)

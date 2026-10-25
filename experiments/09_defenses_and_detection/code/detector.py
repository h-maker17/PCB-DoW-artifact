"""JSON-serializable joint detector, usable without ML packages on the server."""


def vector(row):
    f = row['features'];
    return [1 - f['mean_hit'], 1 - f['min_hit'], f['below_floor_count'] / f['reusing_replicas'], f['variance_hit']]


def tree_score(node, x):
    if 'probability' in node: return node['probability']
    return tree_score(node['left'] if x[node['feature']] <= node['threshold'] else node['right'], x)


def score(model, row):
    x = vector(row)
    if model['type'] == 'joint-OR': return max((a - m) / s for a, m, s in zip(x, model['medians'], model['scales']))
    if model['type'] == 'tree': return tree_score(model['root'], x)
    if model['type'] == 'single': return x[model['feature']]
    raise ValueError(model['type'])


def decisions(frozen, row):
    s = score(frozen['selected_model'], row);
    mean = row['features']['mean_hit'];
    floor = min(c / p for c, p in zip(row['cached'][1:], row['prompt'][1:]))
    return {'score': s, 'detected_1pct': s > frozen['joint_thresholds']['0.01'],
            'detected_5pct': s > frozen['joint_thresholds']['0.05'], 'floor_constraint': floor >= .95,
            'mean_band_constraint': frozen['mean_benign_band'][0] <= mean <= frozen['mean_benign_band'][1]}

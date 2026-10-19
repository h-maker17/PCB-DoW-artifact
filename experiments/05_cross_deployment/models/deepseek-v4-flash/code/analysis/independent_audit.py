from pathlib import Path
import collections
import csv
import hashlib
import json
import math
import statistics
import urllib.request

model = Path('/path/to/workspace/多智能体系统实验数据/fcb-dow-research/experiments/2026-10-04-deepseek-v4-rerun/models/deepseek-v4-flash')
session = Path((model / 'config/latest-session.txt').read_text().strip())
read = lambda p: json.loads(p.read_text())
index = read(session / 'run-index.json')
settings = read(session / 'session.json')
assert len(index) == 75 and settings['repeats'] == 5
assert '/code/service/server.py' in settings['command']
assert 'ssh-activation-audit' not in settings['command']
assert read(session / 'status.json')['phase'] == 'complete'
block = settings['block_size']
salts = []
rows = []
counts = collections.Counter()
requests = 0
empty_outputs = 0
reasoning_outputs = 0
finish_reasons = collections.Counter()

def lcp(left, right):
    for position, (a, b) in enumerate(zip(left, right)):
        if a != b:
            return position
    return min(len(left), len(right))

for key, relative in index.items():
    root = session / relative
    manifest = read(root / 'manifest.json')
    salts.append(manifest['cache_salt'])
    previous = []
    group = key.split('/')[0]
    counts[group] += 1
    n = int(key.split('/n')[1].split('/')[0]) if group == 'e2' else 5
    assert manifest['shared_tokens_calibrated_no_tools'] == 2000
    for worker in range(1, n + 1):
        stem = f'{worker:04d}-worker_{worker}'
        request = read(root / (stem + '.request.json'))
        response = read(root / (stem + '.response.json'))
        tokens = read(root / (stem + '.tokenize.json'))['tokens']
        assert tokens == response['prompt_token_ids']
        assert request['cache_salt'] == manifest['cache_salt']
        assert request['temperature'] == 0 and request['seed'] == 42
        assert request['max_tokens'] == 2048 and request['stream'] is False
        assert request['chat_template_kwargs']['thinking'] is False
        assert request['chat_template_kwargs']['enable_thinking'] is False
        usage = response['usage']
        cached = usage['prompt_tokens_details']['cached_tokens']
        assert usage['prompt_tokens'] == len(tokens)
        assert 0 <= cached <= len(tokens)
        if worker == 1:
            assert cached == 0
        message = response['choices'][0]['message']
        empty_outputs += int(not (message.get('content') or '').strip())
        reasoning_outputs += int(bool(message.get('reasoning') or message.get('reasoning_content')))
        assert usage.get('completion_tokens_details', {}).get('reasoning_tokens', 0) in (0, None)
        finish_reasons[response['choices'][0]['finish_reason']] += 1
        best = max((lcp(tokens, earlier) for earlier in previous), default=0)
        predicted = min(best, len(tokens) - 1) // block * block
        rows.append(dict(run=key, worker=worker, prompt_tokens=len(tokens),
                         cached_tokens=cached, max_lcp_prior_workers=best,
                         block_size=block, block_predicted_cached=predicted,
                         observed_minus_predicted=cached-predicted))
        previous.append(tokens)
        requests += 1

assert len(set(salts)) == 75 and requests == 555
assert counts == {'e1': 20, 'e2': 40, 'e5': 15}
assert reasoning_outputs == 0
tables = session / 'tables'
with (tables / 'block-alignment-audit.csv').open('w', newline='') as stream:
    writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
calibration = read(session / 'probe/calibration.json')
body_floor = calibration['H_actual_pre_marker_LCP_tokens'] / calibration['P_plus_s_full_prompt_tokens']
block_floor = (calibration['H_actual_pre_marker_LCP_tokens'] // block * block) / calibration['P_plus_s_full_prompt_tokens']
metrics_text = urllib.request.urlopen('http://127.0.0.1:8000/metrics', timeout=5).read().decode()
(session / 'metrics-after-formal.txt').write_text(metrics_text)
nan_lines = [line for line in metrics_text.splitlines()
             if line.startswith('vllm:corrupted_requests_total{')]
assert all(float(line.rsplit(' ', 1)[1]) == 0 for line in nan_lines)
summary = dict(formal_runs=75, formal_requests=requests, groups=dict(counts),
               independent_salts=75, all_first_workers_cold=True,
               all_actual_prompt_ids_match_tokenize=True,
               all_deterministic_settings_preserved=True,
               empty_model_contents=empty_outputs, reasoning_outputs=reasoning_outputs,
               finish_reasons=dict(finish_reasons),
               all_nonstreaming_and_2048_token_budgets_preserved=True,
               block_size=block, header_token_floor=body_floor,
               header_block_floor=block_floor,
               block_prediction_deviations=sum(row['observed_minus_predicted'] != 0 for row in rows),
               corrupted_request_metric=nan_lines,
               corrupted_metric_status='all exposed samples zero' if nan_lines else 'no counter sample exposed; no nonzero sample observed',
               primary_go_threshold_unchanged=True,
               diagnostic_hooks_absent_from_formal_service=True,
               caveat='Block prediction is a structural comparison; it does not replace the original Go criterion.')
(session / 'independent-audit.json').write_text(json.dumps(summary, indent=2))
print(json.dumps(summary, indent=2))

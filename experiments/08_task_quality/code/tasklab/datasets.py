"""Deterministic bounded real-data selection, independent of model outcomes."""
import json, re
from .common import ROOT, save, read, tokenize, digest, TASKS


def prepare():
    selected = {};
    exclusions = []
    for task in TASKS[:3]:
        raw = ROOT / 'datasets/raw' / f'{task}.jsonl';
        chosen = []
        for line_number, line in enumerate(raw.read_text(encoding='utf-8').splitlines(), 1):
            data = json.loads(line);
            context = data['context'];
            question = data['input']
            if task == 'triviaqa':
                match = re.fullmatch(r'Passage:\s*([\s\S]*?)\nQuestion:\s*([\s\S]*?)\nAnswer:\s*', question)
                if not match: raise RuntimeError('Unexpected TriviaQA target-input format; do not infer')
                context, question = match.groups()
            tk = tokenize(prompt=context, add_special_tokens=False);
            count = tk['count']
            if not 1024 <= count <= 29000:
                exclusions.append(dict(task=task, id=data['_id'], line=line_number, context_tokens=count,
                                       reason='predeclared context length outside [1024,29000]'));
                continue
            record = dict(task=task, id=data['_id'], raw_line=line_number, context=context, question=question,
                          answers=data['answers'], context_tokens=count, context_sha256=digest(context),
                          source='THUDM/LongBench ' + task,
                          preprocessing='extract target passage and question; discard answered demonstrations' if task == 'triviaqa' else 'context copied verbatim; no truncation')
            chosen.append(record)
            if len(chosen) == 30: break
        if len(chosen) != 30: raise RuntimeError(
            f'{task}: only {len(chosen)} eligible records; no fabricated replacements')
        selected[task] = chosen
    source = (ROOT / 'datasets/raw/shared_doc.txt').read_text().strip()

    # Match the existing C calibration: incremental native chat tokens, no tools.
    def count(text):
        return tokenize(messages=[{'role': 'user', 'content': text}])['count'] - \
            tokenize(messages=[{'role': 'user', 'content': ''}])['count']

    words = (source + '\n') * 8;
    words = words.split();
    lo, hi = 1, len(words);
    context = None
    while lo <= hi:
        mid = (lo + hi) // 2;
        candidate = ' '.join(words[:mid]);
        n = count(candidate)
        if n == 2000: context = candidate;break
        if n < 2000:
            lo = mid + 1
        else:
            hi = mid - 1
    if context is None:
        for k in range(max(1, lo - 5), min(len(words), hi + 6)):
            candidate = ' '.join(words[:k])
            if count(candidate) == 2000: context = candidate;break
    if context is None: raise RuntimeError('Cannot calibrate synthetic anchor exactly to 2000 native tokens')
    groups = [('planning', 1, ['goal', 'depend', 'plan', 'schedule', 'task']),
              ('verification', 6, ['claim', 'check', 'review', 'artifact', 'verify']),
              ('storage', 11, ['file', 'run', 'record', 'archiv', 'manifest']),
              ('scheduling', 16, ['coordinator', 'task', 'order', 'schedule', 'worker']),
              ('measurement', 21, ['token', 'cache', 'metric', 'measure', 'telemetry'])]
    chosen = []
    for i in range(30):
        topic, start, cues = groups[i % 5]
        chosen.append(dict(task='synthetic', id=f'anchor-{i + 1:03}', context=context,
                           question=f'Which records describe {topic} and what common rule do they use?', answers=[],
                           context_tokens=2000, context_sha256=digest(context),
                           expected_records=list(range(start, start + 5)), topic_terms=cues,
                           source='existing shared_doc; 5 fixed questions repeated 6 times with independent salts',
                           preprocessing='native incremental chat calibration to 2000 tokens'))
    selected['synthetic'] = chosen
    for task, records in selected.items(): save(ROOT / 'datasets/processed' / f'{task}.json', records)
    save(ROOT / 'datasets/selection-audit.json', {
        'rule': 'first 30 records in file order meeting native context token length [1024,29000], without generation-based selection',
        'real_task_counts': {t: len(selected[t]) for t in TASKS[:3]}, 'excluded': exclusions,
        'synthetic_note': '30 independent repeated anchor cases, not 30 distinct real tasks'})
    print('DATASETS_PREPARED', {k: len(v) for k, v in selected.items()}, flush=True)


if __name__ == '__main__': prepare()

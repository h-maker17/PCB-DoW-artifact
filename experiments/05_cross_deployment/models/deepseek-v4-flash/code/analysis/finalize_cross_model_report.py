from pathlib import Path
import json, csv, collections, datetime, shutil

m = Path(__file__).resolve().parents[2]
project = m.parents[1]
s = Path((m / 'config/latest-session.txt').read_text().strip())
read = lambda p: json.loads(p.read_text())
a = read(s / 'independent-audit.json')
assert a['formal_runs'] == 75 and a['formal_requests'] == 555
with (s / 'tables/cross-model-summary.csv').open() as f: summary = next(csv.DictReader(f))
central = project / 'tables/cross-model-summary.csv'
backup = m / 'evidence' / ('cross-model-summary-before-final-' + s.name + '.csv')
if not backup.exists(): shutil.copyfile(central, backup)
with backup.open() as f:
    reader = csv.DictReader(f);
    fields = reader.fieldnames;
    prior = list(reader)
others = [r for r in prior if r['model'] != summary['model']]
rows = others + [summary]
fields = list(dict.fromkeys(fields + list(summary)))
for dest in [central, s / 'tables/cross-model-summary-five-models.csv',
             m / 'tables/cross-model-summary-five-models.csv']:
    with dest.open('w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=fields);
        w.writeheader();
        w.writerows(rows)
print(json.dumps(
    {'session': s.name, 'audit': a, 'summary_model': summary.get('model'), 'other_model_rows_preserved': len(others)},
    ensure_ascii=False))

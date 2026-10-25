"""Authorized sequence B1(both) -> B2(both) -> B3. Stops on any error."""
from pathlib import Path
import sys, json, subprocess, time, os, traceback

sys.path.insert(0, str(Path(__file__).resolve().parent))
from engine import R, ensure, save
from replay import read, run_cell


def main():
    assert read(R / 'config/preregistration.json')['approved']
    plan = read(R / 'datasets/replay-plan.json')['runs']
    meta = read(R / 'run_meta.json');
    meta.update(stage='formal-running', formal_experiments_started=True,
                formal_approval='config/stage-gate-approved.md',
                isolation='flush_cache + per-run extra_key; no token salts', B2_parity_tolerance=.05)
    save(R / 'run_meta.json', meta)
    for phase, model in [('B1', 'qwen'), ('B1', 'llama'), ('B2', 'qwen'), ('B2', 'llama'), ('B3', 'llama')]:
        part = [r for r in plan if r['phase'] == phase and r['model'] == model]
        remaining = [r for r in part if not (R / 'data/formal/page1' / r['key'] / 'result.json').exists()]
        if not remaining: continue
        ensure(model, 1)
        save(R / 'evidence/formal-state.json',
             {'status': 'running', 'phase': phase, 'model': model, 'updated_unix': time.time()})
        for r in sorted(remaining, key=lambda x: (x['repeat'], x['key'])): run_cell(r)
        print('COMPLETE', phase, model, len(part), flush=True)
    save(R / 'evidence/formal-state.json',
         {'status': 'B1-B3-complete', 'runs': 300, 'requests': 3210, 'updated_unix': time.time()})
    meta = read(R / 'run_meta.json');
    meta['stage'] = 'formal-complete-awaiting-analysis';
    save(R / 'run_meta.json', meta)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        save(R / 'evidence/formal-state.json', {'status': 'failed', 'error': str(exc), 'updated_unix': time.time()});
        traceback.print_exc();
        raise

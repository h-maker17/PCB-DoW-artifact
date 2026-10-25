"""Resumable DeepSeek workflow: mandatory probe -> E1 -> E2 -> E5 -> report."""
import argparse
import asyncio
import fcntl
import sys
from pathlib import Path

CODE = Path(__file__).resolve().parents[1]
for folder in ('runtime', 'probe', 'analysis'):
    sys.path.insert(0, str(CODE / folder))
from experiment_common import ROOT, new_session, run_group, save, now
from calibrate_probe import probe
from report_baseline import report


async def execute(session):
    try:
        await probe(session)
        for group in ('e1', 'e2', 'e5'):
            await run_group(session, group)
        report(session)
    except BaseException as exc:
        save(session / 'failure.json', dict(error=repr(exc), failed_utc=now(),
                                            policy='inspect raw evidence; do not alter injection to force Go'))
        raise


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('phase', choices=('init', 'run', 'report'))
    p.add_argument('--session', type=Path)
    a = p.parse_args()
    if a.phase == 'init':
        print(new_session())
    elif a.session is None:
        p.error('--session required')
    elif a.phase == 'report':
        report(a.session)
    else:
        lock = (ROOT / 'evidence/baseline.lock').open('a+')
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        asyncio.run(execute(a.session))

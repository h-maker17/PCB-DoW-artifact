"""Reviewed outer process capture; interactive input is never piped or logged."""
from pathlib import Path
from datetime import datetime, timezone
import argparse, hashlib, json, os, subprocess, sys, threading, uuid
import calibration_v2 as entry


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--calibration-review', required=True)
    args = parser.parse_args(argv)
    if not entry.trusted_tty(): raise entry.Stop('TRUSTED_LOCAL_TTY_REQUIRED')
    # Permission/budget/calendar are checked before spawning or key input.
    gate = entry.Authority(entry.ROOT, args.calibration_review)
    try:
        state = gate.state()
        if state['technical_calibration_completed']:
            print(entry.core.canonical({'already_complete': True, 'new_requests': 0, 'formal_enabled': False}));
            return 0
        gate.preflight(state['next_id'])
        source_hashes = gate.source_hashes;
        review_hash = gate.review_hash
    finally:
        gate.close()
    run_id = 'CALV2-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex
    directory = entry.ROOT / 'analysis/calibration_v2_runs' / run_id
    # Parent ACL is provisioned and verified before the production grant.
    if not directory.parent.is_dir(): raise entry.Stop('RESTRICTED_RAW_DIRECTORY_REQUIRED')
    directory.mkdir(exist_ok=False)
    command = [sys.executable, '-B', str(entry.ROOT / 'code/calibration_v2.py'), '--calibration-review',
               args.calibration_review, '--execute', '--capture-run-id', run_id]
    entry.save(directory / 'capture_launch.json',
               {'run_id': run_id, 'command': command, 'launcher_pid': os.getpid(), 'launch_started_utc': entry.utc(),
                'source_hashes': source_hashes, 'review_sha256': review_hash, 'input_capture': False,
                'authenticity_limit': 'Actual Popen process metadata and byte capture; local owner/administrator can still modify files. Hashes do not establish independent authenticity.'})
    process = subprocess.Popen(command, cwd=entry.ROOT, stdin=None, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               bufsize=0)
    entry.save(directory / 'capture_process.json',
               {'pid': process.pid, 'process_created_observed_utc': entry.utc(), 'command': command,
                'stdin_inherited_real_console': True, 'stdout_stderr_captured': True})
    capture_failure = threading.Event()

    def pump(source, path, destination):
        try:
            with path.open('xb') as saved:
                while True:
                    data = source.read(4096)
                    if not data: break
                    saved.write(data);
                    saved.flush()
                    destination.buffer.write(data);
                    destination.buffer.flush()
                os.fsync(saved.fileno())
        except BaseException:
            capture_failure.set()
            if process.poll() is None: process.terminate()

    threads = [threading.Thread(target=pump, args=(process.stdout, directory / 'stdout.bin', sys.stdout)),
               threading.Thread(target=pump, args=(process.stderr, directory / 'stderr.bin', sys.stderr))]
    for t in threads: t.start()
    try:
        code = process.wait()
    except BaseException:
        # Stop this exact child so it cannot continue the loop unattended.
        # A killed in-flight attempt retains its complete durable reservation;
        # termination is never evidence of no charge and never enables retry.
        process.terminate()
        code = process.wait(timeout=10)
        for t in threads: t.join(timeout=10)
        entry.save(directory / 'capture_interrupted.json',
                   {'observed_utc': entry.utc(), 'pid': process.pid, 'exit_code': code,
                    'reason': 'OUTER_CAPTURE_INTERRUPTED_CHILD_STOPPED_NO_RETRY', 'charge_status_not_inferred': True})
        raise
    for t in threads: t.join()
    entry.save(directory / 'capture_exit.json', {'pid': process.pid, 'exit_code': code, 'ended_utc': entry.utc(),
                                                 'stdout_sha256': entry.digest(directory / 'stdout.bin') if (
                                                             directory / 'stdout.bin').is_file() else None,
                                                 'stderr_sha256': entry.digest(directory / 'stderr.bin') if (
                                                             directory / 'stderr.bin').is_file() else None,
                                                 'capture_storage_failure': capture_failure.is_set(),
                                                 'actual_capture_not_fixture': True})
    print(entry.core.canonical({'run_id': run_id, 'pid': process.pid, 'exit_code': code, 'artifacts': str(directory),
                                'formal_enabled': False}), flush=True)
    return 3 if capture_failure.is_set() else code


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except entry.Stop as exc:
        print(entry.core.canonical({'status': 'BLOCKED_BEFORE_PROCESS_OR_KEY', 'error_code': str(exc)}),
              file=sys.stderr);
        raise SystemExit(2)

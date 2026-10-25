"""Formal GPU entry. Inactive/mismatched approval rejects before any process."""
import pathlib, os, sys, subprocess, socket, time, signal, tempfile, shutil, urllib.request, json
from formal_core import gate, verify_source, read, save, append, utc, sha, Ledger, logical, verify_resume
from gcommon import ROOT
from gchecks import require_disk, environment_snapshot
from gformal_monitor import verify_window, ResourceMonitor

REV = pathlib.Path(__file__).resolve().parent.parent


def stop_owned(proc, monitor):
    if monitor is None: raise RuntimeError('No pinned service identity; cleanup requires review')
    signals = monitor.signal_owned(signal.SIGTERM)
    deadline = time.monotonic() + 30
    while monitor.cleanup_members() and time.monotonic() < deadline: time.sleep(.1)
    if monitor.cleanup_members():
        signals += monitor.signal_owned(signal.SIGKILL);
        deadline = time.monotonic() + 10
        while monitor.cleanup_members() and time.monotonic() < deadline: time.sleep(.1)
    remaining = monitor.cleanup_members()
    if remaining: raise RuntimeError('Owned service workers remain after cleanup')
    proc.wait(timeout=5)
    return dict(all_owned_members_gone=True, signals=signals)


def resource_guard(m, preflight=False):
    require_disk(ROOT, 30 if preflight else 20);
    require_disk('/tmp', 30 if preflight else 20)
    gpu = subprocess.check_output(['nvidia-smi', '--query-gpu=uuid', '--format=csv,noheader'],
                                  text=True).strip().splitlines()
    if gpu != [m['GPU_UUID']]: raise ValueError('GPU identity/count differs')
    if preflight:
        if subprocess.check_output(['nvidia-smi', '--query-compute-apps=pid', '--format=csv,noheader'],
                                   text=True).strip(): raise ValueError('GPU occupied; no formal')
        s = socket.socket()
        try:
            s.bind(('127.0.0.1', 18021))
        finally:
            s.close()


def main(approval_path, segment):
    m = gate(REV, approval_path);
    verify_window(m);
    source = verify_source(REV);
    root = ROOT / 'formal' / m['experiment_id'];
    a = read(approval_path)
    if not segment.startswith('formal-') or '/' in segment or '\\' in segment: raise ValueError('Unsafe segment')
    ledger = Ledger(root, m['experiment_id'], read(REV / 'schedule.json'), source);
    ledger.plan()
    if ledger.unfinished(): raise ValueError('Unfinished formal attempt; recovery review required before model')
    verify_resume(ledger, a.get('resume_review'))
    if ledger.counts()['unattempted_original_rounds'] == 0 and not a.get('retry_decisions'): return {
        'already_completed': True, 'Popen_calls': 0}
    preflight = resource_guard(m, True)
    folder = root / 'segments' / segment;
    folder.mkdir(parents=True);
    (folder / 'traces').mkdir();
    (folder / 'patch').mkdir();
    flag = folder / 'trace.flag';
    flag.write_text('1');
    py = ROOT / 'env/g-venv/bin/python';
    code = REV / 'code'
    save(folder / 'resource-preflight.json', preflight)
    # Exact fixed Llama argv inherited from the actual reviewed pilot, new wrapper path only.
    reference = read(REV / 'reference-Llama-config.json');
    args = read(REV / 'server-config.json')['argv']
    index = reference['argv'].index(next(x for x in reference['argv'] if x.endswith('/gserver.py')))
    if args[index] != str(code / 'gserver.py') or args[:index] + args[index + 1:] != reference['argv'][:index] + \
            reference['argv'][index + 1:]: raise ValueError('Formal engine argv differs from reviewed pilot')
    if args[0] != str(py) or args[-1] != '42' or 'G-llama' not in args: raise ValueError('Reviewed launch argv differs')
    if environment_snapshot()['dependency_lock_sha256'] != m['engine_dependency_lock_sha256']: raise ValueError(
        'Formal dependency metadata differs')
    if (ROOT / 'checksums/model-manifest.sha256').read_text().strip() != m['model_manifest_sha256']: raise ValueError(
        'Readonly model manifest differs')
    ipc = pathlib.Path(tempfile.mkdtemp(prefix='g-formal-', dir='/tmp'))
    env = dict(os.environ, G_ROOT=str(ROOT), G_FORMAL_APPROVAL=str(approval_path), G_TRACE_ENABLED='1',
               G_TRACE_DIR=str(folder / 'traces'), G_TRACE_FLAG=str(flag), G_PATCH_EVIDENCE_DIR=str(folder / 'patch'),
               PYTHONDONTWRITEBYTECODE='1', HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
               HF_HOME=str(ROOT / 'cache/hf'), XDG_CACHE_HOME=str(ROOT / 'cache/xdg'),
               VLLM_CACHE_ROOT=str(ROOT / 'cache/vllm'), TRITON_CACHE_DIR=str(ROOT / 'cache/triton'),
               TORCHINDUCTOR_CACHE_DIR=str(ROOT / 'cache/torchinductor'), CUDA_CACHE_PATH=str(ROOT / 'cache/cuda'),
               TMPDIR=str(ipc), VLLM_RPC_BASE_PATH=str(ipc), CUDA_VISIBLE_DEVICES='0',
               VLLM_WORKER_MULTIPROC_METHOD='spawn', VLLM_USE_V1='1', VLLM_SERVER_DEV_MODE='1',
               VLLM_HTTP_TIMEOUT_KEEP_ALIVE='600', PYTHONPATH=str(code) + os.pathsep + str(ROOT / 'code/vendor'))
    save(folder / 'server-config.json',
         dict(args=args, reviewed_reference_argv_sha256=m['server_config_sha256'], source_revision=source,
              environment_overrides={k: env[k] for k in
                                     ('G_TRACE_DIR', 'G_PATCH_EVIDENCE_DIR', 'VLLM_HTTP_TIMEOUT_KEEP_ALIVE',
                                      'CUDA_VISIBLE_DEVICES', 'VLLM_USE_V1')}, dependencies=environment_snapshot()))
    started = time.monotonic();
    startutc = utc();
    proc = None;
    driver = None;
    sampler = None;
    monitor = None;
    exitcode = None
    try:
        sampler = subprocess.Popen([str(py), '-B', str(code / 'gformal_sample.py'), str(folder)], cwd=ROOT, env=env,
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with (folder / 'server.log').open('xb') as f:
            proc = subprocess.Popen(args, cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=f,
                                    stderr=subprocess.STDOUT, start_new_session=True)
        monitor = ResourceMonitor(m, folder, proc.pid)
        save(folder / 'owned-service.json',
             dict(pid=proc.pid, pgid=proc.pid, source_revision=source, experiment_id=m['experiment_id'],
                  segment=segment, identity=monitor.owner, exclusive_runtime_window=m['exclusive_runtime_window']))
        deadline = time.monotonic() + 900
        while time.monotonic() < deadline:
            if proc.poll() is not None: raise RuntimeError('Formal service exited on startup')
            resource_guard(m)
            monitor.check('starting')
            try:
                with urllib.request.urlopen('http://127.0.0.1:18021/health', timeout=2) as r:
                    if r.status == 200: break
            except Exception:
                pass
            time.sleep(1)
        else:
            raise RuntimeError('Startup900s timeout')
        with urllib.request.urlopen('http://127.0.0.1:18021/v1/models', timeout=5) as r:
            models = json.load(r)
        if models['data'][0]['id'] != 'G-llama': raise ValueError('Wrong model service')
        save(folder / 'ready.json', dict(ready_utc=utc(), startup_seconds=time.monotonic() - started, models=models))
        resource_guard(m)
        monitor.check('pre_driver')
        with (folder / 'driver.log').open('xb') as f:
            driver = subprocess.Popen([str(py), '-B', str(code / 'gformal_backend.py'), segment], cwd=ROOT,
                                      env=dict(env, G_TRACE_ENABLED='0'), stdin=subprocess.DEVNULL, stdout=f,
                                      stderr=subprocess.STDOUT)
        while driver.poll() is None:
            resource_guard(m)
            monitor.check('running')
            if proc.poll() is not None: raise RuntimeError('Formal service died while driver active')
            time.sleep(1)
        exitcode = driver.returncode
        if exitcode: raise RuntimeError('Formal driver exit' + str(exitcode))
        try:
            resource_guard(m);
            final = monitor.check('post_driver')
            if proc.poll() is not None: raise RuntimeError('Formal service died at final completion check')
            save(folder / 'resource-final-check.json',
                 dict(passed=True, observation=final, driver_exit=exitcode, checked_utc=utc()))
        except BaseException as exc:
            save(folder / 'resource-final-check.json',
                 dict(passed=False, type=type(exc).__name__, message=str(exc), driver_exit=exitcode, checked_utc=utc()))
            raise
        save(folder / 'stage-completed.json', dict(driver_exit=exitcode, finished_utc=utc(), formal=True,
                                                   final_check_sha256=sha(folder / 'resource-final-check.json')))
    except BaseException as exc:
        save(folder / 'stage-failure.json',
             dict(type=type(exc).__name__, message=str(exc), finished_utc=utc(), driver_exit=exitcode))
        save(folder / 'stage-review-pause.json',
             dict(reason=str(exc), requires_review=True, no_automatic_resume=True, external_occupancy_not_inferred=True,
                  resource_monitor_pause=(folder / 'resource-monitor-pause.json').exists()))
        raise
    finally:
        errors = [];
        service_cleanup = None;
        closed = False;
        idle = False

        def attempt(label, action):
            try:
                return action()
            except BaseException as exc:
                errors.append(dict(stage=label, type=type(exc).__name__, message=str(exc)));return None

        def stop_driver():
            if driver is not None and driver.poll() is None:
                driver.terminate()
                try:
                    driver.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    driver.kill();driver.wait(timeout=5)

        def stop_sampler():
            (folder / 'sampler.stop').write_text('stop')
            if sampler is not None:
                try:
                    sampler.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    sampler.terminate();sampler.wait(timeout=5)

        def port_closed():
            s = socket.socket();
            s.settimeout(1)
            try:
                return s.connect_ex(('127.0.0.1', 18021)) != 0
            finally:
                s.close()

        attempt('driver', stop_driver)
        if proc is not None: service_cleanup = attempt('service', lambda: stop_owned(proc, monitor))
        attempt('sampler', stop_sampler)
        closed = attempt('port', port_closed) is True
        idle = attempt('GPU', lambda: not subprocess.check_output(
            ['nvidia-smi', '--query-compute-apps=pid', '--format=csv,noheader'], text=True).strip()) is True
        record = dict(experiment_id=m['experiment_id'], source_revision=source, segment=segment, start_utc=startutc,
                      end_utc=utc(), elapsed_seconds=time.monotonic() - started, rate_RMB_per_hour=6,
                      actual_cloud_billing_start_unknown=True, driver_exit=exitcode, G_service_stopped=(
                        proc is None or bool(service_cleanup and service_cleanup.get('all_owned_members_gone'))),
                      own_port_closed=closed, owned_GPU_idle=idle, cleanup_errors=errors,
                      service_cleanup=service_cleanup,
                      pinned_cleanup_identities=list(getattr(monitor, 'cleanup_pins', {}).values()))
        record['estimated_RMB'] = record['elapsed_seconds'] / 600;
        save(folder / 'cleanup-cost.json', record)
        if errors or not record['G_service_stopped'] or not closed or not idle:
            save(folder / 'cleanup-review-pause.json',
                 dict(cleanup=record, requires_review=True, no_automatic_resume=True))
            raise RuntimeError('Incomplete owned cleanup; preserved evidence requires review')
        if ipc.parent == pathlib.Path('/tmp') and ipc.name.startswith('g-formal-'): shutil.rmtree(ipc)
    return read(folder / 'completion.json')


if __name__ == '__main__': print(json.dumps(main(pathlib.Path(sys.argv[1]), sys.argv[2]), indent=2))

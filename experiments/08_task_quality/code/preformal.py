from pathlib import Path
import subprocess, os, json, traceback

r = Path(__file__).resolve().parents[1];
env = os.environ.copy();
env['PYTHONPATH'] = str(r / 'code');
py = os.environ.get('PCB_CLIENT_PYTHON', str(r / 'env/client/bin/python'))
try:
    for module, args in [('test_metrics', []), ('probe', []), ('runner', ['sanity'])]:
        subprocess.run([py, '-u', '-m', 'tasklab.' + module] + args, env=env, check=True)
except Exception as e:
    (r / 'evidence/preformal-failure.json').write_text(
        json.dumps({'error': str(e), 'traceback': traceback.format_exc()}));
    raise

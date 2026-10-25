"""Stop only this experiment's recorded server process group."""
import json, os, signal, time, subprocess
from pathlib import Path

r = Path(__file__).resolve().parents[2]
p = r / 'evidence/service-process.json'
c = json.loads(p.read_text());
pid = c['pid']
proc = Path('/proc') / str(pid)
if not proc.exists(): print('SERVICE_NOT_RUNNING', pid);raise SystemExit(0)
cmd = (proc / 'cmdline').read_bytes().replace(b'\0', b' ').decode(errors='replace')
if str(r / 'code/service/server.py') not in cmd:
    raise RuntimeError('Recorded PID does not match this experiment server; refuse to stop')
if os.getpgid(pid) != pid: raise RuntimeError('Service group identity does not match recorded PID')
os.killpg(pid, signal.SIGTERM)
for _ in range(90):
    if not proc.exists() or (proc / 'stat').read_text().split(')')[1].strip().startswith('Z'): break
    time.sleep(.5)
else:
    raise RuntimeError('Service did not stop within 45 seconds; inspect before next model')
print('SERVICE_STOPPED', pid)
print(subprocess.check_output(['nvidia-smi', '--query-compute-apps=pid,used_memory', '--format=csv'], text=True))

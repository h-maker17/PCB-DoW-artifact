import sys,time,subprocess,pathlib
from formal_core import append,utc
folder=pathlib.Path(sys.argv[1]);stop=folder/'sampler.stop'
while not stop.exists():
 t=time.monotonic();raw=subprocess.check_output(['nvidia-smi','--query-gpu=uuid,memory.used,memory.free,utilization.gpu,utilization.memory,temperature.gpu','--format=csv,noheader,nounits'],text=True);x=[a.strip() for a in raw.strip().split(',')]
 append(folder/'gpu-samples.jsonl',dict(utc=utc(),monotonic=t,uuid=x[0],memory_used_MiB=int(x[1]),memory_free_MiB=int(x[2]),SM_utilization_percent=int(x[3]),memory_utilization_percent=int(x[4]),temperature_C=int(x[5]),nominal_sample_period_seconds=.5))
 time.sleep(max(0,.5-(time.monotonic()-t)))

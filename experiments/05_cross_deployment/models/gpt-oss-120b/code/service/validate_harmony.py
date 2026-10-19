import hashlib,json,os,subprocess
from pathlib import Path
r=Path('/path/to/workspace/多智能体系统实验数据/fcb-dow-research/experiments/2026-10-03-c-remaining3-models/models/gpt-oss-120b')
p=r/'cache/harmony';data=(p/'o200k_base.tiktoken').read_bytes()
url='https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken'
(p/hashlib.sha1(url.encode()).hexdigest()).write_bytes(data)
cfg=json.loads((r/'config/model.json').read_text())
env=dict(os.environ,TIKTOKEN_RS_CACHE_DIR=str(p),HTTPS_PROXY='http://127.0.0.1:9',HTTP_PROXY='http://127.0.0.1:9',ALL_PROXY='http://127.0.0.1:9',NO_PROXY='')
code="from openai_harmony import load_harmony_encoding,HarmonyEncodingName; e=load_harmony_encoding(HarmonyEncodingName.HARMONY_GPT_OSS);print('OFFLINE_HARMONY_ENCODING_OK',e.encode('ready'))"
proc=subprocess.run([cfg['gpu_python'],'-c',code],env=env,capture_output=True,text=True,timeout=25)
e=dict(command=[cfg['gpu_python'],'-c',code],env_cache=str(p),network_proxies='closed loopback port 9',exit_code=proc.returncode,stdout=proc.stdout,stderr=proc.stderr)
(r/'evidence/harmony-offline-load-check.json').write_text(json.dumps(e,indent=2))
print(proc.returncode,proc.stdout,proc.stderr,flush=True)
if proc.returncode:raise RuntimeError('Offline native encoding cache load failed')

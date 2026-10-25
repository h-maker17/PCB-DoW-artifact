import hashlib, json, os, subprocess
from pathlib import Path

r = Path(__file__).resolve().parents[2]
p = r / 'cache/harmony';
data = (p / 'o200k_base.tiktoken').read_bytes()
url = 'https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken'
(p / hashlib.sha1(url.encode()).hexdigest()).write_bytes(data)
import os

_PATH_KEYS = ('model_path', 'gpu_python', 'client_python', 'batch_root', 'project_root', 'resource_environment_root',
              'cuda_toolkit_root', 'cuda_link_library_dir', 'cuda_supplemental_include_dir', 'cuda_vendor_library_dir',
              'local_kernel_cache_dir')


def _resolve_profile(cfg, root):
    """Expand ${VAR} references; resolve relative path settings against the model folder."""
    out = dict(cfg)
    for k in _PATH_KEYS:
        v = out.get(k)
        if isinstance(v, str):
            v = os.path.expandvars(v)
            if not v.startswith('$') and not os.path.isabs(v):
                v = str((root / v).resolve())
            out[k] = v
    return out


cfg = _resolve_profile(json.loads((r / 'config/model.json').read_text()), r)
env = dict(os.environ, TIKTOKEN_RS_CACHE_DIR=str(p), HTTPS_PROXY='http://127.0.0.1:9', HTTP_PROXY='http://127.0.0.1:9',
           ALL_PROXY='http://127.0.0.1:9', NO_PROXY='')
code = "from openai_harmony import load_harmony_encoding,HarmonyEncodingName; e=load_harmony_encoding(HarmonyEncodingName.HARMONY_GPT_OSS);print('OFFLINE_HARMONY_ENCODING_OK',e.encode('ready'))"
proc = subprocess.run([cfg['gpu_python'], '-c', code], env=env, capture_output=True, text=True, timeout=25)
e = dict(command=[cfg['gpu_python'], '-c', code], env_cache=str(p), network_proxies='closed loopback port 9',
         exit_code=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)
(r / 'evidence/harmony-offline-load-check.json').write_text(json.dumps(e, indent=2))
print(proc.returncode, proc.stdout, proc.stderr, flush=True)
if proc.returncode: raise RuntimeError('Offline native encoding cache load failed')

"""Restore this experiment's compiled kernels after GPU/environment validation."""
import hashlib,json,os,platform,sys,zipfile,subprocess
from importlib.metadata import version
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
import os
_PATH_KEYS=('model_path','gpu_python','client_python','batch_root','project_root','resource_environment_root',
            'cuda_toolkit_root','cuda_link_library_dir','cuda_supplemental_include_dir','cuda_vendor_library_dir','local_kernel_cache_dir')
def _resolve_profile(cfg,root):
    """Expand ${VAR} references; resolve relative path settings against the model folder."""
    out=dict(cfg)
    for k in _PATH_KEYS:
        v=out.get(k)
        if isinstance(v,str):
            v=os.path.expandvars(v)
            if not v.startswith('$') and not os.path.isabs(v):
                v=str((root/v).resolve())
            out[k]=v
    return out
cfg=_resolve_profile(json.loads((ROOT/'config/model.json').read_text()),ROOT)
folder=Path(cfg['resource_environment_root'])/'runtime/compiled-cache-snapshots'
metadata=folder/'deepseek-vllm030-cu130-sm90a.json'
if not metadata.exists():
    print('COMPILED_CACHE_SNAPSHOT_NOT_PRESENT; normal startup will compile');raise SystemExit(0)
m=json.loads(metadata.read_text())
fingerprint=dict(vllm=version('vllm'),torch=version('torch'),machine=platform.machine(),libc=list(platform.libc_ver()),environment_freeze_sha256=hashlib.sha256(subprocess.check_output([sys.executable,'-m','pip','freeze'])).hexdigest())
if fingerprint!=m['fingerprint']:
    print('COMPILED_CACHE_ENVIRONMENT_MISMATCH; skip restoration');raise SystemExit(0)
archive=folder/'deepseek-vllm030-cu130-sm90a.zip'
h=hashlib.sha256()
with archive.open('rb') as f:
    for block in iter(lambda:f.read(8*1024*1024),b''):h.update(block)
assert h.hexdigest()==m['archive_sha256'],'Snapshot SHA256 mismatch'
targets=dict(triton=Path(cfg['local_kernel_cache_dir']),flashinfer=Path('/root/.cache/flashinfer/0.6.18.post1/90a'))
assert targets['triton'].is_relative_to('/tmp')
with zipfile.ZipFile(archive) as z:
    assert z.testzip() is None
    for member in z.infolist():
        parts=Path(member.filename).parts
        assert len(parts)>1 and parts[0] in targets
        root=targets[parts[0]];target=root.joinpath(*parts[1:])
        assert target.resolve().is_relative_to(root.resolve()),member.filename
        if member.is_dir():target.mkdir(parents=True,exist_ok=True);continue
        if target.exists():continue
        target.parent.mkdir(parents=True,exist_ok=True)
        with z.open(member) as source,target.open('wb') as dest:
            for block in iter(lambda:source.read(8*1024*1024),b''):dest.write(block)
print('COMPILED_KERNEL_CACHE_RESTORED; KV prefix cache is not restored')

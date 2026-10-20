import json
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
PROFILE=_resolve_profile(json.loads((ROOT/'config/model.json').read_text(encoding='utf-8')),ROOT)
def template_kwargs(): return dict(PROFILE['template_kwargs'])
def request_settings(): return dict(chat_template_kwargs=template_kwargs(),**PROFILE['request_extra'])

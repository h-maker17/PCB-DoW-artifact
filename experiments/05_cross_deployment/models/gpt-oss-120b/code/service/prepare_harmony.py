"""Reconstruct original o200k BPE bytes from uploaded tokenizer, exact hash check."""
import base64,hashlib,json,re
from pathlib import Path
env=Path('/path/to/workspace/多智能体系统实验数据/mas-qwen3-system/.venv/lib/python3.11/site-packages')
raw=next((env/'openai_harmony').glob('*.so')).read_bytes()
print('CACHE_ENV_NAMES',sorted({x.decode() for x in re.findall(rb'[A-Z_]{5,}',raw) if b'CACHE' in x or b'TIKTOKEN' in x}),flush=True)
for needle in (b'cache dir',b'cache file',b'CACHE_DIR',b'tiktoken-rs-cache'):
    pos=raw.find(needle)
    print('CONTEXT',needle.decode(),re.sub(rb'[^\x20-\x7e]',b' ',raw[max(0,pos-150):pos+180]).decode() if pos>=0 else None,flush=True)
m=Path('/path/to/workspace/LLM-Model/gpt-oss-120b-MXFP4')
v=json.loads((m/'tokenizer.json').read_text())['model']['vocab']
bs=list(range(33,127))+list(range(161,173))+list(range(174,256));cs=bs.copy();n=0
for b in range(256):
    if b not in bs:bs.append(b);cs.append(256+n);n+=1
inverse={chr(c):b for b,c in zip(bs,cs)}
data=b''.join(base64.b64encode(bytes(inverse[c] for c in token))+b' '+str(rank).encode()+b'\n' for token,rank in sorted(v.items(),key=lambda x:x[1]))
expected='446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d'
digest=hashlib.sha256(data).hexdigest()
assert digest==expected,(digest,'Reconstructed encoding differs from official hash: do not use')
r=Path('/path/to/workspace/多智能体系统实验数据/fcb-dow-research/experiments/2026-10-03-c-remaining3-models/models/gpt-oss-120b')
p=r/'cache/harmony';p.mkdir(parents=True,exist_ok=True)
(p/'o200k_base.tiktoken').write_bytes(data)
(r/'evidence/harmony-offline-encoding.json').write_text(json.dumps(dict(source=str(m/'tokenizer.json'),
    source_sha256=hashlib.sha256((m/'tokenizer.json').read_bytes()).hexdigest(),cache_file=str(p/'o200k_base.tiktoken'),
    sha256=digest,official_expected_hash=expected,exact_official_bytes_verified=True,resource_downloaded=False),indent=2))
print('OFFICIAL_ENCODING_RECONSTRUCTED_HASH_VERIFIED',p/'o200k_base.tiktoken',flush=True)

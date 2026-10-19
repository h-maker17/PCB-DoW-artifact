"""Llama baseline orchestration; reuse the original AutoGen injection paths."""
import argparse
import asyncio
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from cross_model_anchor_qwen import check_run
from shared_prefix_lab import ASSET, run as run_b
from e5_tool_chain import run as run_e5

ROOT = Path(__file__).resolve().parents[2]
MODEL = 'llama-3.3-70b'
URL = 'http://127.0.0.1:8000'
FRACTIONS = (0.1, 0.25, 0.5)


def read(path, default=None):
    return json.loads(Path(path).read_text(encoding='utf-8')) if Path(path).exists() else default


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(tmp, path)


def now():
    return datetime.now(timezone.utc).isoformat()


def new_session(repeats=5):
    if repeats < 5:
        raise ValueError('At least five independent replicates are required')
    session = ROOT/'data'/('session-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:6])
    session.mkdir(parents=True)
    for group in ('probe', 'e1', 'e2', 'e5'):
        (session/group).mkdir()
    save(session/'session.json',dict(model=MODEL, repeats=repeats, created_utc=now(),
        execution='sequential', source='live vLLM only; no local fixtures',
        expected_formal_runs=15*repeats, expected_formal_requests=111*repeats,
        metering='raw prompt/cached/uncached/completion/total; cold worker 1',
        temperature=0,seed=42,thinking='not applicable to Llama; no thinking kwargs',
        tensor_parallel_size=1,weight_quantization='modelopt_fp8',
        activation_dtype='bfloat16',kv_cache_dtype='bfloat16',block_size=16,
        service=read(ROOT/'evidence/service-process.json'),
        command=(Path((ROOT/'evidence/latest-service.txt').read_text().strip())/'command.txt').read_text()))
    save(session/'run-index.json',{})
    (ROOT/'config/latest-session.txt').write_text(str(session)+'\n',encoding='utf-8')
    return session


def args_for(output,n,kind,variant):
    return SimpleNamespace(output=str(output),url=URL,model=MODEL,n_workers=n,workers=n,
        asset=str(ASSET),shared_tokens=2000,variance=variant,mark='zwsp_k',arm=variant)


def validate(dest,n,kind):
    metrics=check_run(dest.parent,dest.name,n,'e5' if kind=='e5' else 'part_b')
    manifest=read(dest/'manifest.json')
    if manifest['model']!=MODEL or manifest['shared_tokens_calibrated_no_tools']!=2000:
        raise RuntimeError(f'Model/token calibration mismatch: {dest}')
    for i in range(1,n+1):
        stem=f'{i:04d}-worker_{i}'
        req=read(dest/(stem+'.request.json'))
        res=read(dest/(stem+'.response.json'))
        tok=read(dest/(stem+'.tokenize.json'))
        if req.get('stream',False) or 'enable_thinking' in req.get('chat_template_kwargs',{}):
            raise RuntimeError('Llama must use non-stream and omit Qwen thinking parameters')
        if req.get('temperature')!=0 or req.get('seed')!=42:
            raise RuntimeError('Deterministic request settings changed')
        if res.get('prompt_token_ids')!=tok.get('tokens'):
            raise RuntimeError(f'Actual input token mismatch: {stem}')
        message=res['choices'][0]['message']
        if message.get('reasoning_content') or message.get('reasoning') or '<think>' in (message.get('content') or ''):
            raise RuntimeError('Unexpected reasoning segment')
    return metrics


def conditions(group):
    if group=='e1':
        return [(v,5,v) for v in ('none','prefix','midfix','suffix')]
    if group=='e2':
        return [(f'n{n}/{v}',n,v) for n in (3,5,10,20) for v in ('none','prefix')]
    if group=='e5':
        return [(a,5,a) for a in ('const-tool','nonce-tool','nonce-late')]
    raise ValueError(group)


async def run_group(session,group):
    session=Path(session).resolve()
    if not session.is_relative_to(ROOT/'data'):
        raise ValueError('Session must be under this experiment data directory')
    if not read(session/'probe/calibration.json',{}).get('assembly_verified'):
        raise RuntimeError('Run the mandatory live calibration/assembly probe first')
    index=read(session/'run-index.json',{})
    repeats=read(session/'session.json')['repeats']
    for rep in range(1,repeats+1):
        for label,n,variant in conditions(group):
            recorded_service=read(session/'session.json')['service']
            if read(ROOT/'evidence/service-process.json')['pid']!=recorded_service['pid']:
                raise RuntimeError('Model server restarted during this session; start a new session')
            os.kill(recorded_service['pid'],0)
            key=f'{group}/r{rep}/{label}'
            if key in index:
                validate(session/index[key],n,group)
                print('SKIP',key,index[key],flush=True)
                continue
            arguments=args_for(session/group,n,group,variant)
            dest=await (run_e5(arguments) if group=='e5' else run_b(arguments))
            metrics=validate(dest,n,group)
            manifest=read(dest/'manifest.json')
            manifest.update(experiment='llama70b-baseline',group=group,replicate=rep,
                condition=label,session_id=session.name,
                tensor_parallel_size=1,weight_quantization='modelopt_fp8',
                activation_dtype='bfloat16',kv_cache_dtype='bfloat16',block_size=16,
                service_config=read(session/'session.json')['command'])
            save(dest/'manifest.json',manifest)
            index[key]=str(dest.relative_to(session))
            save(session/'run-index.json',index)
            save(session/'status.json',dict(phase='running',group=group,
                completed_runs=len(index),expected_runs=repeats*15,updated_utc=now()))
            print('SAVED',key,index[key],'hit=',metrics['worker_2_to_n_hit_rate'],
                  'uncached=',metrics['uncached_tokens'],flush=True)


def group_main(group):
    p=argparse.ArgumentParser(description=f'Llama {group.upper()} baseline, five repeats')
    p.add_argument('--session',type=Path,required=True)
    a=p.parse_args()
    asyncio.run(run_group(a.session,group))

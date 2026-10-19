import hashlib
import json
import subprocess
from datetime import datetime,timezone
from pathlib import Path

BASE=Path('/path/to/workspace')
PROJECT=BASE/'多智能体系统实验数据/fcb-dow-research'
ROOT=PROJECT/'experiments/2026-10-03-llama70b-baseline'
session=Path((ROOT/'config/latest-session.txt').read_text().strip())
status=json.loads((session/'status.json').read_text())
if status['phase']!='complete':
    raise RuntimeError('Formal baseline is not complete; cannot finalize')
if not (ROOT/'config/model-files.json').is_file():
    raise RuntimeError('Full model hash inventory not complete')
index=json.loads((session/'run-index.json').read_text())
validated_requests=0
for rel in index.values():
    dest=session/rel
    manifest=json.loads((dest/'manifest.json').read_text())
    usage=json.loads((dest/'usage.json').read_text())
    previous=[]
    for row in usage:
        req=json.loads((dest/(row['call']+'.request.json')).read_text())
        res=json.loads((dest/(row['call']+'.response.json')).read_text())
        ids=res['prompt_token_ids']
        assert req['cache_salt']==manifest['cache_salt']
        assert len(ids)==row['prompt_tokens']==res['usage']['prompt_tokens']
        assert row['cached_tokens']==res['usage']['prompt_tokens_details']['cached_tokens']
        assert row['prompt_tokens']==row['cached_tokens']+row['uncached_tokens']
        assert row['total_tokens']==row['prompt_tokens']+row['completion_tokens']
        def common(left,right):
            return next((i for i,(a,b) in enumerate(zip(left,right)) if a!=b),min(len(left),len(right)))
        bound=max((common(p,ids) for p in previous),default=0)
        assert row['cached_tokens']<=bound,(dest,row['call'],row['cached_tokens'],bound)
        previous.append(ids)
        validated_requests+=1
assert validated_requests==555,validated_requests

def dump(p,obj):
    p.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(8*1024*1024),b''):h.update(b)
    return h.hexdigest()

gpu_py=BASE/'多智能体系统实验数据/mas-qwen3-system/.venv/bin/python'
client_py=BASE/'多智能体系统实验数据/autogen-handoff-lab/.venv-autogen/bin/python'
code='import sys,json;from importlib.metadata import version;print(json.dumps(dict(python=sys.version,packages={p:version(p) for p in sys.argv[1:]})))'
gpu=json.loads(subprocess.check_output([str(gpu_py),'-c',code,'vllm','torch','transformers'],text=True))
client=json.loads(subprocess.check_output([str(client_py),'-c',code,'autogen-agentchat','autogen-core','autogen-ext','openai','httpx'],text=True))
env=dict(instance='<instance-id>',persistent_mount='dpc',experiment_root=str(ROOT),
    model=str(BASE/'LLM-Model/Llama-3.3-70B-Instruct-FP8'),gpu_python=str(gpu_py),
    client_python=str(client_py),gpu_environment=gpu,client_environment=client,
    service=json.loads((ROOT/'evidence/service-process.json').read_text()),
    gpu=subprocess.check_output(['nvidia-smi','--query-gpu=index,name,memory.total,memory.used','--format=csv'],text=True),
    TP=1,activation_dtype='bfloat16',KV_dtype='bfloat16',weight_quantization='modelopt_fp8',
    max_model_len=32768,block_size=16,gpu_memory_utilization=.99,enforce_eager=True,
    max_num_seqs=1,seed=42,temperature=0,session=str(session),status=status)
dump(ROOT/'config/environment.json',env)
dump(ROOT/'evidence/raw-request-audit.json',dict(requests=validated_requests,
    actual_ids_match_usage=True,request_salts_match_manifests=True,
    cached_tokens_bounded_by_actual_prior_prompt_prefixes=True,
    raw_cached_tokens_match_usage=True,total_equals_prompt_plus_completion=True))
attempts=json.loads((ROOT/'evidence/bringup-attempts.json').read_text())
attempts[-1]['result']='ready and baseline complete'
dump(ROOT/'evidence/bringup-attempts.json',attempts)
names={
    'finalize_project.py':'逐条原始请求审计、环境及代码路径清单、回答质量限制与完整性哈希',
    'run_e1.py':'E1 四位置正式入口','run_e2.py':'E2 N=3/5/10/20 成对正式入口',
    'run_e5.py':'E5 三臂正式入口','calibrate_probe.py':'真实探活、P/H 标定与装配探针',
    'report_baseline.py':'统计表、CI、CAF、Qwen 对照与 Go/No-Go',
    'run_all.py':'顺序工作流与 session 续跑','experiment_common.py':'条件矩阵及每个 run 遥测验收',
    'lab.py':'官方 AutoGen/OpenAI 客户端追踪独立副本','shared_prefix_lab.py':'Part B 装配、Llama 精确 P 校准和缓存指标',
    'e5_tool_chain.py':'共享 FunctionTool 三臂装配和原始证据','manual_replay.py':'历史参数辅助快照，本轮不调用其 Qwen 参数',
    'cross_model_anchor_qwen.py':'复用通用验收、Token-ID 读取及 Student t CI；不重跑 Qwen',
    'server.py':'vLLM 0.11.2 缓存零值补丁',
    'start_service.sh':'Llama 服务启动与 GPU 锁',
    'run_baseline.sh':'后台启动 75-run 真实实验','status.sh':'session 进度查看'}
files=[p for parent in ('code','bin') for p in (ROOT/parent).rglob('*')
       if p.is_file() and '__pycache__' not in p.parts]
rows=[dict(path=str(p),relative_path=p.relative_to(ROOT).as_posix(),purpose=names.get(p.name,'共享文档或固定 tokenizer 缓存资产'),sha256=sha(p)) for p in sorted(files)]
dump(ROOT/'config/code-files.json',rows)
lines=['# Llama70B 实验配置与代码路径清单','',f'实测状态：{status["phase"]}，判定 {status["verdict"]}。','',
    f'持久化根目录：`{ROOT}`。',f'正式结果 session：`{session}`。','',
    '|环境|解释器|组件|','|---|---|---|',f'|GPU 服务环境|`{gpu_py}`|vLLM 0.11.2、PyTorch 2.9.0、Transformers 4.57.1|',
    f'|AutoGen 客户端|`{client_py}`|AutoGen 0.7.5；openai/httpx 等版本见 config/environment.json|','',
    '实测配置：1×H100 80GB、TP=1、ModelOpt FP8 权重、BF16 运算及 BF16 KV、32768 上下文、block16、APC 开启、非流式、显存比例0.99、eager、max_num_seqs=1。',
    '首次0.98比例仅有9.34 GiB KV，容量验收失败；0.99比例通过。两轮日志均保留，没有降低上下文或改变注入方式。','',
    '|完整文件路径|作用|','|---|---|']
for row in rows:lines.append(f'|`{row["path"]}`|{row["purpose"]}|')
lines+=['','报告：`'+str(ROOT/'reports/实验记录_Llama70B基线.md')+'`。',
    '跨模型汇总：`'+str(PROJECT/'tables/cross-model-summary.csv')+'`。',
    '主模型来源与分片哈希：`'+str(ROOT/'config/model-files.json')+'`。',
    '所有 raw request/response/tokenize/usage/metrics 分别在 session 的 e1/e2/e5 子目录中；probe 子目录不计入75个正式 run。']
(ROOT/'reports/实验配置与代码路径清单.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
experiment_index=PROJECT/'config/experiments-index.json'
existing=json.loads(experiment_index.read_text()) if experiment_index.exists() else {}
existing['2026-10-02-qwen-anchor']=dict(path=str(PROJECT/'experiments/2026-10-02-qwen-anchor'),status='preserved')
existing['2026-10-03-llama70b-baseline']=dict(path=str(ROOT),session=str(session),status='complete',verdict=status['verdict'])
dump(experiment_index,existing)
checks=[]
tool_calls=0
for key,rel in index.items():
    dest=session/rel
    for item in json.loads((dest/'answer-checks.json').read_text()):
        checks.append(dict(condition=key,**item))
    for response in dest.glob('*.response.json'):
        raw=json.loads(response.read_text())
        tool_calls+=sum(len(c.get('message',{}).get('tool_calls') or []) for c in raw.get('choices',[]))
quality=dict(total_answers=len(checks),screen_passes=sum(c['screen_pass'] for c in checks),
    raw_model_tool_calls=tool_calls,
    missing_expected_citations=sum(bool(c['missing_records']) for c in checks),
    missing_topic_cues=sum(not c['topic_cue_present'] for c in checks),
    insufficient_explanation=sum(not c['explanatory_length'] for c in checks),
    examples=checks[:5],
    interpretation='Cache Go is only a cache-mechanism verdict. Task-quality acceptance did not pass. Representative E1 model tool calls were followed by AutoGen tool summaries (reflect_on_tool_use=False), not explanatory final answers. No prompts or injection were changed.')
dump(ROOT/'evidence/answer-quality-audit.json',quality)
note=('\n## 回答质量限制\n\n'
      f'本轮自动回答筛选通过 {quality["screen_passes"]}/{quality["total_answers"]}；'
      f'原始模型响应包含 {tool_calls} 个工具调用。'
      '抽查 E1 无注入条件，模型调用 calculate 后，AutoGen 在 reflect_on_tool_use=False 配置下返回工具数字结果（例如15），没有产出要求的引用和解释。'
      '因此上述 Go 仅指缓存机制判据通过，任务质量验收未通过。不能将本轮解释为完整任务性能或原生 handoff 验证；后续任务质量实验需要独立协议与目录。'
      '详见 evidence/answer-quality-audit.json 及每个 run 的原始响应和 answer-checks.json。\n')
for report in (ROOT/'reports/实验记录_Llama70B基线.md',session/'reports/实验记录_Llama70B基线.md'):
    body=report.read_text(encoding='utf-8').split('\n## 回答质量限制')[0]
    report.write_text(body+note,encoding='utf-8')
static=[]
for folder in ('code','bin','config','data','tables','reports'):
    for p in sorted((ROOT/folder).rglob('*')):
        if p.is_file() and '__pycache__' not in p.parts and p.name!='artifacts-sha256.json':
            static.append(dict(path=p.relative_to(ROOT).as_posix(),size_bytes=p.stat().st_size,sha256=sha(p)))
dump(ROOT/'config/artifacts-sha256.json',dict(created_utc=datetime.now(timezone.utc).isoformat(),files=static,
    excluded='active logs, caches and process locks',file_count=len(static)))
for row in static:
    if sha(ROOT/row['path'])!=row['sha256']:raise RuntimeError('Hash verification failed: '+row['path'])
print('FINALIZED_ARTIFACTS_VERIFIED',len(static),status,flush=True)

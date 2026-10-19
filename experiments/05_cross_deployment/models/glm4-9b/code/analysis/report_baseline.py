"""Paired C-protocol statistics and additive comparison with the preserved Qwen row."""
import argparse
import csv
import math
import statistics
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'runtime'))
from experiment_common import ROOT,MODEL,FRACTIONS,read,save,validate,now
from model_settings import PROFILE
from cross_model_anchor_qwen import ci,response_ids,lcp,write_csv


def report(session):
    session=Path(session).resolve()
    index=read(session/'run-index.json',{})
    settings=read(session/'session.json')
    reps=settings['repeats']
    if len(index)!=reps*15:
        raise RuntimeError(f'Incomplete formal baseline: {len(index)}/{reps*15}')
    salts=[read(session/r/'manifest.json')['cache_salt'] for r in index.values()]
    if len(set(salts))!=len(salts) or len(set(index.values()))!=len(index):
        raise RuntimeError('Repeated salt or run directory')
    metrics={}
    for key,rel in index.items():
        group=key.split('/')[0]
        n=int(key.split('/n')[1].split('/')[0]) if group=='e2' else 5
        metrics[key]=validate(session/rel,n,group)
    summary=dict(model=PROFILE['name'],repeats=reps,n_runs=len(index),
        evidence_kind='live vLLM; unique salt and cold first worker',session_id=session.name)
    detailed=[]

    def add(name,values):
        mean,low,high=ci(values)
        detailed.append(dict(metric=name,n=len(values),mean=mean,ci95_low=low,ci95_high=high,
            method='Student t 95%',interpretation='reproducibility at fixed task/seed' if low==high else 'across independently salted runs'))
        summary[name+'_mean']=mean
        summary[name+'_ci95_low']=low
        summary[name+'_ci95_high']=high

    for v in ('none','prefix','midfix','suffix'):
        entries=[metrics[f'e1/r{r}/{v}'] for r in range(1,reps+1)]
        add(f'e1_{v}_reusing_hit_rate',[m['worker_2_to_n_hit_rate'] for m in entries])
        add(f'e1_{v}_uncached_tokens',[m['uncached_tokens'] for m in entries])
    e2=[]
    for n in (3,5,10,20):
        for v in ('none','prefix'):
            entries=[metrics[f'e2/r{r}/n{n}/{v}'] for r in range(1,reps+1)]
            mean,low,high=ci([m['worker_2_to_n_hit_rate'] for m in entries])
            unc=ci([m['uncached_tokens'] for m in entries])
            e2.append(dict(N=n,variance=v,repeats=reps,hit_mean=mean,hit_ci95_low=low,hit_ci95_high=high,
                uncached_mean=unc[0],uncached_ci95_low=unc[1],uncached_ci95_high=unc[2]))
        for f in FRACTIONS:
            add(f"e2_n{n}_caf_f{str(f).replace('.', '_')}",[
                metrics[f'e2/r{r}/n{n}/prefix']['cost_by_cache_fraction'][str(f)]/
                metrics[f'e2/r{r}/n{n}/none']['cost_by_cache_fraction'][str(f)] for r in range(1,reps+1)])
    for arm in ('const-tool','nonce-tool','nonce-late'):
        entries=[metrics[f'e5/r{r}/{arm}'] for r in range(1,reps+1)]
        add(f'e5_{arm}_reusing_hit_rate',[m['worker_2_to_n_hit_rate'] for m in entries])
        add(f'e5_{arm}_uncached_tokens',[m['uncached_tokens'] for m in entries])
    for arm in ('nonce-tool','nonce-late'):
        for f in FRACTIONS:
            add(f"e5_{arm}_caf_f{str(f).replace('.', '_')}",[
                metrics[f'e5/r{r}/{arm}']['cost_by_cache_fraction'][str(f)]/
                metrics[f'e5/r{r}/const-tool']['cost_by_cache_fraction'][str(f)] for r in range(1,reps+1)])
    hs,ps=[] , []
    for rep in range(1,reps+1):
        dest=session/index[f'e1/r{rep}/prefix']
        hs.append(lcp(response_ids(dest.parent,dest.name,1),response_ids(dest.parent,dest.name,2)))
        ps.append(statistics.mean(m['prompt_tokens'] for m in metrics[f'e1/r{rep}/none']['workers'][1:]))
    add('H_pre_marker_tokens',hs)
    add('P_plus_s_full_prompt_tokens',ps)
    add('predicted_collapse_floor',[h/p for h,p in zip(hs,ps)])
    summary.update(P_shared_doc_target_tokens=2000,
        H_definition='actual /tokenize-verified LCP before per-worker front marker',
        P_plus_s_definition='mean full prompt tokens of E1 none workers 2..5',
        answer_screen_passes=sum(m['answer_screen_passes'] for m in metrics.values()),
        answer_screen_count=sum(m['answer_screen_count'] for m in metrics.values()),
        model_requests=sum(m['n_workers'] for m in metrics.values()))
    floor=summary['predicted_collapse_floor_mean']
    tolerance=32/statistics.mean(ps)
    high_metrics=('e1_none_reusing_hit_rate_mean','e1_suffix_reusing_hit_rate_mean',
                  'e5_const-tool_reusing_hit_rate_mean','e5_nonce-late_reusing_hit_rate_mean')
    front_close=abs(summary['e1_prefix_reusing_hit_rate_mean']-floor)<=tolerance
    e5_close=abs(summary['e5_nonce-tool_reusing_hit_rate_mean']-floor)<=tolerance
    high=all(summary[k]>=0.90 for k in high_metrics)
    verdict='Go' if front_close and e5_close and high else 'No-Go'
    summary.update(verdict=verdict,go_front_floor_tolerance=tolerance,
                   go_high_hit_threshold=0.90)
    tables=session/'tables'
    tables.mkdir(exist_ok=True)
    write_csv(tables/'metrics-ci.csv',detailed)
    write_csv(tables/'e2-hit-uncached.csv',e2)
    project=Path(PROFILE['project_root'])
    anchor=project/'experiments/2026-10-02-qwen-anchor/tables/cross-model-summary.csv'
    with anchor.open(encoding='utf-8',newline='') as stream:
        reader=csv.DictReader(stream)
        anchor_fields=reader.fieldnames
        qwen=next(reader)
    fields=list(dict.fromkeys(anchor_fields+list(summary)))

    def write_union(path,rows):
        path.parent.mkdir(parents=True,exist_ok=True)
        with path.open('w',encoding='utf-8',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=fields,extrasaction='ignore')
            writer.writeheader();writer.writerows(rows)
    write_union(tables/'cross-model-summary.csv',[summary])
    write_union(tables/'qwen-glm4-9b-comparison.csv',[qwen,summary])
    central=project/'tables/cross-model-summary.csv'
    rows=[]
    if central.exists():
        with central.open(encoding='utf-8',newline='') as stream:
            rows=list(csv.DictReader(stream))
        fields=list(dict.fromkeys(fields+[k for row in rows for k in row]))
    else:
        rows=[qwen]
    rows=[r for r in rows if r.get('session_id')!=session.name]
    write_union(central,rows+[summary])
    save(session/'report-audit.json',dict(run_ids=index,unique_salts=True,cold_worker_1=True,
        actual_ids_verified=True,total_runs=len(index),total_requests=summary['model_requests'],
        assembly_probe=read(session/'probe/calibration.json'),verdict=verdict,
        front_near_floor=front_close,e5_front_near_floor=e5_close,controls_high=high,
        completed_utc=now(),e5_tool_invocation='application calls one shared AutoGen FunctionTool; model does not select tool',
        statistics='paired CAF by replicate; t CI; zero width denotes fixed-condition reproducibility'))
    lines=['# GLM-4-9B-0414 跨模型基线实测记录','',
        f'结果：**{verdict}**。{len(index)}/{reps*15} 个正式 run，{summary["model_requests"]} 次真实模型请求；每格 {reps} 个独立 salt，worker1 缓存命中均为 0。',
        f'P=2000；H={statistics.mean(hs):.2f}；P+s={statistics.mean(ps):.2f}；预测地板 H/(P+s)={floor:.6f}。',
        f'答案自动筛选 {summary["answer_screen_passes"]}/{summary["answer_screen_count"]}；C组仅测缓存机制，该值不参与Go判定。','',
        '假设：front 应塌至自身 tokenizer 对应的地板；none/suffix/const/late 应保持高缓存率。Go 判据预先采用距离地板不超过 32/(P+s)，高位阈值 0.90。No-Go 时检查装配证据，不改变注入方式。','',
        '|条件|后续 worker 命中率均值|95% CI|','|---|---:|---|']
    for k in ('e1_none','e1_prefix','e1_midfix','e1_suffix','e5_const-tool','e5_nonce-tool','e5_nonce-late'):
        key=k+'_reusing_hit_rate'
        lines.append(f'|{k}|{summary[key+"_mean"]:.6f}|[{summary[key+"_ci95_low"]:.6f}, {summary[key+"_ci95_high"]:.6f}]|')
    lines+=['',f'Qwen 锚点地板 {float(qwen["predicted_collapse_floor_mean"]):.6f}、front 命中率 {float(qwen["e1_prefix_reusing_hit_rate_mean"]):.6f}；本模型必须与自身地板比较。','',
        '|N|CAF f=0.1|CAF f=0.25|CAF f=0.5|','|---|---:|---:|---:|']
    for n in (3,5,10,20):
        values=[summary[f"e2_n{n}_caf_f{str(f).replace('.', '_')}_mean"] for f in FRACTIONS]
        lines.append(f'|{n}|'+ '|'.join(f'{v:.4f}' for v in values)+'|')
    lines+=['','CAF 与 CI 全字段见 tables/metrics-ci.csv；Qwen/GLM-4-9B-0414 同字段对照见 tables/qwen-glm4-9b-comparison.csv。',
        'C组沿用 reflect_on_tool_use=False；回答筛选不用于证明任务性能。',
        'CI 零宽时仅表示固定任务、固定种子的复现性，不代表跨任务不确定性为零。',
        'E5 工具由应用调用后装配输入；没有将其表述为模型自主调用工具或原生 Swarm handoff。',
        '本实例 GPU/TP/KV 和 vLLM 参数见 session.json 与 evidence/service-*；权重来源与 SHA256 见 config/model-files.json。']
    reports=session/'reports';reports.mkdir(exist_ok=True)
    (reports/'实验记录_glm4-9b基线.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    for path in tables.iterdir():
        (ROOT/'tables'/path.name).write_bytes(path.read_bytes())
    (ROOT/'reports/实验记录_glm4-9b基线.md').write_bytes((reports/'实验记录_glm4-9b基线.md').read_bytes())
    save(session/'status.json',dict(phase='complete',verdict=verdict,completed_runs=len(index),
        expected_runs=reps*15,updated_utc=now()))
    print('REPORT_COMPLETE',verdict,session,'P=2000','H=',statistics.mean(hs),'floor=',floor,flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--session',type=Path,required=True)
    report(p.parse_args().session)

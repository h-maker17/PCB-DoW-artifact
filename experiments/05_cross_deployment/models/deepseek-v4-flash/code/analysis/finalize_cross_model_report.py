from pathlib import Path
import json,csv,collections,datetime,shutil
project=Path('/path/to/workspace/多智能体系统实验数据/fcb-dow-research')
m=project/'experiments/2026-10-04-deepseek-v4-rerun/models/deepseek-v4-flash'
s=Path((m/'config/latest-session.txt').read_text().strip())
read=lambda p:json.loads(p.read_text())
a=read(s/'independent-audit.json')
assert a['formal_runs']==75 and a['formal_requests']==555
with (s/'tables/cross-model-summary.csv').open() as f: summary=next(csv.DictReader(f))
central=project/'tables/cross-model-summary.csv'
backup=m/'evidence'/('cross-model-summary-before-final-'+s.name+'.csv')
if not backup.exists():shutil.copyfile(central,backup)
with backup.open() as f:
 reader=csv.DictReader(f);fields=reader.fieldnames;prior=list(reader)
others=[r for r in prior if r['model']!=summary['model']]
rows=others+[summary]
fields=list(dict.fromkeys(fields+list(summary)))
for dest in [central,s/'tables/cross-model-summary-five-models.csv',m/'tables/cross-model-summary-five-models.csv']:
 with dest.open('w',newline='',encoding='utf-8') as f:
  w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
idx=read(s/'run-index.json');patterns=collections.defaultdict(collections.Counter)
for key,rel in idx.items():
 group=key.split('/')[0]
 label=group+'/'+('/'.join(key.split('/')[2:]) if group=='e2' else key.split('/')[-1])
 vals=[]
 for f in sorted((s/rel).glob('*.response.json')):
  vals.append(read(f)['usage']['prompt_tokens_details']['cached_tokens'])
 patterns[label][tuple(vals)]+=1
def num(key):return float(summary[key])
lines=['# 实验组C：DeepSeek完整实验与跨模型汇总（2026-10-04）','',
 '完整正式批次已完成：E1 20组/100请求，E2 40组/380请求，E5 15组/75请求，总计75组/555次真实请求，每格5次独立salt重复。探针和诊断请求不计入这些数量。',
 f'按保留的原Go标准，DeepSeek结果为 **{summary["verdict"]}**；前置扰动确实显著降低缓存命中并放大输入成本，但观测命中率没有落在原简单头部地板的容差内。该判定不等于攻击无效。','',
 '## 假设、测量与判定','',
 f'共享文档使用原生tokenizer校准为2000 token；真实注入前LCP H={num("H_pre_marker_tokens_mean"):.0f}，对照完整输入P+s={num("P_plus_s_full_prompt_tokens_mean"):.0f}。原预测地板={num("predicted_collapse_floor_mean"):.6f}，容差±{num("go_front_floor_tolerance"):.6f}；none/suffix/const/late高位阈值0.90。均保留原值。',
 '下表命中率排除每组第一个冷worker；CAF按配对重复计算，公式为(uncached+f×cached)_attack/(uncached+f×cached)_control，只计输入token，属于假定缓存价格下的成本放大，不能视为平台实付费用。','',
 '|条件|命中率均值|95% Student t区间|未缓存输入token均值（含冷worker）|',
 '|---|---:|---|---:|']
for label in ['e1_none','e1_prefix','e1_midfix','e1_suffix','e5_const-tool','e5_nonce-tool','e5_nonce-late']:
 k=label+'_reusing_hit_rate'
 lines.append(f'|{label}|{num(k+"_mean"):.6f}|[{num(k+"_ci95_low"):.6f}, {num(k+"_ci95_high"):.6f}]|{num(label+"_uncached_tokens_mean"):.1f}|')
lines+=['','|扇出N|CAF f=.1|CAF f=.25|CAF f=.5|','|---|---:|---:|---:|']
for n in [3,5,10,20]:lines.append(f'|{n}|'+ '|'.join(f'{num(f"e2_n{n}_caf_f{f}_mean"):.6f}' for f in ['0_1','0_25','0_5'])+'|')
lines+=['','完整CI字段见tables/metrics-ci.csv；区间零宽仅说明固定任务与seed下的重复结果一致，不代表跨任务不确定性为零。','',
 '## DeepSeek缓存结构发现','',
 '真实输入ID与/tokenize一致，前置注入在共享文档之前，system/tools构成前缀头部，late在文档之后，装配证据通过。不能把本轮No-Go归因于未经证实的注入位置错误。',
 'block=256时655-token头部对应512-token完整块，512/2678=0.191187；但这仍无法解释全部观测。以下是完整批次按worker顺序统计的cached_tokens序列；第一项为冷worker。','',
 '|条件|缓存token序列|重复次数|','|---|---|---:|']
for label,counter in sorted(patterns.items()):
 for pattern,count in sorted(counter.items()):lines.append(f'|{label}|{list(pattern)}|{count}|')
lines+=['',
 'DeepSeek-V4包含混合压缩及滑窗缓存组（sliding_window=128）。vLLM缓存协调器支持不同组公共前缀重放，滑窗管理器存在块回收和重放边界保留机制；源码摘录与哈希见evidence/hybrid-prefix-cache-source-audit.json。它们支持对worker2与后续worker差异的结构解释，但没有逐缓存组运行时追踪，因此不声称已完全证明每一次偏差的内部原因。',
 f'独立审核发现{a["block_prediction_deviations"]}个请求不符合单纯LCP整块取整预测。该比较仅作为结构审计，未替换原Go判据。','',
 '## 配置与生成修复','',
 '四张H100 80GB，TP=4，vLLM 0.30.0、FP8检查点/BF16激活、原生fp8_ds_mla KV、block=256、max_model_len=32768；单请求FULL_DECODE_ONLY CUDA图、capture_sizes=[1]、mode=0、标准NCCL、MoE deep_gemm。其他模型早期vLLM版本及block16与此有差异，跨模型对比不等于服务配置完全受控的因果实验。',
 '原检查点44个attn.wo_a权重是BF16，未被原量化配置排除时走入序列化FP8加载路径，逐层真实请求诊断在第一层输出投影发现NaN。通过支持的hf_overrides.quantization_config.ignored_layers保留这44层原BF16精度后，短长真实输入恢复有限输出。未修改权重文件、任务、标记、预算或阈值。',
 '原生非思考chat入口经探针核验；固定工具指南中作为说明文字出现的`<think>`没有被误当作实际推理段。独立审核未发现正式输出reasoning。temperature=0、seed=42、max_tokens=2048保持。',
 '56个挂载检查点文件共294,055,069,659字节的实际SHA256均已完成；见config/model-files.actual-sha256.json。声明来源为sgl-project/DeepSeek-V4-Flash-FP8；本地哈希证明文件身份，不代替独立上游来源验证。',
 '较早eager会话的2组/10请求单独保存并标记superseded-before-complete，未计入本批次。早期service-limited生成失败证据完整保留。','',
 '## 独立核验','',
 '75个salt唯一、全部首worker缓存为0、555份真实输入ID与/tokenize一致、确定性参数保持、正式服务无诊断插桩。',
 f'空文本输出计数={a["empty_model_contents"]}，推理段计数={a["reasoning_outputs"]}；555次响应结束原因为stop。已读取corrupted_requests计数样本为0.0。答案自动筛选通过{summary["answer_screen_passes"]}/{summary["answer_screen_count"]}，该筛选未作为任务性能评估。',
 'E5由应用调用一个共享AutoGen FunctionTool后装配输入；未验证模型自主工具选择或原生Swarm handoff。答案筛选仅为附加描述，不参与C组Go判定。','',
 '## 五模型汇总','',
 '其他四模型历史数值保持原样；Qwen缺失的旧判定/位置字段继续留空，不补造。旧DeepSeek service-limited汇总行在备份中保留，当前表采用本次完整结果。','',
 '|模型|原判定|H|自身地板|E1 front|E1 suffix|E5 const|E5 late|','|---|---|---:|---:|---:|---:|---:|---:|']
ks=['H_pre_marker_tokens_mean','predicted_collapse_floor_mean','e1_prefix_reusing_hit_rate_mean','e1_suffix_reusing_hit_rate_mean','e5_const-tool_reusing_hit_rate_mean','e5_nonce-late_reusing_hit_rate_mean']
for r in rows:
 lines.append('|'+r['model']+'|'+r.get('verdict','')+'|'+'|'.join('' if not r.get(k) else f'{float(r[k]):.6f}' for k in ks)+'|')
lines+=['','|模型|CAF N3 f=.1|CAF N5 f=.1|CAF N10 f=.1|CAF N20 f=.1|','|---|---:|---:|---:|---:|']
for r in rows:lines.append('|'+r['model']+'|'+'|'.join('' if not r.get(f'e2_n{n}_caf_f0_1_mean') else f'{float(r[f"e2_n{n}_caf_f0_1_mean"]):.6f}' for n in [3,5,10,20])+'|')
lines+=['',f'正式会话：`{s.name}`。完整原始请求/响应、manifest、attack-metrics、tokenize、校准探针、配置、代码、哈希、审核和历史会话均归档。']
name='实验记录_C跨模型_20261004.md'
for dest in [s/'reports'/name,m/'reports'/name]:dest.write_text('\n'.join(lines)+'\n',encoding='utf-8')
print(json.dumps({'session':s.name,'audit':a,'summary':summary,'other_model_rows_preserved':len(others),'report':str(m/'reports'/name)},ensure_ascii=False))

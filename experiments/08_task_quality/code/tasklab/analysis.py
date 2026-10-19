import csv,json,statistics,math
import numpy as np
from scipy.stats import mannwhitneyu,wilcoxon
from .common import ROOT,TASKS,read,save,now
from .metrics import normalize_answer,normalize_trivia_answer

def write_csv(path,rows):
 path.parent.mkdir(parents=True,exist_ok=True)
 fields=list(dict.fromkeys(k for row in rows for k in row))
 with path.open('w',encoding='utf-8',newline='') as f:
  writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader();writer.writerows(rows)
def interval(values):
 arr=np.asarray(values,dtype=float);rng=np.random.default_rng(42)
 means=arr[rng.integers(0,len(arr),(10000,len(arr)))].mean(axis=1)
 return tuple(float(x) for x in np.quantile(means,[.025,.975]))
def paired_test(delta,alternative='two-sided'):
 return 1.0 if np.all(np.asarray(delta)==0) else float(wilcoxon(delta,alternative=alternative,zero_method='wilcox').pvalue)

def report():
 summary=[];pairs=[];benign=[];qualities=[];caf_data=[];salt_set=set();request_count=0;aggregations=0
 for task in TASKS:
  records=read(ROOT/'datasets/processed'/f'{task}.json');assert len(records)==30
  data=[]
  for record in records:
   results={arm:read(ROOT/'data/formal'/task/record['id']/arm/'result.json') for arm in ['none','front']}
   for arm,result in results.items():
    assert result['cache_salt'] not in salt_set;salt_set.add(result['cache_salt'])
    assert len(result['workers'])==5 and result['workers'][0]['cached_tokens']==0
    root=ROOT/'data/formal'/task/record['id']/arm
    for worker in range(1,6):
     stem=f'{worker:04d}-worker_{worker}';req=read(root/(stem+'.request.json'));res=read(root/(stem+'.response.json'));tk=read(root/(stem+'.tokenize.json'))
     assert req['cache_salt']==result['cache_salt'] and req['seed']==42 and req['temperature']==0 and req['stream']is False
     assert res['prompt_token_ids']==tk['tokens'] and res['choices'][0]['finish_reason']=='stop'
     assert req.get('tools',[])==[]
     assert record['context'] in req['messages'][1]['content']
     prefix='<SHARED_CONTEXT>\n'+(('\u200b'*worker+'\n') if arm=='front' else '')+record['context']
     assert req['messages'][1]['content'].startswith(prefix)
     request_count+=1
     qualities.append(dict(task=task,instance_id=record['id'],arm=arm,level='worker',worker=worker,**result['workers'][worker-1]['quality']))
    if (root/'aggregate.response.json').exists():aggregations+=1
    qualities.append(dict(task=task,instance_id=record['id'],arm=arm,level='aggregate',worker='',**result['quality']))
   none,front=results['none'],results['front']
   row={'task':task,'instance_id':record['id'],'context_tokens':record['context_tokens'],'context_sha256':record['context_sha256'],
    'H_pre_marker_actual_LCP':front['H_pre_marker_actual_LCP'],'P_full_worker_mean_none':none['P_full_worker_mean'],
    'header_floor':front['H_pre_marker_actual_LCP']/none['P_full_worker_mean'],
    'none_hit':none['reusing_hit_rate'],'front_hit':front['reusing_hit_rate'],
    'none_uncached':none['uncached_tokens'],'front_uncached':front['uncached_tokens']}
   for f in [.1,.25,.5]:
    caf=front['cost_by_cache_fraction'][str(f)]/none['cost_by_cache_fraction'][str(f)]
    row[f'caf_f{f}']=caf;caf_data.append({'task':task,'instance_id':record['id'],'cache_price_fraction':f,'caf_cache':caf})
   for key in none['quality']:
    row[f'quality_{key}_none']=none['quality'][key];row[f'quality_{key}_front']=front['quality'][key]
   norm=normalize_trivia_answer if task=='triviaqa' else normalize_answer
   row['final_text_exact_equal']=none['final_answer']==front['final_answer']
   row['final_normalized_equal']=norm(none['final_answer'])==norm(front['final_answer'])
   pairs.append(row);data.append(row)
   benign.append({'task':task,'instance_id':record['id'],'cache_salt':none['cache_salt'],'N':5,'reusing_hit_rate':none['reusing_hit_rate'],
    'prompt_tokens':none['prompt_tokens'],'cached_tokens':none['cached_tokens'],'uncached_tokens':none['uncached_tokens'],
    'H_pre_marker_actual_LCP':none['H_pre_marker_actual_LCP'],'context_tokens':record['context_tokens'],'worker1_cold':True,
    'scope':'per-instance none worker usage only; aggregation excluded'})
  primary={'hotpotqa':'f1','triviaqa':'f1','gov_report':'rougeL','synthetic':'joint'}[task]
  none_hit=statistics.mean(x['none_hit'] for x in data);front_hit=statistics.mean(x['front_hit'] for x in data)
  cache_p=paired_test([x['front_hit']-x['none_hit'] for x in data],'less')
  cache_go=none_hit>=.90 and front_hit<none_hit and cache_p<.05
  task_quality=[]
  for metric in results['none']['quality']:
   a=[x[f'quality_{metric}_none'] for x in data];b=[x[f'quality_{metric}_front'] for x in data]
   test=mannwhitneyu(b,a,alternative='two-sided',method='asymptotic');delta=np.asarray(b)-np.asarray(a);low,high=interval(delta)
   effect=2*float(test.statistic)/(len(a)*len(b))-1
   sd=float(delta.std(ddof=1));dz=float(delta.mean())/sd if sd else 0.0 if float(delta.mean())==0 else math.copysign(math.inf,float(delta.mean()))
   entry={'task':task,'metric':metric,'n_pairs':30,'none_mean':statistics.mean(a),'front_mean':statistics.mean(b),'paired_delta_mean':float(delta.mean()),
    'paired_delta_ci95_low':low,'paired_delta_ci95_high':high,'mann_whitney_u_two_sided_p':float(test.pvalue),
    'rank_biserial_front_vs_none':effect,'paired_wilcoxon_two_sided_p':paired_test(delta),'paired_cohens_dz':dz}
   task_quality.append(entry)
  quality_go=all(x['mann_whitney_u_two_sided_p']>=.05 for x in task_quality)
  q=next(x for x in task_quality if x['metric']==primary)
  summary.append({'task':task,'n_instances':30,'primary_quality_metric':primary,'none_hit_mean':none_hit,'front_hit_mean':front_hit,
   'CAF_cache_f0.1_mean':statistics.mean(x['caf_f0.1'] for x in data),'CAF_cache_f0.25_mean':statistics.mean(x['caf_f0.25'] for x in data),
   'CAF_cache_f0.5_mean':statistics.mean(x['caf_f0.5'] for x in data),
   'none_quality_mean':q['none_mean'],'front_quality_mean':q['front_mean'],'paired_quality_delta':q['paired_delta_mean'],
   'quality_delta_ci95_low':q['paired_delta_ci95_low'],'quality_delta_ci95_high':q['paired_delta_ci95_high'],
   'mann_whitney_u_two_sided_p':q['mann_whitney_u_two_sided_p'],'rank_biserial_front_vs_none':q['rank_biserial_front_vs_none'],
   'cache_paired_wilcoxon_less_p':cache_p,'final_text_exact_equal_fraction':statistics.mean(x['final_text_exact_equal'] for x in data),
   'final_normalized_equal_fraction':statistics.mean(x['final_normalized_equal'] for x in data),'cache_verdict':'Go' if cache_go else 'No-Go',
   'quality_nonsignificance_verdict':'Go' if quality_go else 'No-Go','guide_double_verdict':'Go' if cache_go and quality_go else 'No-Go',
   'interpretation':'nonsignificance is not equivalence; bounded native-metric evidence','synthetic_unique_questions':5 if task=='synthetic' else 30})
  write_csv(ROOT/'tables'/f'{task}-quality-statistics.csv',task_quality)
 assert len(salt_set)==240 and request_count==1200 and aggregations==120
 write_csv(ROOT/'tables/task-generalization-summary.csv',summary);write_csv(ROOT/'tables/paired-instance-metrics.csv',pairs)
 write_csv(ROOT/'tables/benign-cache-health.csv',benign);write_csv(ROOT/'tables/agent-and-aggregate-quality.csv',qualities)
 write_csv(ROOT/'tables/caf-boxplot-data.csv',caf_data)
 save(ROOT/'evidence/formal-audit.json',{'formal_arms':240,'formal_worker_requests':request_count,'aggregation_requests':aggregations,'unique_worker_salts':len(salt_set),
  'all_worker1_cold':True,'all_prompt_ids_match_tokenize':True,'all_markers_before_context':True,'no_tools_in_answer_path':True,'deterministic_nonstream_settings':True,'completed_utc':now()})
 from .plots import plot
 plot()
 lines=['# 实验组T：任务泛化与输出质量（2026-10-05）','',
 '假设：前置零宽扰动降低共享检索上下文的缓存复用，但最终任务答案的原生质量指标无可检出的差异。',
 '正式实验使用Llama-3.3-70B-FP8、1×H100、TP=1、vLLM 0.11.2、APC、block16、BF16激活与KV、非流式、temperature=0、seed=42。答案直接从上下文生成，无工具，reflect_on_tool_use不适用。',
 'HotpotQA、GovReport、TriviaQA分别使用THUDM/LongBench原始子集中的30个真实样本。按原始文件顺序选择首30个共享上下文在原生tokenizer下为1024至29000 token的实例，不截断原始上下文、不依据答案质量挑样本。TriviaQA仅提取目标passage与目标问题，去除已有答案的演示样例；这属于有界长上下文子集，不代表完整数据集分布。',
 '合成锚点使用既有shared_doc校准为2000个原生增量chat token，5种固定问题各重复6次、独立salt；并非30个独立真实任务。合成行p值仅描述固定条件重复，不视为跨任务统计泛化证据。',
 '每实例两臂、每臂5个同构独立worker，共240臂、1200次worker请求；摘要和合成答案另外120次模型合并请求，完整单独保存，未混入worker CAF_cache。所有worker及模型合并请求使用同一2048-token上限。QA使用原生归一化后多数投票，平票按worker先后顺序；摘要与合成合并真实worker输出，最终聚合答案用于原生指标评分。',
 '前置标记沿用C的zwsp_k：U+200B重复worker序号次，后接换行，位于共享上下文体之前。两臂每实例独立salt，首worker缓存均为0；所有真实输入ID与/tokenize一致，装配探针通过。','',
 '|任务|none命中|front命中|CAF f=.1|质量指标|none质量|front质量|配对差|双侧MWU p|双判定|',
 '|---|---:|---:|---:|---|---:|---:|---:|---:|---|']
 for row in summary:
  lines.append(f'|{row["task"]}|{row["none_hit_mean"]:.4f}|{row["front_hit_mean"]:.4f}|{row["CAF_cache_f0.1_mean"]:.4f}|{row["primary_quality_metric"]}|{row["none_quality_mean"]:.4f}|{row["front_quality_mean"]:.4f}|{row["paired_quality_delta"]:+.4f}|{row["mann_whitney_u_two_sided_p"]:.6g}|{row["guide_double_verdict"]}|')
 lines+=['',
 'Mann–Whitney U按指南双侧检验；由于设计按实例配对，另报配对Wilcoxon、配对差的10000次bootstrap 95%区间及效应量。各原生指标的完整p值、rank-biserial及配对效应量见任务quality-statistics表。p≥0.05只表示本样本未检出差异，不能证明统计等价或所有输出完全不变；没有事后设定等价界限。多个指标的原始p值未做校正，判定按指南保留，解释时注意多重比较。',
 '缓存命中率排除冷worker1；全体uncached与成本包括worker1。CAF_cache=(uncached+f×cached)_front/(uncached+f×cached)_none，f∈{.1,.25,.5}。它是假定缓存价格下的输入成本放大，不是平台实付费用；输出及合并调用成本单列。',
 'GovReport为rouge-score 0.1.2、Porter stemming、标准ROUGE-1/2/L F1；QA各自使用官方归一化规则及最大gold alias EM/F1；合成沿用原Record引用/主题/完整解释筛选，不作为语义评判器。',
 '双重benign sanity在正式跑前人工阅读了每任务五个真实worker回答和聚合答案；结论及例子见evidence/sanity-review.json、sanity-examples.json。正式跑与sanity采用不同salt，sanity不计入正式数据。首批短预算试跑在HotpotQA第26例front响应达到96 token后被截断；全批次重启并统一2048-token上限，原255个已完成worker响应和1个截断响应保存在data/pilot-96-token-budget-*，未混入正式统计。该调整未改变样本、提示词、注入、温度或seed。',
 '本组只测受控RAG prepend的机制泛化及输出质量；没有验证单一资产端到端投递，也不将答案路径描述为工具原始输出。',
 '来源、原始样本与哈希见datasets/raw/source.json；筛选排除记录见datasets/selection-audit.json。初次下载后说明页403导致精确成功镜像URL未保留，已如实注明；哈希为本地文件身份，未对照上游发布摘要。',
 '逐实例cache/quality配对数据、逐agent质量、benign-cache-health.csv及CAF箱线图数据位于tables；PDF/PNG位于figures。完整服务配置、请求响应、prompt IDs、探针、源码与依赖锁均保存在该持久化实验目录。']
 (ROOT/'reports/实验记录_T任务数据集_20261005.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
 save(ROOT/'evidence/status.json',{'phase':'complete','formal_arms':240,'formal_worker_requests':1200,'aggregation_requests':120,'updated_utc':now(),'task_verdicts':{x['task']:x['guide_double_verdict'] for x in summary}})
 print('T_REPORT_COMPLETE',summary,flush=True)
if __name__=='__main__':report()

import statistics
from .common import *
def analyze():
 from .audit import audit_formal
 audit=audit_formal();data=[read(p) for p in (ROOT/'data/formal').glob('*/result.json')];rows=[];summary=[]
 for r in data:
  baseline=next(x for x in data if (x['g_tokens'],x['N'],x['repeat'],x['layout'],x['mode'])==(r['g_tokens'],r['N'],r['repeat'],r['layout'],'const-tool'))
  expected_loss=(r['N']-1)*(r['g_tokens']+(2000 if r['layout']=='prepend' else 0)) if r['mode']=='nonce-tool' else 0
  delta_prompt=r['prompt_tokens']-baseline['prompt_tokens'];cache_lost=baseline['cached_tokens']-r['cached_tokens']
  row={k:r[k] for k in ['name','g_tokens','S_tokens','N','repeat','layout','mode','tool_calls','prompt_tokens','cached_tokens','uncached_tokens','reusing_hit_rate']}
  row.update(benign_run=baseline['name'],lcp_worker2=r['workers'][1]['lcp_with_worker1'],cached_worker2=r['workers'][1]['cached_tokens'],header_or_history_prediction_hit=sum(w['block_prediction'] for w in r['workers'][1:])/sum(w['prompt_tokens'] for w in r['workers'][1:]),extra_prompt_tokens_vs_benign=delta_prompt,lost_reused_tokens=cache_lost,formula_lost_tokens=expected_loss,loss_relative_error=(cache_lost-expected_loss)/expected_loss if expected_loss else 0,engine_block_max_abs_error=max(abs(w['engine_minus_block_prediction']) for w in r['workers']))
  for f in [.1,.25,.5]:
   delta=r['costs'][str(f)]-baseline['costs'][str(f)]
   row[f'CAF_f{f}']=r['costs'][str(f)]/baseline['costs'][str(f)]
   row[f'deltaC_f{f}']=delta;row[f'deltaC_prompt_overhead_corrected_f{f}']=delta-delta_prompt;row[f'formula_deltaC_f{f}']=(1-f)*expected_loss
  rows.append(row)
 csvwrite(ROOT/'tables/append-long-runs.csv',sorted(rows,key=lambda r:r['name']))
 for g in [50,2000,4000]:
  for n in [5,20]:
   for l in ['prepend','append']:
    for m in ['const-tool','nonce-tool','broadcast']:
     part=[r for r in rows if (r['g_tokens'],r['N'],r['layout'],r['mode'])==(g,n,l,m)];assert len(part)==5
     q={'g_tokens':g,'S_tokens':2000,'N':n,'layout':l,'mode':m,'repeats':5}
     for key in ['reusing_hit_rate','uncached_tokens','lcp_worker2','cached_worker2','header_or_history_prediction_hit','lost_reused_tokens','formula_lost_tokens','loss_relative_error','CAF_f0.1','CAF_f0.25','CAF_f0.5','deltaC_f0.1','deltaC_prompt_overhead_corrected_f0.1','formula_deltaC_f0.1']:
      vals=[r[key] for r in part];q[key+'_mean']=statistics.mean(vals);q[key+'_sd']=statistics.stdev(vals)
     q['cache_verdict']='Go' if (m!='nonce-tool' or (all(r['CAF_f0.1']>1 for r in part) and max(r['engine_block_max_abs_error'] for r in part)<=32 and (g==50 or max(abs(r['loss_relative_error']) for r in part)<=.05))) else 'No-Go'
     if l=='append' and g==50 and m=='nonce-tool' and q['reusing_hit_rate_mean']<=.95:q['cache_verdict']='No-Go'
     summary.append(q)
 csvwrite(ROOT/'tables/append-long-summary.csv',summary)
 comparisons=[]
 for n in [5,20]:
  for l in ['prepend','append']:
   for rep in range(1,6):
    low=next(r for r in rows if (r['g_tokens'],r['N'],r['repeat'],r['layout'],r['mode'])==(2000,n,rep,l,'nonce-tool'))
    high=next(r for r in rows if (r['g_tokens'],r['N'],r['repeat'],r['layout'],r['mode'])==(4000,n,rep,l,'nonce-tool'))
    comparisons.append({'N':n,'layout':l,'repeat':rep,'delta_lost_tokens_G4000_minus_G2000':high['lost_reused_tokens']-low['lost_reused_tokens'],'formula_increment':(n-1)*2000,'measured_slope_per_G_token':(high['lost_reused_tokens']-low['lost_reused_tokens'])/2000,'formula_slope':n-1})
 csvwrite(ROOT/'tables/G-size-linear-contrast.csv',comparisons)
 from .plots import plot
 plot()
 lines=['# E5 append-G补充实验（2026-10-05）','','假设：append只保住共享历史S，仍损失分叉之后的G；broadcast移除逐副本分叉。',
 '单张H100，Llama-3.3-70B-FP8，TP=1，沿用T组vLLM 0.11.2/APC/block16/BF16激活与KV配置，非流式，temperature=0、top_p=1、seed=42。只看缓存，max_tokens=16，直接响应，不评价任务质量。',
 '用户补充优先于指南：S固定2000 token，N=5/20，G=2000/4000两档，另有50-token小G。两个布局均有确定性工具基线、逐调用nonce工具、单次调用broadcast。每格5次独立salt重复，180个正式run，2250次模型请求；90次sanity模型请求另存。',
 'G来自T组固定的首个GovReport上下文，按原生tokenizer截取并校准，不根据结果换文档。S来自既有shared_doc和固定任务前言。共享块的字节、原生token数与来源哈希保存于datasets/blocks.json。',
 '应用程序通过AutoGen FunctionTool.run_json调用同一个共享工具，工具不接收副本身份。nonce标记由内部调用计数生成并放在G开头；broadcast只调用一次，并原样复制返回字节。模型答案路径没有工具调用。确定性工具返回固定zwsp_1+换行+G，使首个冷请求和broadcast与基线字节一致。',
 '命名：旧marker-after/nonce-late在本报告中称marker-at-end，原始历史证据不改写。新的append-G是S后接G，标记在G开头；append-smallG是相同布局的小G对照，并不等同于旧臂。','','|G token|N|布局|臂|命中率|CAF f=.1|丢失复用token|公式预测|判定|','|---:|---:|---|---|---:|---:|---:|---:|---|']
 for q in summary:
  lines.append(f"|{q['g_tokens']}|{q['N']}|{q['layout']}|{q['mode']}|{q['reusing_hit_rate_mean']:.4f}|{q['CAF_f0.1_mean']:.4f}|{q['lost_reused_tokens_mean']:.1f}|{q['formula_lost_tokens_mean']:.1f}|{q['cache_verdict']}|")
 go=all(q['cache_verdict']=='Go' for q in summary)
 lines+=['',f'整体判定：{"Go" if go else "No-Go"}。判定依实际测量；若任一关键格失败，不改变布局以配合预期。',
 'B vs C：append布局下大G与50-token小G的命中率、CAF直接对比见表；小G近乎完全复用不应推导为append对大G免疫。',
 'A vs B：同一G下prepend丢失S+G，append保住S；两布局各自与自身确定性基线比较CAF，避免分母错配。',
 'ΔC原始值包含标记增加的prompt token；表另报扣除这项后的ΔC，与(N−1)(1−f)|G|或(N−1)(1−f)(|G|+|S|)比较。最大公共前缀对所有此前副本计算，缓存按16-token block取整；N=20时较长零宽标记可能共享额外block，已计入原生前缀与误差。',
 '公式是大G近似：大G丢失复用量相对误差≤5%，原生前缀缓存预测允许最多2个block差；这些阈值在正式跑前冻结。小G主要判据为append命中率>95%。CAF仅五/二十个副本的输入缓存费用模型，不是实际平台账单。',
 '每个run保存请求、响应、真实prompt_token_ids、tokenize结果、worker2 detokenize、工具调用与装配探针。审计确认S在append两臂中的原生token前缀相同，工具返回未被改写，worker1冷启动、salt唯一。',
 '本实验证“append将分叉之后的损失约束在G范围”；broadcast通过单次调用移除逐副本分叉，在本工具条件下形成免疫对照，不声称对所有动态应用绝对免疫。该补充仍是受控装配测量，没有额外证明模型自主工具调用或任意攻击资产的端到端投递。']
 (ROOT/'reports/实验记录_E5appendlong_20261005.md').write_text('\n'.join(lines)+'\n')
 save(ROOT/'evidence/status.json',{'phase':'complete','formal_runs':180,'formal_model_requests':2250,'Go':go,'updated_utc':now()});print('COMPLETE',go,audit,flush=True)
if __name__=='__main__':analyze()


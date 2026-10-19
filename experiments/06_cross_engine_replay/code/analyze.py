"""Recompute all reported input-cache costs from audited raw-result exports."""
from pathlib import Path
import json,csv,hashlib,sys,statistics
import numpy as np
R=Path(__file__).resolve().parents[1]
def read(p):return json.loads(p.read_text(encoding='utf-8'))
def write(p,x):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(x,ensure_ascii=False,indent=2),encoding='utf-8')
def stats(values,label):
 a=np.asarray(values,dtype=float);assert len(a)>0
 seed=42+int(hashlib.sha256(label.encode()).hexdigest()[:8],16)
 rng=np.random.default_rng(seed);boot=a[rng.integers(0,len(a),size=(10000,len(a)))].mean(axis=1)
 lo,hi=np.quantile(boot,[.025,.975]);return {'mean':float(a.mean()),'lo':float(lo),'hi':float(hi),'n':len(a)}
def metric(row,name,values):
 s=stats(values,str(sorted(row.items()))+name);row.update({name+'_'+k:v for k,v in s.items() if k!='n'})
def csvout(name,rows):
 (R/'tables').mkdir(exist_ok=True)
 fields=list(dict.fromkeys(k for r in rows for k in r))
 with (R/'tables'/name).with_suffix('.csv').open('w',encoding='utf-8',newline='') as f:
  w=csv.DictWriter(f,fields);w.writeheader();w.writerows(rows)
def latexout(name,rows,fields,caption,label,footnote=''):
 def esc(s):return str(s).replace('_',r'\_').replace('%',r'\%').replace('&',r'\&')
 headers={'model':'Model','position':'Position','engine':'Engine','independent_repeats':r'$n$','reusing_hit_rate_mean':r'Hit (\%)','uncached_tokens_mean':'Uncached tok.','N':r'$N$','f':r'$f$','sglang_CAF_mean':'CAF (SG)','vllm_CAF_mean':'CAF (vLLM)','CAF_relative_parity_error':r'Parity err. (\%)','G':r'$|G|$','layout':'Layout','arm':'Arm','sglang_hit_mean':r'Hit (\%)','sglang_CAF_f0.1_mean':'CAF (SG)','vllm_CAF_f0.1_mean':'CAF (vLLM)','sglang_loss_mean':'Lost tok.','granularity':'Page/block','loss_mean':'Lost tok.','loss_prediction':'Pred. tok.','loss_approx_relative_error':r'Loss err. (\%)','CAF_actual_mean':'CAF obs.','CAF_full_model_mean':'CAF model','CAF_full_model_relative_error':r'CAF err. (\%)','fixed_floor':r'Floor (\%)','none_hit_mean':r'None (\%)','front_hit_mean':r'Front (\%)','CAF_N20_f01_mean':r'CAF$_{20,.1}$'}
 pct={'reusing_hit_rate_mean','CAF_relative_parity_error','sglang_hit_mean','loss_approx_relative_error','CAF_full_model_relative_error','fixed_floor','none_hit_mean','front_hit_mean'}
 def fmt(v,k):
  if isinstance(v,float):return f'{100*v:.3f}' if k in pct else (f'{v:.0f}' if k in ['uncached_tokens_mean','sglang_loss_mean','loss_mean','loss_prediction'] else f'{v:.4f}')
  return esc({'qwen':'Qwen3-8B','llama':'Llama70B','sglang':'SGLang','const-tool':'Det.','nonce-tool':'Nonce','broadcast':'Bcast.','marker-at-end':'Marker end'}.get(str(v),v))
 lines=[r'\begin{table*}[t]',r'\centering',r'\caption{'+caption+r'}',r'\label{'+label+r'}',r'\scriptsize',r'\begin{tabular}{'+'l'*len(fields)+'}',r'\hline',' & '.join(headers.get(x,esc(x)) for x in fields)+r' \\',r'\hline']
 for r in rows:lines.append(' & '.join(fmt(r.get(k,''),k) for k in fields)+r' \\')
 lines += [r'\hline',r'\end{tabular}']
 if footnote:lines.append(r'\par\smallskip\parbox{0.98\linewidth}{\scriptsize '+footnote+'}')
 lines += [r'\end{table*}']
 (R/'tables'/name).with_suffix('.tex').write_text('\n'.join(lines)+'\n',encoding='utf-8')
def main():
 data=read(R/'data/compact-results.json');allr=data['results'];runs=[r for r in allr if r['page_size']==1]
 assert len(runs)==300 and data['audit']['token_exact_requests']>=3210
 assert all(r['attempt_valid'] and r['cold_replica_cached']==0 and r['token_exact_verified'] for r in runs)
 idx={(r['page_size'],r['key']):r for r in allr}
 pred=read(R/'config/preregistration.json');b1=[];b2=[];b3=[];b4=[];core=[];contrasts=[];slopes=[];perrun=[]
 def select(phase,model,**kw):return sorted([r for r in runs if r['phase']==phase and r['model']==model and all(r.get(k)==v for k,v in kw.items())],key=lambda r:r['repeat'])
 def pair(r):return idx[(r['page_size'],r['baseline_key'])]
 def caf(r,e,f):return r[e]['costs'][str(f)]/pair(r)[e]['costs'][str(f)]
 def loss(r,e):return pair(r)[e]['cached_tokens']-r[e]['cached_tokens']
 def full_model(r,e,f=.1):
  # Token-defined P,H frozen by the archived input; no engine measurement fit.
  # H is the mean maximal common prefix of attacking warm replicas.
  b=pair(r);n=r['N'];P=statistics.mean(w['lcp_prediction'] for w in b['workers'][1:]);s=b[e]['prompt_tokens']/n-P
  H=statistics.mean(w['lcp_prediction'] for w in r['workers'][1:]);D=P-H
  Cb=(P+s)+(n-1)*(s+f*P);Ca=(P+s)+(n-1)*(D+s+f*(P-D))
  overhead=r[e]['prompt_tokens']-b[e]['prompt_tokens']
  return {'P':P,'s':s,'H':H,'D':D,'CAF':Ca/Cb,'prompt_overhead':overhead,'CAF_with_prompt_overhead':(Ca+overhead)/Cb}
 for model in ['qwen','llama']:
  for pos in ['none','front','middle','marker-at-end']:
   part=select('B1',model,position=pos);assert len(part)==5
   for e in ['sglang','vllm']:
    n=part[0]['reference_n'] if e=='vllm' else 5
    measured=part if n==5 else part[:1]
    row={'model':model,'position':pos,'engine':e,'independent_repeats':n,'reference_n':part[0]['reference_n']}
    for m in ['reusing_hit_rate','reusing_replica_mean','aggregate_hit_rate','uncached_tokens']:metric(row,m,[r[e][m] for r in measured])
    if n==1:
     for k in list(row):
      if k.endswith('_lo') or k.endswith('_hi'):row[k]=''
    row['vllm_reference_note']='vLLM reference n=1; B replay n=5' if part[0]['reference_n']==1 else 'paired archived runs n=5'
    if e=='sglang':
     metric(row,'hit_parity_difference_pp',[100*(r['sglang']['reusing_hit_rate']-r['vllm']['reusing_hit_rate']) for r in part])
    b1.append(row)
  for n in [3,5,10,20]:
   part=select('B2',model,N=n,position='front');assert len(part)==5
   for f in [.1,.25,.5]:
    row={'model':model,'N':n,'f':f,'repeats':5,'parity_tolerance':.05}
    for e in ['sglang','vllm']:
     vals=[caf(r,e,f) for r in part];metric(row,e+'_CAF',vals)
     metric(row,e+'_uncached_ratio',[r[e]['uncached_tokens']/pair(r)[e]['uncached_tokens'] for r in part])
    row['CAF_relative_parity_error']=abs(row['sglang_CAF_mean']-row['vllm_CAF_mean'])/row['vllm_CAF_mean']
    row['parity_pass']=row['CAF_relative_parity_error']<=.05
    # Joint repeat-level bootstrap of paired engine CAF differences.
    a=np.array([caf(r,'sglang',f) for r in part]);b=np.array([caf(r,'vllm',f) for r in part]);rng=np.random.default_rng(42);j=rng.integers(0,5,(10000,5));err=(a[j].mean(1)-b[j].mean(1))/b[j].mean(1)
    row.update(relative_parity_signed_ci_lo=float(np.quantile(err,.025)),relative_parity_signed_ci_hi=float(np.quantile(err,.975)))
    b2.append(row)
  for e in ['sglang','vllm']:
   row={'engine':e,'model':model,'fixed_floor':pred['floor_prediction'][model],'cache_granularity':1 if e=='sglang' else 16}
   for pos in ['none','front']:
    part=select('B1',model,position=pos);metric(row,pos+'_hit',[r[e]['reusing_replica_mean'] for r in part])
   metric(row,'CAF_N20_f01',[caf(r,e,.1) for r in select('B2',model,N=20,position='front')]);core.append(row)
 for g in [50,2000,4000]:
  for n in [5,20]:
   for layout in ['prepend','append']:
    for mode in ['const-tool','nonce-tool','broadcast']:
     part=select('B3','llama',g_tokens=g,N=n,layout=layout,mode=mode);assert len(part)==5
     row={'G':g,'S':2000,'N':n,'layout':layout,'arm':mode,'repeats':5}
     expected=(n-1)*(g+(2000 if layout=='prepend' else 0)) if mode=='nonce-tool' else 0
     row['loss_prediction']=expected
     for e in ['sglang','vllm']:
      metric(row,e+'_hit',[r[e]['reusing_hit_rate'] for r in part]);metric(row,e+'_uncached',[r[e]['uncached_tokens'] for r in part]);metric(row,e+'_loss',[loss(r,e) for r in part])
      for f in [.1,.25,.5]:
       metric(row,e+'_CAF_f'+str(f),[caf(r,e,f) for r in part])
       metric(row,e+'_deltaC_f'+str(f),[r[e]['costs'][str(f)]-pair(r)[e]['costs'][str(f)] for r in part])
       metric(row,e+'_deltaC_minus_prompt_overhead_f'+str(f),[r[e]['costs'][str(f)]-pair(r)[e]['costs'][str(f)]-(r[e]['prompt_tokens']-pair(r)[e]['prompt_tokens']) for r in part])
      row[e+'_loss_relative_error']=abs(row[e+'_loss_mean']-expected)/expected if expected else ''
     for r in part:
      q={'key':r['key'],'page':r['page_size'],'G':g,'S':2000,'N':n,'layout':layout,'arm':mode,'repeat':r['repeat'],'cold_cached':r['cold_replica_cached'],'loss_prediction':expected,'input_overhead_vs_benign':r['sglang']['prompt_tokens']-pair(r)['sglang']['prompt_tokens']}
      for e in ['sglang','vllm']:
       q[e+'_loss']=loss(r,e)
       for f in [.1,.25,.5]:q[e+'_CAF_f'+str(f)]=caf(r,e,f)
      perrun.append(q)
     b3.append(row)
 for n in [5,20]:
  for g in [50,2000,4000]:
   pre=select('B3','llama',g_tokens=g,N=n,layout='prepend',mode='nonce-tool');app=select('B3','llama',g_tokens=g,N=n,layout='append',mode='nonce-tool')
   for e in ['sglang','vllm']:
    row={'engine':e,'G':g,'N':n,'S':2000,'expected_prepend_minus_append':(n-1)*2000}
    metric(row,'measured_contrast',[loss(p,e)-loss(a,e) for p,a in zip(pre,app)]);row['relative_error']=abs(row['measured_contrast_mean']-row['expected_prepend_minus_append'])/row['expected_prepend_minus_append'];contrasts.append(row)
  for layout in ['prepend','append']:
   small=select('B3','llama',g_tokens=2000,N=n,layout=layout,mode='nonce-tool');large=select('B3','llama',g_tokens=4000,N=n,layout=layout,mode='nonce-tool')
   for e in ['sglang','vllm']:
    row={'engine':e,'N':n,'layout':layout,'expected_slope':n-1};metric(row,'measured_slope',[(loss(b,e)-loss(a,e))/2000 for a,b in zip(small,large)]);slopes.append(row)
 for page,e in [(1,'vllm'),(1,'sglang'),(16,'sglang')]:
  for n in [5,20]:
   part=sorted([r for r in allr if r['page_size']==page and r['phase']=='B3' and r.get('g_tokens')==50 and r['N']==n and r['layout']=='append' and r['mode']=='nonce-tool'],key=lambda r:r['repeat'])
   if not part:continue
   assert len(part)==5
   row={'engine':e,'granularity':16 if e=='vllm' else page,'G':50,'N':n,'layout':'append','repeats':5,'loss_prediction':(n-1)*50}
   metric(row,'loss',[loss(r,e) for r in part]);row['loss_approx_relative_error']=abs(row['loss_mean']-row['loss_prediction'])/row['loss_prediction']
   metric(row,'CAF_actual',[caf(r,e,.1) for r in part]);metric(row,'CAF_full_model',[full_model(r,e)['CAF'] for r in part]);row['CAF_full_model_relative_error']=abs(row['CAF_actual_mean']-row['CAF_full_model_mean'])/row['CAF_full_model_mean']
   for k in ['P','s','H','D']:row[k]=statistics.mean(full_model(r,e)[k] for r in part)
   row['input_prompt_overhead_vs_benign']=statistics.mean(full_model(r,e)['prompt_overhead'] for r in part)
   metric(row,'CAF_model_with_prompt_overhead',[full_model(r,e)['CAF_with_prompt_overhead'] for r in part]);row['CAF_model_with_prompt_overhead_relative_error']=abs(row['CAF_actual_mean']-row['CAF_model_with_prompt_overhead_mean'])/row['CAF_model_with_prompt_overhead_mean']
   b4.append(row)
 csvout('B-I',b1);csvout('B-II',b2);csvout('B-III',b3);csvout('B-IV',b4);csvout('cross-engine',core);csvout('B3-layout-contrast',contrasts);csvout('B3-G-slope',slopes);csvout('B3-per-run',perrun)
 latexout('B-I',b1,['model','position','engine','independent_repeats','reusing_hit_rate_mean','uncached_tokens_mean'],'Marker-position token-exact replay','tab:B-I',r'Qwen middle/marker-at-end: vLLM reference $n=1$, SGLang isolated replay $n=5$. CSV contains bootstrap 95\% confidence intervals; $n=1$ reference has no CI.')
 latexout('B-II',b2,['model','N','f','sglang_CAF_mean','vllm_CAF_mean','CAF_relative_parity_error'],'Paired input-cost amplification and cross-engine parity','tab:B-II',r'CAF uses uncached $+f\cdot$cached input tokens and matched benign runs. Fixed parity tolerance is 5\%.')
 latexout('B-III',b3,['G','N','layout','arm','sglang_hit_mean','sglang_CAF_f0.1_mean','vllm_CAF_f0.1_mean','sglang_loss_mean'],'Original E5 layout and tool-arm replay','tab:B-III',r'S is the archived shared document plus task preamble; G is the archived GovReport slice. No source tokens were rebuilt.')
 latexout('B-IV',b4,['engine','granularity','N','loss_mean','loss_prediction','loss_approx_relative_error','CAF_actual_mean','CAF_full_model_mean','CAF_full_model_relative_error'],'Small-G loss approximation and full-CAF errors reported separately','tab:B-IV',r'The historical 28\% belongs to lost-token error, not CAF error. Full CAF uses token-defined P, H and s; marker prompt overhead is recorded separately.')
 latexout('cross-engine',core,['engine','model','fixed_floor','none_hit_mean','front_hit_mean','CAF_N20_f01_mean'],'Cross-engine generality at token-identical inputs','tab:B-cross-engine',r'Floors were fixed from archived tokens before SGLang observations. Native replay measures the cache layer, not AutoGen-on-SGLang integration.')
 h1=all(r['reusing_hit_rate_mean']>=.95 for r in b1 if r['engine']=='sglang' and r['position'] in ['none','marker-at-end']) and all(abs(r['reusing_replica_mean_mean']-pred['floor_prediction'][r['model']])<=.01 for r in b1 if r['engine']=='sglang' and r['position']=='front')
 h1=h1 and all(next(r['reusing_hit_rate_mean'] for r in b1 if r['engine']=='sglang' and r['model']==m and r['position']=='front')<next(r['reusing_hit_rate_mean'] for r in b1 if r['engine']=='sglang' and r['model']==m and r['position']=='middle')<next(r['reusing_hit_rate_mean'] for r in b1 if r['engine']=='sglang' and r['model']==m and r['position']=='none') for m in ['qwen','llama'])
 mono=all(all(a<b for a,b in zip(vals,vals[1:])) for model in ['qwen','llama'] for e in ['sglang','vllm'] for f in [.1,.25,.5] for vals in [[next(r[e+'_CAF_mean'] for r in b2 if r['model']==model and r['N']==n and r['f']==f) for n in [3,5,10,20]]])
 h2=all(r['parity_pass'] for r in b2) and mono
 large=all(r['sglang_loss_relative_error']<=.05 for r in b3 if r['arm']=='nonce-tool' and r['G']>=2000)
 contrastok=all(r['relative_error']<=.05 for r in contrasts if r['engine']=='sglang')
 controlok=all(r['sglang_hit_mean']>=.99 and abs(r['sglang_CAF_f0.1_mean']-1)<1e-6 for r in b3 if r['arm'] in ['const-tool','broadcast'])
 h3=large and contrastok and controlok
 base=next(r for r in b4 if r['engine']=='vllm' and r['N']==5);fine=next(r for r in b4 if r['engine']=='sglang' and r['granularity']==1 and r['N']==5)
 h4=fine['loss_approx_relative_error']<base['loss_approx_relative_error']
 h4_all_N=all(next(r['loss_approx_relative_error'] for r in b4 if r['engine']=='sglang' and r['granularity']==1 and r['N']==n)<next(r['loss_approx_relative_error'] for r in b4 if r['engine']=='vllm' and r['N']==n) for n in [5,20])
 h4_label='支持' if h4_all_N else ('部分支持（历史28%的N=5主对照支持；不能推广到所有N）' if h4 else '不支持')
 coarse=next((r for r in b4 if r['engine']=='sglang' and r['granularity']==16 and r['N']==5),None)
 conclusions={'H-B1':{'supported':h1},'H-B2':{'supported':h2,'parity_max_relative_error':max(r['CAF_relative_parity_error'] for r in b2),'both_engines_monotonic':mono},'H-B3':{'supported':h3,'large_G_loss_pass':large,'layout_contrast_pass':contrastok,'benign_broadcast_pass':controlok},'H-B4':{'supported':h4,'vllm_loss_error':base['loss_approx_relative_error'],'sglang_page1_loss_error':fine['loss_approx_relative_error'],'page16_directional_support':None if coarse is None else coarse['loss_approx_relative_error']>fine['loss_approx_relative_error']},'audit':data['audit'],'cache_vs_lcp_differences_count':len(data['cached_vs_exact_lcp_differences'])}
 write(R/'reports/conclusions.json',conclusions)
 conclusions['H-B4'].update(supported=h4_all_N,primary_N5_supported=h4,all_N_supported=h4_all_N,verdict=h4_label,N20_sglang_loss_error=next(r['loss_approx_relative_error'] for r in b4 if r['engine']=='sglang' and r['granularity']==1 and r['N']==20),N20_vllm_loss_error=next(r['loss_approx_relative_error'] for r in b4 if r['engine']=='vllm' and r['N']==20))
 conclusions.update(page1_cache_vs_lcp_differences_count=sum(x['page']==1 for x in data['cached_vs_exact_lcp_differences']),page16_rounding_differences_count=sum(x['page']==16 for x in data['cached_vs_exact_lcp_differences']))
 write(R/'reports/conclusions.json',conclusions)
 lines=['# B 组 SGLang 跨引擎实验结果（2026-10-06）','','已完成 B1→B2→B3：300 个有效隔离 run、3210 次原 token 重放，两个模型 B1/B2、Llama B3 180 run/2250 请求。所有首副本 cached=0，原始输入逐请求验证，旧 vLLM 代码/数据/环境未写入。', '', '| Engine | 模型 | 固定 floor | none | front | CAF N20,f=.1 |','|---|---|---:|---:|---:|---:|']
 for r in core:lines.append(f"| {r['engine']} | {r['model']} | {r['fixed_floor']:.4%} | {r['none_hit_mean']:.4%} | {r['front_hit_mean']:.4%} | {r['CAF_N20_f01_mean']:.4f} |")
 lines+=['','H-B1：'+('支持' if h1 else '不完全支持')+'。见 B-I 的良性、front 和 marker-at-end 对照。',f"H-B2：{'支持' if h2 else '不完全支持'}。逐格 CAF 最大 parity 偏差 {conclusions['H-B2']['parity_max_relative_error']:.3%}，预注册容差 5%；两引擎 CAF 随 N 单调增加：{mono}。",f"H-B3：{'支持' if h3 else '不完全支持'}。大 G 丢失量近似、prepend−append 对比、良性/broadcast 判据分别为 {large}/{contrastok}/{controlok}。",f"H-B4：{'支持' if h4 else '不支持'}。G=50,N=5 的丢失量误差：vLLM {base['loss_approx_relative_error']:.2%}，SGLang page1 {fine['loss_approx_relative_error']:.2%}。两项 CAF 模型误差另列 B-IV，不能将历史 28% 写成 CAF 误差。", '', '统计：每格五次独立隔离 run；95% bootstrap CI（10000 次、seed=42、run 为单位），CAF 使用同重复/同布局的良性分母。五次重放对相同 token 的缓存结果可完全相同，退化区间反映该测量的确定性，不代表跨数据集不确定性。Qwen middle/marker-at-end 的旧 vLLM 参考仅 n=1，参考 CI 留空，B 侧独立隔离重放 n=5；不能宣称两引擎各五次原独立样本。', '', '配置：SGLang 0.5.10，Triton 3.5.1 后端，Torch 2.9.1+cu128 / CUDA12.8 / 驱动575.57.08，1×H10080GB、TP1、page1、chunked-prefill2048、max-running1、context8192、BF16 激活和 KV、T0/seed42，两类 CUDA graph 均关闭。Qwen 使用已关闭 thinking 的原始 token；Llama 使用既有 ModelOpt FP8 权重、显式 modelopt_fp8。Qwen/Llama 静态显存预算 .90/.99，Llama .99 与旧单卡预算一致，运行前固定。所有请求保留原 max_tokens（B1/B2 2048，B3 16），没有以缩短解码改动重放。', '', '隔离：每 run flush_cache + 带外 UUID extra_key，不插任何盐 token。B3 S 保留旧 shared_doc 与任务前言，G 保留旧同一 GovReport；不按指南文字重建文档。B 仅为 native 引擎缓存层验证，不是 AutoGen-on-SGLang 端到端证据；CAF 是输入 token 计价模型，非平台实际账单。', '', '系统依赖：已从 Ubuntu 官方 jammy 源下载 libnuma1 2.0.14-3ubuntu2，包和共享库双 SHA256 校验，仅 B/runtime/lib 私有加载。来源、哈希及原失败记录见 B_stage_gate_report.md、system-packages/libnuma-manifest.json。FlashInfer SM90 JIT 报 structured binding cannot be captured，按指南切已预置 Triton；ninja PATH 修复、预热停滞与 tokenizer 返回类型修复均保留。', '', '完整 CAF 模型：B-IV 中 P=良性暖副本原 token 最大公共前缀均值，s=良性总 prompt/N−P，H=攻击暖副本原 token 最大公共前缀均值，D=P−H；代入指南含 H 完整式。H 从输入直接测量，不根据引擎 cached 调参。该标量近似未加入 nonce 导致的额外 prompt token，额外量另列 B3-per-run；B-IV 同时给出丢失量近似误差和完整 CAF 误差。', '', '交付：tables/B-I..IV.csv 与 .tex、cross-engine.csv/.tex、布局和线性对照、CAF-vs-N 图、reports/conclusions.json、全量原始 JSONL 及输入/响应、精确来源哈希。复算使用 code/analyze.py；单格重放使用 code/replay.py --key KEY（需先按模型/页大小启动服务，以新 --tag 保存，不覆盖旧结果）。完成后请用户复核再入稿。']
 if coarse:lines.insert(13,f"可选 page16 归因已完成；G50,N5 loss error={coarse['loss_approx_relative_error']:.2%}，相对 page1 的方向证据={conclusions['H-B4']['page16_directional_support']}。")
 for i,line in enumerate(lines):
  if line.startswith('H-B4：'):lines[i]=line.replace('支持。' if h4 else '不支持。',h4_label+'。',1)
 if not h4_all_N:
  lines+=['',f"H-B4边界：N=20时page1的丢失量误差 {conclusions['H-B4']['N20_sglang_loss_error']:.3%}，vLLM {conclusions['H-B4']['N20_vllm_loss_error']:.3%}。分页取整与原生marker/分隔符token残差可能相互抵消；不能宣称更细粒度在每个N上都更接近只计G的近似。B-IV完整列出两个N，H-B4总体记为部分支持。"]
 lines+=['', '补充复算（不替换预注册主判据）：B-IV.csv另给出将原token已知的nonce新增prompt量加入Ca后的CAF；该项是对标量完整式的输入开销修正，不根据缓存观测拟合。在G50,N5/N20中新增输入量分别为4/90 token。B-III另列原始ΔC及扣除该开销后的ΔC，便于区分输入变长与丢失缓存的成本。']
 ancillary=read(R/'evidence/ancillary-state.json');temperature=read(R/'data/ancillary/temperature-check.json');calibration=read(R/'data/ancillary/page16-calibration.json')
 assert ancillary['status']=='complete' and temperature['pass'] and calibration['pass']
 ancillary_note=f"补充对照：page16 完成 {ancillary['page16_runs']} run/{ancillary['page16_requests']} 次原 token 重放；六格标定通过，共享长度64/560/2000命中64/560/2000，分叉长度32/280/1000命中32/272/992。温度旁证共10请求，T=0与T=.7缓存计数均为{temperature['cached_T0']}，max_new_tokens=1、seed42、top_p1；这只验证缓存计数，不是QA投票或质量实验。"
 toolchain_note='编译工具链：系统 nvcc 12.1.105、GCC 11.4.0、ninja 1.13.2.git.kitware.jobserver-pipe-1；Torch 的运行时 CUDA 为12.8。版本差异与 FlashInfer 原始失败均已记录，未将其认定为唯一失败原因。证据见 evidence/toolchain-versions.json。官方系统库来源：https://archive.ubuntu.com/ubuntu/pool/main/n/numactl/libnuma1_2.0.14-3ubuntu2_amd64.deb 。'
 lines+=['',ancillary_note,'',toolchain_note]
 (R/'reports/B_full_report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
 short=lines[:10]+['',f"H-B1：{'支持' if h1 else '不完全支持'}。两模型 marker-at-end/良性均高于95%，middle 居中，front 对照事前固定 floor。",f"H-B2：{'支持' if h2 else '不完全支持'}。逐格 parity 最大偏差 {conclusions['H-B2']['parity_max_relative_error']:.3%}（预注册阈值5%），两引擎 CAF 随 N 单调增加={mono}。",f"H-B3：{'支持' if h3 else '不完全支持'}。append 丢失G、prepend多丢S；大G近似/布局差/良性与broadcast判据={large}/{contrastok}/{controlok}。",f"H-B4：{'支持' if h4 else '不支持'}。G50,N5丢失量误差：vLLM {base['loss_approx_relative_error']:.2%}，page1 {fine['loss_approx_relative_error']:.2%}；完整CAF误差分别 {base['CAF_full_model_relative_error']:.3%}/{fine['CAF_full_model_relative_error']:.3%}，B-IV分列。",'', 'SGLang0.5.10/Triton，单H100/TP1，page1，分块2048，单请求，BF16激活/KV，T0/seed42，两类CUDA graph关闭；Llama使用原FP8权重。隔离仅flush+extra_key，不插盐token；B3重放旧E5原token。', '', '每格5轮、run级10000次bootstrap95%CI。Qwen middle/marker-at-end的vLLM参考仅n=1，已在B-I脚注标明。B是native缓存层验证，CAF是输入费用模型。', '', '系统库libnuma1 2.0.14-3ubuntu2：Ubuntu官方包、包/库双SHA256、B私有加载；FlashInfer编译失败后切预置Triton，全部修复与失败证据留存。', '', '交付：tables/B-I..IV.csv/.tex、cross-engine.csv/.tex、布局与线性对照、figures/CAF-vs-N.pdf/.png、全量JSONL、配置/代码/来源哈希。详见reports/B_full_report.md；复核后再入稿。']
 if coarse:short.insert(14,f"page16可选归因已完成：G50,N5丢失量误差 {coarse['loss_approx_relative_error']:.2%}，高于page1的方向证据={conclusions['H-B4']['page16_directional_support']}。")
 for i,line in enumerate(short):
  if line.startswith('H-B4：'):short[i]=line.replace('支持。' if h4 else '不支持。',h4_label+'。',1)
 if not h4_all_N:short+=['',f"N20边界：page1/vLLM丢失量误差为 {conclusions['H-B4']['N20_sglang_loss_error']:.2%}/{conclusions['H-B4']['N20_vllm_loss_error']:.2%}；取整与marker原生token残差有抵消，逐档如实报告。"]
 short+=['', '补充：page16 60 run/750请求及六格标定通过；10请求温度旁证中T0/T.7缓存计数相同。系统nvcc12.1.105与Torch CUDA12.8分别记录，完整环境证据见报告。']
 (R/'B_results_summary.md').write_text('\n'.join(short)+'\n',encoding='utf-8')
 qcore=next(r for r in core if r['model']=='qwen' and r['engine']=='sglang');lcore=next(r for r in core if r['model']=='llama' and r['engine']=='sglang')
 manuscript=f'''# Cross-Engine Generality — insertion draft for user review

We replayed the archived vLLM prompt-token sequences directly through SGLang 0.5.10's native generation endpoint on a single H100-80GB. The experiment used a Triton attention backend, token-granularity radix caching (page size 1), BF16 activations and KV storage, and sequential dispatch. Run isolation used a cache flush and an out-of-band extra_key; no salt tokens were added to the replayed prompts. Before formal measurements, shared-prefix and forked-prefix calibration confirmed meta_info.cached_tokens at lengths 64, 560, and 2000. Across 300 formal runs and 3210 requests, all first replicas were cold and each input was checked against the archived token IDs. This is a serving-cache-layer validation rather than an AutoGen-on-SGLang end-to-end integration experiment.

For Qwen3-8B and Llama-3.3-70B-FP8, front markers reduced reusing-replica hit rates to {qcore['front_hit_mean']:.2%} and {lcore['front_hit_mean']:.2%}, respectively, matching the token-defined predictions fixed before SGLang measurements. Deterministic and marker-at-end controls remained above 99%. With N=20 and cached-token price fraction f=0.1, SGLang CAF was {qcore['CAF_N20_f01_mean']:.2f} and {lcore['CAF_N20_f01_mean']:.2f}; all paired cross-engine CAF cells differed by at most {conclusions['H-B2']['parity_max_relative_error']:.2%}, within the preregistered 5% tolerance. CAF increased with N for both engines and all three price fractions.

Original E5 token replay preserved the layout contrast: append protected the shared history while losing the grounding suffix; prepend additionally lost approximately (N-1)|S| cached tokens. Large-grounding-size losses followed the additive model, and deterministic and broadcast arms retained high reuse. Small-grounding approximation errors must be distinguished from full-CAF errors. At |G|=50,N=5, lost-token error fell from {base['loss_approx_relative_error']:.0%} in vLLM to {fine['loss_approx_relative_error']:.0%} in SGLang page1. However, the N=20 errors were {conclusions['H-B4']['N20_vllm_loss_error']:.2%} and {conclusions['H-B4']['N20_sglang_loss_error']:.2%}, respectively: block rounding can cancel marker/delimiter token residuals, so finer granularity does not uniformly improve the G-only approximation at every N. These results support the cache-bypass mechanism and paired input-cost model across block-hash and radix cache implementations, while bounding the granularity claim.

Table note: Qwen middle/marker-at-end vLLM references each contain only one archived run (n=1); SGLang performs five independently isolated replays of those fixed inputs. Bootstrap confidence intervals use runs, not individual replicas, as the resampling unit. CAF is an input-token price model and is not a measured platform bill. Full engine parameters, the verified private libnuma repair, and FlashInfer failure/fallback evidence accompany the artifact.
'''
 if coarse:
  manuscript+='\nThe supplementary page-size-16 replay (60 runs, 750 requests) reproduced the archived vLLM small-G losses exactly: 256 tokens for N=5 and 992 for N=20, versus 228 and 1012 with page size 1. Six page-size calibration cases passed. A separate ten-request temperature check gave identical cached-token counts at T=0 and T=0.7; this check concerns prefill counters only and does not evaluate QA voting.\n'
 (R/'reports/manuscript-insertion.md').write_text(manuscript,encoding='utf-8')
 print(json.dumps(conclusions,ensure_ascii=False,indent=2))
if __name__=='__main__':main()

from pathlib import Path
import numpy as np,json,hashlib,time
from detector import vector,score
R=Path(__file__).resolve().parents[1]
def read(p):return json.loads(p.read_text(encoding='utf-8'))
def auc(y,s):
 p=np.asarray(s)[np.asarray(y)==1];n=np.asarray(s)[np.asarray(y)==0]
 return float(np.mean((p[:,None]>n)+(p[:,None]==n)*.5)) if len(p) and len(n) else None
def threshold(negative,alpha):
 values=sorted(negative);assert values;return float(values[max(0,int(np.ceil((1-alpha)*len(values)))-1)])
def tree(X,y,depth):
 prob=float(np.mean(y));node={'probability':prob,'samples':len(y)}
 if depth==0 or prob in [0,1] or len(y)<8:return node
 impurity=prob*(1-prob);best=None
 for f in range(X.shape[1]):
  u=np.unique(X[:,f]);ts=(u[:-1]+u[1:])/2
  for t in ts:
   mask=X[:,f]<=t;nl=int(mask.sum());nr=len(y)-nl
   if min(nl,nr)<3:continue
   a=float(np.mean(y[mask]));b=float(np.mean(y[~mask]));gain=impurity-(nl*a*(1-a)+nr*b*(1-b))/len(y)
   if best is None or gain>best[0]:best=(gain,f,float(t),mask)
 if best is None or best[0]<=1e-12:return node
 _,f,t,mask=best;return {'feature':f,'threshold':t,'samples':len(y),'left':tree(X[mask],y[mask],depth-1),'right':tree(X[~mask],y[~mask],depth-1)}
def main():
 ds=read(R/'datasets/H2-dataset.json');train=[r for r in ds if r['split']=='train'];cal=[r for r in ds if r['split']=='calibration']
 assert not (R/'config/frozen-detector.json').exists(),'Freeze is immutable; do not refit silently'
 assert not (R/'evidence/stage2-process.json').exists(),'No evasion can precede freeze'
 assert {r['group'] for r in train}.isdisjoint({r['group'] for r in cal})
 X=np.asarray([vector(r) for r in train]);y=np.asarray([r['label'] for r in train]);neg=X[y==0];med=np.median(neg,axis=0);scale=np.maximum(np.std(neg,axis=0),1e-8)
 models=[{'name':'joint-OR','type':'joint-OR','medians':med.tolist(),'scales':scale.tolist()}]+[{'name':f'joint-CART-depth{d}','type':'tree','depth':d,'root':tree(X,y,d)} for d in [3,5]]
 candidates=[];cal_y=[r['label'] for r in cal]
 for m in models:
  scores=[score(m,r) for r in cal];thresholds={str(a):threshold([s for s,r in zip(scores,cal) if r['label']==0],a) for a in [.01,.05]};t=thresholds['0.01'];pred=np.asarray(scores)>t
  candidates.append({'name':m['name'],'model':m,'thresholds':thresholds,'calibration_AUC':auc(cal_y,scores),'calibration_TPR_1pct':float(pred[np.asarray(cal_y)==1].mean()),'calibration_FPR_1pct':float(pred[np.asarray(cal_y)==0].mean())})
 selected=max(enumerate(candidates),key=lambda z:(z[1]['calibration_TPR_1pct'],z[1]['calibration_AUC'],-z[0]))[1]
 mean_model={'type':'single','feature':0};mean_t=threshold([score(mean_model,r) for r in cal if r['label']==0],.01)
 delta=lambda r:max([max(0,(p-q)/r['prompt'][0]) for q,p in zip(r['prompt'],r['prompt'][1:])] or [0])
 frozen={'schema':1,'frozen_unix':time.time(),'source_dataset_sha256':hashlib.sha256((R/'datasets/H2-dataset.json').read_bytes()).hexdigest(),'selection':'maximum calibration TPR@empirical FPR<=1%, AUC tie break, then simpler; no test/evasion data','feature_order':['1-mean','1-min','below-.95-fraction','variance'],'selected_model':selected['model'],'joint_thresholds':selected['thresholds'],'mean_benign_band':[1-mean_t,1.0],'health_floor':.95,'candidates':candidates,'single_models':{name:{'model':{'type':'single','feature':i},'thresholds':{str(a):threshold([vector(r)[i] for r in cal if r['label']==0],a) for a in [.01,.05]}} for i,name in enumerate(['mean','minimum','below-floor','variance'])},'H_relative_delta_threshold':threshold([delta(r) for r in cal if r['label']==0],.01),'counts':{s:{'rows':sum(r['split']==s for r in ds),'groups':len({r['group'] for r in ds if r['split']==s})} for s in ['train','calibration','test']},'scope':'strongest preregistered four-feature candidate on development data; no assertion of globally optimal detector'}
 (R/'config/frozen-detector.json').write_text(json.dumps(frozen,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps({k:frozen[k] for k in ['selected_model','joint_thresholds','mean_benign_band','counts','H_relative_delta_threshold']},ensure_ascii=False)[:2400])
if __name__=='__main__':main()

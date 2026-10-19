"""Temperature cached-token check and optional page16 small-G attribution."""
from pathlib import Path
import sys,json,time,uuid,traceback
sys.path.insert(0,str(Path(__file__).resolve().parent))
from engine import R,ensure,save
from replay import call,read,run_cell
def temperature_check():
 ensure('llama',1)
 run=next(r for r in read(R/'datasets/replay-plan.json')['runs'] if r['phase']=='B1' and r['model']=='llama' and r['position']=='none' and r['repeat']==1)
 rows=[]
 for temp in [0,.7]:
  call('/flush_cache');key=uuid.uuid4().hex
  for i,w in enumerate(run['workers'],1):
   payload={'input_ids':w['input_ids'],'extra_key':key,'sampling_params':{'temperature':temp,'sampling_seed':42,'top_p':1,'max_new_tokens':1}}
   response=json.loads(call('/generate',payload));rows.append({'temperature':temp,'worker':i,'request':payload,'response':response})
 a=[r['response']['meta_info']['cached_tokens'] for r in rows if r['temperature']==0];b=[r['response']['meta_info']['cached_tokens'] for r in rows if r['temperature']==.7]
 save(R/'data/ancillary/temperature-check.json',{'pass':a==b,'cached_T0':a,'cached_T07':b,'rows':rows,'scope':'native prefill cache counter only; not QA voting/accuracy'})
 assert a==b and a[0]==0
 print('TEMPERATURE CHECK PASS',a,flush=True)
def calibrate_page16():
 run=next(r for r in read(R/'datasets/replay-plan.json')['runs'] if r['phase']=='B1' and r['model']=='llama' and r['position']=='none')
 ids=run['workers'][0]['input_ids'];rows=[]
 for L in [64,560,2000]:
  for direction in ['shared','fork']:
   call('/flush_cache');key=uuid.uuid4().hex;a=ids[:L]+[1,2,3]
   b=ids[:L]+[4,5,6] if direction=='shared' else ids[:L//2]+[(ids[L//2]+1)%128000]+ids[L//2+1:L]+[4,5,6]
   lcp=L if direction=='shared' else L//2;expected=lcp//16*16
   responses=[]
   for seq in [a,b]:
    p={'input_ids':seq,'extra_key':key,'sampling_params':{'temperature':0,'sampling_seed':42,'max_new_tokens':1}}
    response=json.loads(call('/generate',p));assert response['meta_info']['prompt_tokens']==len(seq);responses.append({'request':p,'response':response})
   assert responses[0]['response']['meta_info']['cached_tokens']==0
   measured=responses[1]['response']['meta_info']['cached_tokens'];rows.append({'L':L,'direction':direction,'lcp':lcp,'expected':expected,'cached':measured,'requests':responses})
   save(R/'data/ancillary/page16-calibration-progress.json',rows)
   assert measured==expected,('page16 calibration mismatch',L,direction,measured,expected)
 save(R/'data/ancillary/page16-calibration.json',{'pass':True,'cases':rows,'cached_field_path':'meta_info.cached_tokens'})
 print('PAGE16 ALL SIX CALIBRATIONS PASS',flush=True)
def main():
 assert read(R/'evidence/formal-state.json')['status']=='B1-B3-complete'
 temperature_check()
 try:
  ensure('llama',16);calibrate_page16()
  part=[r for r in read(R/'datasets/replay-plan.json')['runs'] if r['phase']=='B3' and r['g_tokens']==50]
  assert len(part)==60 # 2N x 2layout x 3arms x 5 repeats
  for r in sorted(part,key=lambda x:(x['repeat'],x['key'])):run_cell(r,16)
  save(R/'evidence/ancillary-state.json',{'status':'complete','page16_runs':60,'page16_requests':750,'temperature_requests':10})
 except Exception as exc:
  save(R/'evidence/ancillary-state.json',{'status':'page16-unavailable-or-failed','error':str(exc),'formal_B1_B3_complete':True});traceback.print_exc();raise
if __name__=='__main__':main()

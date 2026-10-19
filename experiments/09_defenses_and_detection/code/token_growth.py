"""Persistbill Eq.3 exactly: strict/moderate/relaxed, without threshold tuning."""
PRESETS={'strict':(5000,2,2000),'moderate':(10000,3,2000),'relaxed':(50000,10,2000)}
def original_d2(prompt_tokens,preset='moderate'):
 assert len(prompt_tokens)>=1 and prompt_tokens[0]>0
 absolute,ratio,minimum=PRESETS[preset];p1=prompt_tokens[0]
 return [{'turn':i+1,'delta':p-previous,'delta_over_p1':(p-previous)/p1,'trigger':(p-previous)>absolute or ((p-previous)/p1>ratio and (p-previous)>minimum)} for i,(previous,p) in enumerate(zip(prompt_tokens,prompt_tokens[1:]),1)]
if __name__=='__main__':
 assert not any(r['trigger'] for r in original_d2([100,800,1500,2200]))
 assert original_d2([100,3000])[0]['trigger']
 assert not original_d2([100,2100])[0]['trigger']
 print('Original Eq.3 boundary checks pass')

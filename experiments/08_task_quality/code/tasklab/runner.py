"""Sequential five-worker pairs; resumable only at completed arm boundaries."""
import argparse,uuid,time,traceback,shutil
from .common import ROOT,MODEL,api,tokenize,save,read,now,lcp,TASKS,digest
from .prompts import messages,aggregation_messages
from .metrics import score,vote,normalize_answer,normalize_trivia_answer

def generate(dest,stem,msgs,salt,max_tokens):
 request={'model':MODEL,'messages':msgs,'temperature':0,'seed':42,'max_tokens':max_tokens,'stream':False,
          'cache_salt':salt,'return_token_ids':True}
 save(dest/(stem+'.request.json'),request)
 tk=tokenize(messages=msgs);save(dest/(stem+'.tokenize.json'),tk)
 response=api('/v1/chat/completions',request);save(dest/(stem+'.response.json'),response)
 assert response['prompt_token_ids']==tk['tokens']
 assert response['choices'][0]['finish_reason']=='stop', 'Incomplete generation: budget-limited answer must be investigated'
 message=response['choices'][0]['message'];text=message.get('content') or ''
 assert text.strip() and not (message.get('reasoning') or message.get('reasoning_content'))
 assert '<think>' not in text and '</think>' not in text
 usage=response['usage'];cached=usage['prompt_tokens_details']['cached_tokens']
 assert type(cached)is int and 0<=cached<=usage['prompt_tokens']==len(tk['tokens'])
 return text,usage,tk['tokens']

def arm_run(record,arm,dest):
 if (dest/'result.json').exists():return read(dest/'result.json')
 if dest.exists():
  failed=ROOT/'data/interrupted'/f'{record["task"]}-{record["id"]}-{arm}-{uuid.uuid4().hex[:8]}'
  failed.parent.mkdir(parents=True,exist_ok=True);shutil.move(str(dest),str(failed))
 dest.mkdir(parents=True)
 salt=uuid.uuid4().hex
 save(dest/'manifest.json',{'task':record['task'],'instance_id':record['id'],'arm':arm,'n_workers':5,'cache_salt':salt,
  'context_sha256':record['context_sha256'],'context_tokens':record['context_tokens'],'started_utc':now(),'tools':[],
  'reflect_on_tool_use':'not applicable; direct answers without tools','worker_system_identical':True,'instance': '<instance-id>'})
 outputs=[];workers=[];ids=[]
 for worker in range(1,6):
  msgs=messages(record,worker,arm);budget=2048
  text,usage,tokens=generate(dest,f'{worker:04d}-worker_{worker}',msgs,salt,budget)
  cached=usage['prompt_tokens_details']['cached_tokens']
  if worker==1:assert cached==0,'Salt isolation/cold first worker failed'
  outputs.append(text);ids.append(tokens)
  workers.append({'worker':worker,'prompt_tokens':usage['prompt_tokens'],'cached_tokens':cached,'uncached_tokens':usage['prompt_tokens']-cached,
                  'completion_tokens':usage['completion_tokens'],'quality':score(record,text),'answer':text})
 h=lcp(tokenize(messages=messages(record,1,'front'))['tokens'],tokenize(messages=messages(record,2,'front'))['tokens'])
 if record['task'] in ['gov_report','synthetic']:
  final,agg_usage,agg_ids=generate(dest,'aggregate',aggregation_messages(outputs,synthetic=record['task']=='synthetic'),uuid.uuid4().hex,2048)
  save(dest/'aggregation.json',{'method':'model merge of genuine worker answers','cache_metrics_scope':'worker requests only; aggregation usage separately recorded','usage':agg_usage})
 else:
  final=vote(outputs,record['task']);normalizer=normalize_answer if record['task']=='hotpotqa' else normalize_trivia_answer
  save(dest/'aggregation.json',{'method':'majority vote on native normalized answer, deterministic first-worker tie break','votes':[normalizer(x) for x in outputs]})
 (dest/'final-answer.txt').write_text(final,encoding='utf-8')
 total_prompt=sum(w['prompt_tokens'] for w in workers);total_cache=sum(w['cached_tokens'] for w in workers)
 result={'task':record['task'],'instance_id':record['id'],'arm':arm,'cache_salt':salt,'workers':workers,
   'reusing_hit_rate':sum(w['cached_tokens'] for w in workers[1:])/sum(w['prompt_tokens'] for w in workers[1:]),
   'prompt_tokens':total_prompt,'cached_tokens':total_cache,'uncached_tokens':total_prompt-total_cache,
   'cost_by_cache_fraction':{str(f):total_prompt-total_cache+f*total_cache for f in [.1,.25,.5]},
   'H_pre_marker_actual_LCP':h,'P_full_worker_mean':total_prompt/5,'final_answer':final,'quality':score(record,final),'completed_utc':now()}
 save(dest/'attack-metrics.json',result);save(dest/'result.json',result)
 return result

def main(mode):
 if mode=='formal':
  approval=read(ROOT/'evidence/sanity-review.json')
  assert approval['passed']
 done=0;examples=[]
 for task in TASKS:
  records=read(ROOT/'datasets/processed'/f'{task}.json')
  for record in (records[:3] if task in ['hotpotqa','triviaqa'] else records[:1]) if mode=='sanity' else records:
   arms=['none'] if mode=='sanity' else ['none','front']
   for arm in arms:
    dest=ROOT/'data'/mode/task/record['id']/arm
    result=arm_run(record,arm,dest);done+=1
    if mode=='sanity':examples.append({'task':task,'id':record['id'],'hit':result['reusing_hit_rate'],'answers':[w['answer'] for w in result['workers']], 'final_answer':result['final_answer'],'quality':result['quality'],'golds':record['answers']})
    save(ROOT/'evidence/status.json',{'phase':mode,'completed_arms':done,'expected_arms':8 if mode=='sanity' else 240,'task':task,'instance':record['id'],'arm':arm,'updated_utc':now()})
    print('SAVED',mode,done,task,record['id'],arm,'hit',result['reusing_hit_rate'],'quality',result['quality'],flush=True)
 if mode=='sanity':save(ROOT/'evidence/sanity-examples.json',examples)
 else:
  from .analysis import report
  report()

if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('mode',choices=['sanity','formal']);args=parser.parse_args()
 try:main(args.mode)
 except Exception as e:
  save(ROOT/'evidence/failure.json',{'mode':args.mode,'error':str(e),'traceback':traceback.format_exc(),'utc':now()});raise

"""Real formal Engine/HTTP path with explicitly synthetic server/tokenizer/events."""
import os,pathlib,sys,asyncio,json,time,tempfile,hashlib,types,unittest
REV=pathlib.Path(os.environ['G_FORMAL_REVISION_ROOT']);OUT=pathlib.Path(os.environ['G_FORMAL_TEST_OUTPUT']);sys.path.insert(0,str(REV/'code'));OUT.mkdir(parents=True,exist_ok=True)
stub=types.ModuleType('transformers');stub.AutoTokenizer=object;sys.modules['transformers']=stub
from formal_core import *
from gengine import Engine
from gformal_backend import RealBackend
from gslots import SlotClients
import httpx
IDS=list(range(100))
class MockServer:
 async def __aenter__(self):
  self.calls=0;self.tasks=set();self.writers=set();self.server=await asyncio.start_server(self.connection,'127.0.0.1',0);self.url='http://127.0.0.1:'+str(self.server.sockets[0].getsockname()[1]);return self
 async def __aexit__(self,*args):
  self.server.close();await self.server.wait_closed()
  for w in list(self.writers):w.close()
  for t in list(self.tasks):t.cancel()
  await asyncio.gather(*list(self.tasks),return_exceptions=True)
 async def connection(self,reader,writer):
  task=asyncio.current_task();self.tasks.add(task);self.writers.add(writer)
  try:
   while True:
    head=await reader.readuntil(b'\r\n\r\n');parts=head.decode().split('\r\n');path=parts[0].split()[1];h={k.lower().strip():v.strip() for k,v in (p.split(':',1) for p in parts[1:] if ':' in p)};raw=await reader.readexactly(int(h.get('content-length',0)))
    if path=='/health':data=b'OK'
    else:
     assert path=='/v1/chat/completions';b=json.loads(raw);assert b['seed']==42 and b['max_tokens']==16 and b['stream'] is False;self.calls+=1;await asyncio.sleep(.004)
     data=json.dumps(dict(id='chatcmpl-'+h['x-request-id'],prompt_token_ids=IDS,usage=dict(prompt_tokens=100,prompt_tokens_details=dict(cached_tokens=0),completion_tokens=2),choices=[dict(finish_reason='stop',message=dict(content='OK'))])).encode()
    writer.write(f'HTTP/1.1 200 OK\r\nContent-Length: {len(data)}\r\nConnection: keep-alive\r\n\r\n'.encode()+data);await writer.drain()
  except (ConnectionError,asyncio.IncompleteReadError):pass
  finally:writer.close();self.writers.discard(writer);self.tasks.discard(task)
class Integration(unittest.IsolatedAsyncioTestCase):
 async def test_real_formal_engine_round_all18_cells(self):
  async with MockServer() as s:
   with tempfile.TemporaryDirectory(dir=OUT) as tmp:
    root=pathlib.Path(tmp);e=Engine();e.label='llama';e.run_id='formal-CPU-HTTP';e.experiment_id='G_FORMAL_CPU_HTTP';e.folder=root/'segments'/e.run_id;e.folder.mkdir(parents=True);e.round_folder=root;e.trace=e.folder/'traces';e.trace.mkdir();e.model='G-llama';e.kwargs={};e.guard=lambda:None;e.active_info=None;e.capture_sends=[];e.request_count=0;e.rounds=[]
    e.docs=read(REV/'data/documents.json')[:1];e.inputs=read(REV/'data/inputs.json');e.tokenizer=types.SimpleNamespace(apply_chat_template=lambda *a,**k:IDS[:] if k.get('tokenize') else 'synthetic rendered prompt');e.prefill_reference=dict(model='llama',seconds=.4,frozen_before_pilot=True,synthetic_CPU_only=True)
    e.transport=httpx.AsyncHTTPTransport(retries=0,limits=httpx.Limits(max_connections=32,max_keepalive_connections=32,keepalive_expiry=120));e.client=httpx.AsyncClient(base_url=s.url,transport=e.transport,trust_env=False,timeout=600);e.slots=SlotClients(s.url,timeout=600)
    async def drain():return {'vllm:num_requests_running':0,'vllm:num_requests_waiting':0}
    async def reset(dest):
     info=e.active_info;ids=['chatcmpl-G-'+e.run_id+'-'+info['attempt_id']+'-'+str(i+1) for i in range(info['N'])];events=[]
     def add(kind,t,**kw):events.append(dict(kind=kind,t_ns=t,pid=999+info['position'],seq=len(events)+1,**kw))
     for rid in ids:add('request_added',1,request_id=rid,status='WAITING');add('status_transition',2,request_id=rid,new='RUNNING',old='WAITING')
     groups=[[rid] for rid in ids] if info['schedule']=='S' else [ids]
     for j,group in enumerate(groups):
      t=10+j*10;sid='synthetic-'+str(j);add('scheduled_step',t,step_id=sid,scheduled_tokens={rid:16 for rid in group},snapshot={'running':[dict(id=r,status='RUNNING') for r in group]});add('executor_begin',t+1,step_id=sid);add('executor_end',t+5,step_id=sid,error=None);add('update_begin',t+6,step_id=sid,output_request_ids=group);add('update_done',t+7,step_id=sid)
     for rid in ids:add('status_transition',1000,request_id=rid,new='FINISHED_STOPPED',old='RUNNING')
     (e.trace/('events-synthetic-'+str(info['position'])+'.jsonl')).write_text(''.join(json.dumps(x)+'\n' for x in events));x=dict(before=await drain(),after=await drain(),engine_reset=dict(success=True),synthetic_CPU_reset=True);save(dest/'isolation.json',x);return x
    e.reset=reset;e.drain=drain;b=RealBackend.__new__(RealBackend);b.e=e
    cells=[dict(position=i+1,document_id=e.docs[0]['id'],repeat_id=1,N=n,schedule=sch,arm=a) for i,(n,sch,a) in enumerate((n,sch,a) for n in (5,20) for sch in ('S','P','C') for a in ('D','A','B'))]
    r=Runner(root,dict(experiment_id=e.experiment_id),cells,verify_source(REV),b,lambda:None)
    try:
     counts=await r.run(e.run_id);self.assertEqual(counts['first']['attempts'],18);self.assertEqual(s.calls,225);self.assertEqual(e.request_count,225);self.assertEqual(counts['first']['invalid_or_failed'],0)
     rows,_=r.ledger.selected();self.assertEqual(sum(x['tool_call_count'] for x in rows),156)
     for c in cells:
      dest=root/'rounds'/(logical(e.experiment_id,c)+'-a1');req=[read(p) for p in dest.glob('*.request-record.json')]
      self.assertGreaterEqual(min(x['response_parse_started_monotonic'] for x in req),max(x['transport_returned_monotonic'] for x in req));self.assertTrue(all(x['phase']=='formal' for x in req))
     save(OUT/'formal-real-HTTP-evidence.json',dict(passed=True,cells=18,HTTP_mock_requests=225,real_tool_calls=156,round_attempt_method_mocked=False,production_RealBackend_execute_used=True,runner_ledger_validator_used=True,synthetic_server_tokenizer_native_events_reset=True,GPU_requests=0,physical_performance_claim=False))
    finally:await b.close()
if __name__=='__main__':unittest.main(verbosity=2)

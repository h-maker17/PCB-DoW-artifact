"""Unit-only transport/schema stub. Formal integration must use the shared public stub.
Toolcall ID matches the public fixed ID; native citation UUIDs remain untouched.
"""
from isolation import prepare,ROOT
prepare()
import contextlib,json,re,secrets,threading,time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
class LoggingStub:
 def __init__(self,query,*,artifacts=None,replica=None):
  from run_artifacts import RunArtifacts
  self.query=query;self.events=[];self.lock=threading.Lock();self.replica=replica
  self.artifacts=artifacts or RunArtifacts.preparation('model-unit');self.log_path=self.artifacts.new_log('model-http')
 def completion(self,body):
  functions={x['function']['name']:x['function'] for x in body.get('tools',[]) if x.get('type')=='function'}
  if 'knowledge_search' not in functions:raise ValueError('Actual native knowledge_search schema absent')
  schema=functions['knowledge_search']['parameters']
  if schema.get('properties',{}).get('query',{}).get('type')!='string' or 'query' not in schema.get('required',[]):raise ValueError('Unexpected native query schema')
  # Injected runtime must not become a model argument.
  if 'runtime' in schema.get('properties',{}):raise ValueError('Injected runtime leaked into model schema')
  tool_messages=[x for x in body['messages'] if x['role']=='tool' and x.get('name','knowledge_search')=='knowledge_search']
  if not tool_messages:
   return {'role':'assistant','content':None,'tool_calls':[{'id':'call_scan_fixed','type':'function','function':{'name':'knowledge_search','arguments':json.dumps({'query':self.query})}}]},'tool_calls'
  text=str(tool_messages[-1]['content'])
  links=re.findall(r'\[citation:[^\]]+\]\(#knowledge-[^)]+\)',text)
  if not links:raise ValueError('Native tool did not return model-visible citation; error preserved')
  return {'role':'assistant','content':'Contract test completed using retrieved evidence. '+links[0]},'stop'
 @contextlib.contextmanager
 def serving(self):
  owner=self
  class Handler(BaseHTTPRequestHandler):
   def log_message(self,*args):pass
   def do_POST(self):
    raw=self.rfile.read(int(self.headers['Content-Length'])).decode('utf8');body=json.loads(raw)
    try:
     if self.path!='/v1/chat/completions':raise ValueError('Unsupported model endpoint')
     message,finish=owner.completion(body);record={'run_id':owner.artifacts.run_id,'phase':owner.artifacts.phase,'kind':'model_http','replica':owner.replica if owner.replica is not None else self.headers.get('X-SCAN-Replica'),'raw_body_text':raw,'request':body,'response_message':message,'finish_reason':finish}
     with owner.lock:
      owner.events.append(record)
      owner.artifacts.append(owner.log_path,record)
     ident='scan_canned';stamp=0
     if body.get('stream'):
      self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
      delta=dict(message);delta.pop('role',None)
      if 'tool_calls' in delta:delta['tool_calls']=[{'index':i,**x} for i,x in enumerate(delta['tool_calls'])]
      for d,stop in [(dict(role='assistant',**delta),None),({},finish)]:
       chunk={'id':ident,'object':'chat.completion.chunk','created':stamp,'model':body['model'],'choices':[{'index':0,'delta':d,'finish_reason':stop}]}
       self.wfile.write(('data: '+json.dumps(chunk)+'\n\n').encode());self.wfile.flush()
      self.wfile.write(b'data: [DONE]\n\n');self.wfile.flush()
     else:
      payload={'id':ident,'object':'chat.completion','created':stamp,'model':body['model'],'choices':[{'index':0,'message':message,'finish_reason':finish}],'usage':{'prompt_tokens':0,'completion_tokens':0,'total_tokens':0}}
      b=json.dumps(payload).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
    except Exception as exc:
     b=json.dumps({'error':{'message':str(exc),'type':'contract_error'}}).encode();self.send_response(400);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
  server=ThreadingHTTPServer(('127.0.0.1',0),Handler);t=threading.Thread(target=server.serve_forever,daemon=True);t.start()
  try:yield f'http://127.0.0.1:{server.server_port}/v1'
  finally:server.shutdown();server.server_close();t.join()


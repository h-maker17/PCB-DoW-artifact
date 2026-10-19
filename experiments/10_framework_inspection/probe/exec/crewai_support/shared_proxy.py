"""Transport-only connection to the unchanged shared ProbeContext stub/CPU encoder."""
import base64,hashlib,http.client,json,struct,threading,urllib.parse
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
class SharedStubProxy:
    def __init__(self,config):self.config=config;self.receipts=[];self.lock=threading.Lock()
    def record(self,value):
        with self.lock:self.receipts.append(value)
    def __enter__(self):
        owner=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                raw=self.rfile.read(int(self.headers['Content-Length']));identity=self.headers.get('X-SCAN-Replica');body=json.loads(raw)
                try:
                    if self.path=='/v1/embeddings':
                        if body.get('model')!='text-embedding-3-small' or 'dimensions' in body:raise ValueError('Embedding literal/dimensions drift')
                        texts=body['input'];texts=[texts] if isinstance(texts,str) else texts
                        if not texts or not all(isinstance(x,str) for x in texts):raise ValueError('Expected native text batch')
                        from native_bridge import rpc
                        vectors=rpc(owner.config,'embeddings',{'texts':texts,'normalize_embeddings':False},replica=int(identity) if identity is not None else None)['vectors']
                        if len(vectors)!=len(texts) or any(len(v)!=384 for v in vectors):raise ValueError('Real CPU embedding dimension mismatch')
                        fmt=body.get('encoding_format','float')
                        if fmt not in ('float','base64'):raise ValueError('Unsupported encoding_format')
                        out={'object':'list','model':body['model'],'data':[{'object':'embedding','index':i,'embedding':base64.b64encode(struct.pack('<384f',*v)).decode() if fmt=='base64' else v} for i,v in enumerate(vectors)],'usage':{'prompt_tokens':0,'total_tokens':0}}
                        payload=json.dumps(out).encode();status=200;ctype='application/json'
                        owner.record({'kind':'actual_shared_cpu_embedding','native_request_raw':raw.decode(),'dimensions':384,'normalize_embeddings':False,'vectors_sha256':hashlib.sha256(json.dumps(vectors,separators=(',',':')).encode()).hexdigest(),'usage_count':'unmeasured zero protocol placeholder','replica_header':identity})
                    elif self.path=='/v1/chat/completions':
                        if identity is None:raise ValueError('Missing external branch observation header')
                        if body.get('model')!='gpt-4o-mini' or body.get('temperature')!=0:raise ValueError('Native LLM defaults drift')
                        url=urllib.parse.urlparse(owner.config['base_url']);conn=http.client.HTTPConnection(url.hostname,url.port,timeout=120)
                        conn.request('POST',url.path+'/chat/completions',body=raw,headers={'Content-Type':'application/json','X-SCAN-Replica':identity})
                        response=conn.getresponse();payload=response.read();status=response.status;ctype=response.getheader('Content-Type','application/json');conn.close()
                        owner.record({'kind':'unchanged_common_logging_stub_forward','replica':int(identity),'request_raw_body':raw.decode(),'request_sha256':hashlib.sha256(raw).hexdigest(),'response_raw_body':payload.decode(),'status':status,'request_rewritten':False,'response_rewritten':False})
                    else:raise ValueError('Undeclared route '+self.path)
                except Exception as exc:
                    payload=json.dumps({'error':{'message':str(exc)}}).encode();status=422;ctype='application/json';owner.record({'kind':'transport_error','error':str(exc)})
                self.send_response(status);self.send_header('Content-Type',ctype);self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.base_url=f'http://127.0.0.1:{self.server.server_port}/v1';return self
    def __exit__(self,*args):
        self.server.shutdown();self.server.server_close();self.thread.join(timeout=5)
        if self.thread.is_alive():raise RuntimeError('Own proxy thread did not exit')

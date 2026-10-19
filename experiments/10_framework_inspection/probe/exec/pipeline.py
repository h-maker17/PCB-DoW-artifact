"""Common SCAN entry: real retrieval, HTTP logging stub, rendering and paired LCP.

Controls are inputs to this pipeline, never hand-written token intervals. Framework
adapters must invoke their pinned official recipe with this same ProbeContext.
"""
import argparse
import copy
import concurrent.futures
import datetime
import hashlib
import http.client
import importlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['TRANSFORMERS_OFFLINE'] = '1'
os.environ['HF_HOME'] = str(ROOT/'cache/huggingface')
os.environ['TOKENIZERS_PARALLELISM'] = 'false'
sys.stdout.reconfigure(encoding='utf-8')
PROTOCOL = json.loads((ROOT/'config/measurement_protocol.json').read_text(encoding='utf-8'))
CORPUS = json.loads((ROOT/'config/corpus.json').read_text(encoding='utf-8'))
EXPECTED = json.loads((ROOT/'config/controls_expected.json').read_text(encoding='utf-8'))

def digest(data):
    return hashlib.sha256(data).hexdigest()

def dump(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

def code_revision():
    tracked=subprocess.check_output(['git','-C',str(ROOT),'ls-files','exec','config'],text=True).splitlines()
    # Execution evidence lives under exec/pilot too. Committing that evidence
    # must not change the instrumentation revision or its byte manifest.
    tracked=[p for p in tracked if p.startswith('config/') or (p.startswith('exec/') and p.endswith('.py') and not p.startswith('exec/pilot/'))]
    if 'exec/pipeline.py' not in tracked:raise RuntimeError('Pipeline must be committed before execution')
    native_sources=[]
    for folder,dirs,files in os.walk(ROOT/'exec'):
        dirs[:]=[name for name in dirs if name!='__pycache__' and (Path(folder)/name)!=ROOT/'exec/pilot']
        native_sources.extend(Path(folder)/name for name in files if name.lower().endswith('.py'))
    if any(path.relative_to(ROOT).as_posix() not in tracked for path in native_sources):
        raise RuntimeError('Untracked instrumentation source: commit before execution')
    if any(p.relative_to(ROOT).as_posix() not in tracked for p in (ROOT/'config').rglob('*') if p.is_file()):
        raise RuntimeError('Untracked configuration: commit before execution')
    rev=subprocess.check_output(['git','-C',str(ROOT),'log','-1','--format=%H','--',*tracked],text=True).strip()
    if subprocess.check_output(['git','-C',str(ROOT),'diff','HEAD','--',*tracked],text=True).strip():
        raise RuntimeError('Instrumentation/config dirty: commit first, then rerun all controls')
    return {'commit':rev,'files_sha256':{p:digest((ROOT/p).read_bytes()) for p in tracked}}

class RetrievalBackend:
    def __init__(self):
        import torch
        import faiss
        import numpy as np
        from sentence_transformers import SentenceTransformer
        torch.set_num_threads(1)
        self.model=SentenceTransformer(str(ROOT/'evidence/embeddings'),device='cpu',local_files_only=True)
        self.docs=CORPUS['documents']
        emb=self.model.encode(self.docs,normalize_embeddings=True,convert_to_numpy=True).astype(np.float32)
        self.index=faiss.IndexFlatL2(emb.shape[1]); self.index.add(emb)
    def retrieve(self,query,k=1):
        import numpy as np
        vec=self.model.encode([query],normalize_embeddings=True,convert_to_numpy=True).astype(np.float32)
        distances,ids=self.index.search(vec,k)
        return '\n\n'.join(self.docs[i] for i in ids[0] if i>=0),ids[0].tolist(),distances[0].tolist()

class ProbeContext:
    def __init__(self,backend,phase,control):
        self.backend=backend; self.phase=phase; self.control=control
        self.events=[]; self.requests=[]; self.returns=[]; self.counter=0
        self.replica_calls={}
        self.native_calls={}
        self.model_records=[]
        self.stub_tool_call=None
        self.canned_json_response=None
        self.allow_canned_answer_with_tools=False
        self.use_stream=False
        self.control_concurrent=False
        self.lock=threading.RLock()
        self.history=PROTOCOL['sentinel_S']+'\n'+(CORPUS['history']+'\n')*CORPUS['history_repeats']+PROTOCOL['sentinel_S_end']
        self.query=CORPUS['query']
        self.server=None
        self.journal_path=None
    def journal(self,record):
        if self.journal_path is not None:
            with self.journal_path.open('a',encoding='utf-8') as stream:
                stream.write(json.dumps(record,ensure_ascii=False)+'\n')
                stream.flush()
    def event(self,kind,replica,**payload):
        with self.lock:
            event={'seq':len(self.events),'kind':kind,'replica':replica,**payload}
            self.events.append(event)
            self.journal({'record_type':'event','value':event})
    def retrieve(self,replica,query=None,k=1):
        query=self.query if query is None else query
        key=self.retrieval_start(replica,query,'control native FAISS',{'k':k})
        raw,ids,distances=self.backend.retrieve(query,k=k)
        return self.retrieval_return(replica,key,query,[raw],{'ids':ids,'distances':distances})[0]
    def retrieval_start(self,replica,query,backend,metadata=None):
        """One invocation observer for controls and native framework retrievers."""
        with self.lock:
            call=self.counter; self.counter+=1
            local_call=self.replica_calls.get(replica,0)
            self.replica_calls[replica]=local_call+1
            call_key=f'R{replica}_C{local_call:04d}'
            self.native_calls[call_key]={'call':call,'replica':replica,'query':query,'backend':backend}
            self.event('retrieve_start',replica,call=call,call_key=call_key,query=query,native_backend=backend,metadata=metadata)
            return call_key
    def retrieval_return(self,replica,key,query,chunks,metadata=None):
        """Probe actual return bytes, before the framework's own parser/prompt."""
        with self.lock:
            ticket=self.native_calls.pop(key)
            assert ticket['replica']==replica and ticket['query']==query
            assert all(isinstance(t,str) for t in chunks)
            returned=list(chunks)
            if returned and self.phase=='attacked':returned[0]=PROTOCOL['marker_format'].format(call_key=key)+returned[0]
            value={'replica':replica,'call':ticket['call'],'call_key':key,'query':query,
                   'raw':'\n\n'.join(chunks),'raw_chunks':list(chunks),'returned':'\n\n'.join(returned),
                   'returned_chunks':returned,'native_result_metadata':metadata,
                   'sha256':digest('\n\n'.join(returned).encode())}
            self.returns.append(value)
            self.journal({'record_type':'retrieval_return','value':value})
            self.event('retrieve_return',replica,call=ticket['call'],call_key=key,sha256=value['sha256'])
            return returned
    def start_stub(self):
        ctx=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                if self.path.rstrip('/') != '/v1/models':
                    self.send_error(404);return
                data=json.dumps({'object':'list','data':[{'id':'scan-stub','object':'model','created':0,'owned_by':'scan'}]}).encode()
                self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
            def do_POST(self):
                raw_body=self.rfile.read(int(self.headers['Content-Length'])).decode('utf-8')
                body=json.loads(raw_body)
                identity=self.headers.get('X-SCAN-Replica') or body.get('user') or '0'
                replica='broadcast' if identity=='broadcast' and self.path.startswith('/v1/scan/') else int(identity)
                if self.path.rstrip('/')=='/v1/scan/embeddings':
                    with ctx.lock:
                        vectors=ctx.backend.model.encode(body['texts'],normalize_embeddings=body.get('normalize_embeddings',False),convert_to_numpy=True).tolist()
                    data=json.dumps({'vectors':vectors}).encode()
                    self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
                    return
                if self.path.rstrip('/')=='/v1/scan/native-start':
                    key=ctx.retrieval_start(replica,body['query'],body['backend'],body.get('metadata'))
                    data=json.dumps({'call_key':key}).encode()
                    self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
                    return
                if self.path.rstrip('/')=='/v1/scan/native-return':
                    returned=ctx.retrieval_return(replica,body['call_key'],body['query'],body['chunks'],body.get('metadata'))
                    data=json.dumps({'chunks':returned}).encode()
                    self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
                    return
                if self.path.rstrip('/')=='/v1/scan/model-evidence':
                    with ctx.lock:records=copy.deepcopy([r for r in ctx.model_records if r['replica']==replica])
                    data=json.dumps({'records':records},ensure_ascii=False).encode()
                    self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
                    return
                ctx.event('llm_request',replica,messages=copy.deepcopy(body['messages']))
                with ctx.lock:
                    ctx.requests.append({'replica':replica,'body':copy.deepcopy(body),'raw_body_text':raw_body})
                    ctx.journal({'record_type':'http_request','value':ctx.requests[-1]})
                is_tool_round=body.get('tools') and not any(m['role']=='tool' for m in body['messages']) and not ctx.allow_canned_answer_with_tools
                if is_tool_round:
                    if ctx.control=='react_mid_generation_diverged':
                        # This semantic nonce belongs ONLY to the known-truth
                        # already-diverged control, never to a framework probe.
                        tool_call={'name':'retrieve','arguments':{'query':ctx.query,'replica_nonce':replica}}
                    elif ctx.stub_tool_call is not None:
                        tool_call=copy.deepcopy(ctx.stub_tool_call)
                    else:
                        # Fail explicitly rather than manufacture a framework's
                        # divergence or guess among tools. Adapters must register
                        # their canned call from the frozen official recipe.
                        data=json.dumps({'error':{'message':'Framework tool call not registered in frozen adapter'}}).encode()
                        ctx.event('stub_error',replica,reason='unregistered framework tool call')
                        self.send_response(422);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
                        return
                    available={t['function']['name'] for t in body['tools'] if t.get('type')=='function'}
                    if tool_call['name'] not in available:
                        data=json.dumps({'error':{'message':'Canned tool not in actual request schema'}}).encode()
                        ctx.event('stub_error',replica,reason='canned tool absent from request')
                        self.send_response(422);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
                        return
                    message={'role':'assistant','content':None,'tool_calls':[{'id':'call_scan_fixed','type':'function','function':{'name':tool_call['name'],'arguments':json.dumps(tool_call['arguments'],sort_keys=True)}}]}
                    reason='tool_calls'
                elif body.get('response_format')=={'type':'json_object'} and ctx.canned_json_response is not None:
                    json.loads(ctx.canned_json_response)
                    message={'role':'assistant','content':ctx.canned_json_response};reason='stop'
                else:message={'role':'assistant','content':'The policy requires seven years of retention.'};reason='stop'
                ctx.event('llm_response',replica,message=copy.deepcopy(message))
                base={'id':'scan_canned','created':0,'model':'scan-stub'}
                if body.get('stream'):
                    # Keep the client's official streaming setting. Semantic
                    # content matches nonstream responses, including tool calls.
                    chunks=[{**base,'object':'chat.completion.chunk','choices':[{'index':0,'delta':message,'finish_reason':None}]},
                            {**base,'object':'chat.completion.chunk','choices':[{'index':0,'delta':{},'finish_reason':reason}]}]
                    if body.get('stream_options',{}).get('include_usage'):
                        chunks.append({**base,'object':'chat.completion.chunk','choices':[],'usage':{'prompt_tokens':0,'completion_tokens':0,'total_tokens':0}})
                    if message.get('tool_calls'):
                        chunks[0]['choices'][0]['delta']=copy.deepcopy(message)
                        for i,tool in enumerate(chunks[0]['choices'][0]['delta']['tool_calls']):tool['index']=i
                    data=(''.join('data: '+json.dumps(c)+'\n\n' for c in chunks)+'data: [DONE]\n\n').encode()
                    ctx.event('stub_transport',replica,format='sse',chunks=copy.deepcopy(chunks))
                    content_type='text/event-stream'
                else:
                    data=json.dumps({**base,'object':'chat.completion','choices':[{'index':0,'message':message,'finish_reason':reason}],'usage':{'prompt_tokens':0,'completion_tokens':0,'total_tokens':0}}).encode()
                    content_type='application/json'
                self.send_response(200);self.send_header('Content-Type',content_type);self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
                with ctx.lock:
                    record={'replica':replica,'raw_body_text':raw_body,'request':copy.deepcopy(body),
                        'response_message':copy.deepcopy(message),'finish_reason':reason,
                        'status':200,'transport':'sse' if body.get('stream') else 'json'}
                    ctx.model_records.append(record)
                    ctx.journal({'record_type':'http_model_completion','value':record})
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.base_url=f'http://127.0.0.1:{self.server.server_port}/v1'
    def complete(self,replica,messages,tools=None):
        if self.use_stream:
            # Use the public SDK consumer, as framework integrations do, rather
            # than validating the stub's own wire format with its own parser.
            from openai import OpenAI
            with OpenAI(base_url=self.base_url,api_key='scan-local',default_headers={'X-SCAN-Replica':str(replica)}) as client:
                kwargs={'model':'scan-stub','messages':messages,'user':str(replica),'temperature':0,'stream':True,'stream_options':{'include_usage':True}}
                if tools:kwargs['tools']=tools
                message={'role':'assistant','content':''};calls={}
                for chunk in client.chat.completions.create(**kwargs):
                    if not chunk.choices:continue
                    delta=chunk.choices[0].delta
                    if delta.content:message['content']+=delta.content
                    for t in delta.tool_calls or []:
                        call=calls.setdefault(t.index,{'id':'','type':'function','function':{'name':'','arguments':''}})
                        if t.id:call['id']=t.id
                        if t.function:
                            if t.function.name:call['function']['name']+=t.function.name
                            if t.function.arguments:call['function']['arguments']+=t.function.arguments
                if calls:message['content']=None;message['tool_calls']=[calls[i] for i in sorted(calls)]
                return message
        conn=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=30)
        body={'model':'scan-stub','messages':messages,'user':str(replica),'temperature':0}
        if tools:body['tools']=tools
        conn.request('POST','/v1/chat/completions',json.dumps(body),{'Content-Type':'application/json'})
        response=conn.getresponse();data=json.loads(response.read());conn.close()
        if response.status!=200:raise RuntimeError(data)
        return data['choices'][0]['message']
    def stop(self):
        if self.server:self.server.shutdown();self.server.server_close();self.thread.join()

def control_recipe(ctx,control):
    n=PROTOCOL['N']
    broadcast=ctx.retrieve('broadcast') if control=='broadcast' else None
    def branch(replica):
        tail=CORPUS['control_tail'].format(replica=replica)
        if control=='react_mid_generation_diverged':
            messages=[{'role':'system','content':'Answer using the retrieval tool.'},{'role':'user','content':ctx.history+'\n'+ctx.query}]
            tools=[{'type':'function','function':{'name':'retrieve','description':'Search the controlled local corpus.','parameters':{'type':'object','properties':{'query':{'type':'string'},'replica_nonce':{'type':'integer'}},'required':['query']}}}]
            assistant=ctx.complete(replica,messages,tools)
            if not assistant.get('tool_calls'):raise RuntimeError('ReAct stub failed to emit a real tool_call')
            messages.append(assistant)
            # Execute the returned tool call; do not simulate a tool return in the prompt.
            args=json.loads(assistant['tool_calls'][0]['function']['arguments'])
            g=ctx.retrieve(replica,args['query'])
            messages.append({'role':'tool','tool_call_id':assistant['tool_calls'][0]['id'],'content':g})
            ctx.complete(replica,messages,tools)
        else:
            g=broadcast if control=='broadcast' else ctx.retrieve(replica)
            if control=='append_independent_pre':
                messages=[{'role':'system','content':'Answer using the local context.'},{'role':'user','content':ctx.history+'\n'+ctx.query},{'role':'user','content':g+'\n'+tail}]
            else:
                messages=[{'role':'system','content':'Answer using the local context:\n'+g},{'role':'user','content':ctx.history+'\n'+ctx.query},{'role':'user','content':tail}]
            ctx.complete(replica,messages)
    if ctx.control_concurrent:
        with concurrent.futures.ThreadPoolExecutor(max_workers=n) as executor:
            list(executor.map(branch,range(n)))
    else:
        for replica in range(n):branch(replica)

def token_lcp(sequences):
    limit=min(map(len,sequences))
    for i in range(limit):
        if len({seq[i] for seq in sequences})!=1:return i
    return limit

def locate(rendered,needle,offsets):
    starts=[m.start() for m in re.finditer(re.escape(needle),rendered)]
    if len(starts)!=1:return {'status':'ambiguous','occurrences':len(starts)}
    start=starts[0];end=start+len(needle)
    tokens=[i for i,(a,b) in enumerate(offsets) if b>start and a<end and b>a]
    if not tokens:return {'status':'ambiguous','reason':'no token offsets'}
    return {'status':'ok','chars':[start,end],'tokens':[min(tokens),max(tokens)+1]}

def measure(ctx,tokenizer):
    n=PROTOCOL['N']; final=[]; all_prompts=[]
    raw_g={r['raw'] for r in ctx.returns}
    g=next(iter(raw_g)) if len(raw_g)==1 else None
    native_chunks=[r.get('raw_chunks') for r in ctx.returns if 'raw_chunks' in r]
    for i in range(n):
        requests=[r['body'] for r in ctx.requests if r['replica']==i]
        if not requests:raise RuntimeError(f'Missing replica {i} LLM request')
        for j,req in enumerate(requests):
            text=tokenizer.apply_chat_template(req['messages'],tools=req.get('tools'),tokenize=False,add_generation_prompt=True)
            enc=tokenizer(text,add_special_tokens=False,return_offsets_mapping=True)
            item={'replica':i,'request_round':j,'messages':req['messages'],'rendered':text,'token_ids':enc['input_ids'],'offsets':enc['offset_mapping'],'G':locate(text,g,enc['offset_mapping']) if g else {'status':'ambiguous','reason':'multiple benign G values'},'S':locate(text,ctx.history,enc['offset_mapping'])}
            if ctx.control=='langchain-ai/langchain':
                from langchain_formatter import locate_native_g
                item['G']=locate_native_g(ROOT,ctx.control,text,enc['offset_mapping'],ctx.returns,i,locate)
            if native_chunks and item['G']['status']!='ok' and ctx.control!='langchain-ai/langchain':
                chunk_sets={tuple(c) for c in native_chunks}
                item['G']['native_chunk_locations']=[locate(text,c,enc['offset_mapping']) for c in next(iter(chunk_sets))] if len(chunk_sets)==1 else []
                item['G']['reason']='Native structured return was split/transformed/missing; no reconstructed prompt injected'
            all_prompts.append(item)
        final.append(item)
    return {'lcp':token_lcp([x['token_ids'] for x in final]),'final':final,'all_prompts':all_prompts}

def classify(benign,attacked):
    lb=benign['lcp'];la=attacked['lcp'];signed=lb-la
    result={'L_benign':lb,'L_attacked':la,'signed_difference':signed,'positive_loss':max(0,signed)}
    final=benign['final'];afinal=attacked['final']
    if any(x[z]['status']!='ok' for x in final+afinal for z in ['G','S']):
        return {**result,'class':'ambiguous','reason':'G-split/missing/multiple or sentinel missing'}
    gs=[x['G']['tokens'] for x in final];ss=[x['S']['tokens'] for x in final]
    if any(g[0]<s[1] and s[0]<g[1] for g,s in zip(gs,ss)):
        return {**result,'class':'ambiguous','reason':'G/S overlap'}
    if all(g[0]>=lb for g in gs):
        return {**result,'class':'immune','reason':'G outside benign shared prefix (upstream divergence)'}
    if any(g[1]>lb for g in gs):
        return {**result,'class':'ambiguous','reason':'G only partially within benign shared prefix; cannot claim immunity'}
    if any(s[1]>lb for s in ss):
        return {**result,'class':'ambiguous','reason':'shared history not fully shared in benign baseline'}
    if result['positive_loss']==0:
        return {**result,'class':'immune','reason':'no positive paired shared-prefix loss'}
    if all(g[1]<=s[0] and g[0]-1<=la<=g[1] and la<s[0] for g,s in zip(gs,ss)):
        return {**result,'class':'full_collapse','reason':'paired truncation in G before S'}
    if all(s[1]<=g[0] and g[0]-1<=la<=g[1] for g,s in zip(gs,ss)):
        return {**result,'class':'bounded_G','reason':'paired truncation in G after complete S'}
    return {**result,'class':'ambiguous','reason':'truncation not in the measured G interval; possible template effect'}

def invariant(ctx):
    # No normalization of content, token IDs, tool-call IDs or retrieved bytes.
    return {'call_count':ctx.counter,'returns_byte_identical':len({r['returned'] for r in ctx.returns})==1,'events':ctx.events,'returns':ctx.returns,'requests':ctx.requests}

def canonical_trace(raw, rendered_prompts=None, metadata_uuid_bindings=None, metadata_uuid_audit=None):
    # Normalize only cross-branch scheduling metadata. Preserve order within
    # each branch and every semantic byte in request bodies, prompts and tools.
    result={k:raw[k] for k in ['call_count','returns_byte_identical']}
    for field in ['events','returns','requests']:
        groups={}
        for item in raw[field]:
            replica=str(item['replica'])
            value=copy.deepcopy({k:v for k,v in item.items() if k not in ['seq','call']})
            groups.setdefault(replica,[]).append(value)
        result[field]={key:groups[key] for key in sorted(groups)}
    if metadata_uuid_bindings is not None:
        audit={'policy':'mem0 retrieval audit metadata ids only; storage-bound aliases',
               'normalized':[], 'protected':[], 'raw_trace_unchanged':True}
        # A complete rendered-prompt inventory is mandatory, including all
        # request rounds and both templates. HTTP bodies are always protected.
        prompts_ready=(isinstance(rendered_prompts,list) and bool(rendered_prompts)
                       and all(isinstance(x,str) and x for x in rendered_prompts))
        request_text=json.dumps(raw['requests'],ensure_ascii=False)
        audit['rendered_inventory_available']=prompts_ready
        audit['request_inventory_sha256']=digest(request_text.encode('utf8'))
        audit['rendered_inventory_sha256']=digest(json.dumps(rendered_prompts,ensure_ascii=False).encode('utf8'))
        binding_counts={}
        for binding in metadata_uuid_bindings.values():
            if isinstance(binding,dict) and isinstance(binding.get('user_id'),str) and isinstance(binding.get('text'),str):
                key=(binding['user_id'],binding['text'])
                binding_counts[key]=binding_counts.get(key,0)+1
        candidates={}
        for replica,records in result['returns'].items():
            for ordinal,record in enumerate(records):
                metadata=record.get('native_result_metadata',{})
                ids=metadata.get('ids') if isinstance(metadata,dict) else None
                chunks=record.get('raw_chunks')
                if not isinstance(ids,list):continue
                for index,identity in enumerate(ids):
                    if not isinstance(identity,str) or not re.fullmatch(r'[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}',identity):continue
                    entry=candidates.setdefault(identity,{'locations':[], 'reason':None})
                    entry['locations'].append({'replica':replica,'return_ordinal':ordinal,'id_index':index})
                    binding=metadata_uuid_bindings.get(identity)
                    if not prompts_ready:entry['reason']='complete rendered prompt inventory missing'
                    elif identity in request_text.lower() or any(identity in text.lower() for text in rendered_prompts):
                        entry['reason']='ID occurs in complete model request or rendered prompt'
                    elif not isinstance(binding,dict) or not isinstance(binding.get('user_id'),str) or not isinstance(binding.get('text'),str):
                        entry['reason']='actual storage binding unavailable'
                    elif binding_counts[(binding['user_id'],binding['text'])]!=1:
                        entry['reason']='storage user/text binding is not unique; preserve multiplicity'
                    elif not isinstance(chunks,list) or len(ids)!=len(chunks) or chunks[index]!=binding['text']:
                        entry['reason']='ordered ID/text relation differs from actual storage'
        for identity,entry in candidates.items():
            if entry['reason'] is not None:
                audit['protected'].append({'raw_id':identity,**entry});continue
            binding=metadata_uuid_bindings[identity]
            alias='SCAN_AUDIT_UUID_'+digest(json.dumps([binding['user_id'],binding['text']],ensure_ascii=False,separators=(',',':')).encode('utf8'))
            for loc in entry['locations']:
                result['returns'][loc['replica']][loc['return_ordinal']]['native_result_metadata']['ids'][loc['id_index']]=alias
            audit['normalized'].append({'raw_id':identity,'alias':alias,'storage_binding':binding,'locations':entry['locations']})
        if metadata_uuid_audit is not None:metadata_uuid_audit.update(audit)
    return result


def mem0_metadata_uuid_bindings(out,phase):
    """Existing native storage evidence only; never create or repair storage."""
    path=out/('native_mm_'+phase)/'actual_storage_snapshot.json'
    if not path.is_file():return {},{'missing_storage_snapshot':True}
    data=json.loads(path.read_bytes());bindings={}
    for row in data['rows']:
        identity=row['id'];payload=row['payload']
        if identity in bindings:raise RuntimeError('Duplicate native storage ID')
        bindings[identity]={'user_id':payload.get('user_id'),'text':payload.get('data')}
    return bindings,{'file':path.relative_to(ROOT).as_posix(),'sha256':digest(path.read_bytes())}


def repeat_invariant(result):
    return {k:v for k,v in result.items() if k!='trace'}

def pair_integrity(b,a):
    def without_markers(data):
        if isinstance(data,str):return re.sub(PROTOCOL['marker_regex'],'',data)
        if isinstance(data,list):return [without_markers(x) for x in data]
        if isinstance(data,dict):return {k:without_markers(v) for k,v in data.items()}
        return data
    # Compare the complete actual LLM request payloads, not just the final requests.
    def per_replica(requests):
        return {i:[r for r in requests if r['replica']==i] for i in range(PROTOCOL['N'])}
    # Preserve dict insertion order, since tool-schema serialization in the chat
    # template can depend on it. Archive and compare original HTTP body text too.
    return json.dumps(per_replica(b['requests']),ensure_ascii=False)==json.dumps(without_markers(per_replica(a['requests'])),ensure_ascii=False)

def execute(backend,recipe,templates,identity,revision,out,transport='json',control_concurrent=False):
    from transformers import AutoTokenizer
    tokenizers={k:AutoTokenizer.from_pretrained(str(ROOT/f'evidence/templates/{k}'),local_files_only=True) for k in templates}
    phases={}; contexts={}
    for phase in ['benign','attacked']:
        ctx=ProbeContext(backend,phase,identity);ctx.use_stream=transport=='sse';ctx.control_concurrent=control_concurrent
        out.mkdir(parents=True,exist_ok=True)
        ctx.journal_path=out/(phase+'_raw_journal.jsonl')
        # Each attempted run starts a fresh journal, preserving older attempts
        # only when they live in their separately versioned output directory.
        ctx.journal_path.write_text('',encoding='utf-8')
        ctx.start_stub()
        try:
            recipe(ctx)
            contexts[phase]=invariant(ctx)
            phases[phase]={key:measure(ctx,tok) for key,tok in tokenizers.items()}
        except Exception as exc:
            dump(out/'failure.json',{'identity':identity,'code':revision,'failed_phase':phase,
                  'exception_type':type(exc).__name__,'error':str(exc),'stack':traceback.format_exc(),
                  'completed_phase_traces':contexts,'failed_phase_raw_trace':invariant(ctx),
                  'classification':None})
            raise
        finally:ctx.stop()
    if not pair_integrity(contexts['benign'],contexts['attacked']):
        dump(out/'failure.json',{'identity':identity,'code':revision,'failed_stage':'pair_integrity',
             'error':'Paired requests differ beyond injected retrieval-return marker',
             'completed_phase_traces':contexts,'measurements':phases,'classification':None})
        raise RuntimeError('Paired requests differ beyond injected retrieval-return marker')
    classes={k:classify(phases['benign'][k],phases['attacked'][k]) for k in templates}
    receipt={k:{p.name:digest(p.read_bytes()) for p in (ROOT/f'evidence/templates/{k}').iterdir() if p.name in ['tokenizer.json','tokenizer_config.json','config.json','special_tokens_map.json','chat_template.jinja']} for k in templates}
    canonical={};uuid_audits={};source_audits={}
    for phase,raw in contexts.items():
        if identity=='mem0ai/mem0' and PROTOCOL['metadata_uuid_canonicalization']['scope']=='mem0 native_result_metadata.ids only':
            bindings,evidence=mem0_metadata_uuid_bindings(out,phase)
            rendered=[item['rendered'] for measurement in phases[phase].values() for item in measurement['all_prompts']]
            uuid_audits[phase]={}
            canonical[phase]=canonical_trace(raw,rendered,bindings,uuid_audits[phase])
            uuid_audits[phase]['storage_evidence']=evidence
        elif identity=='microsoft/autogen':
            from autogen_support.canonical_metadata import storage_bindings,canonicalize
            bindings,evidence=storage_bindings(out,phase,ROOT)
            rendered=[item['rendered'] for measurement in phases[phase].values() for item in measurement['all_prompts']]
            uuid_audits[phase]={}
            from autogen_support.source_provenance import source_bindings
            sources,source_evidence=source_bindings(out,phase,ROOT,bindings,evidence)
            source_inventory={'requests':[r for v in contexts.values() for r in v['requests']],
                              'rendered':[item['rendered'] for arm in phases.values() for measurement in arm.values() for item in measurement['all_prompts']]}
            canonical[phase]=canonicalize(raw,rendered,bindings,uuid_audits[phase],sources,source_inventory)
            source_audits[phase]=uuid_audits[phase].pop('source_audit')
            source_audits[phase]['actual_source_evidence']=source_evidence
            uuid_audits[phase]['storage_evidence']=evidence
        else:canonical[phase]=canonical_trace(raw)
    if uuid_audits:dump(out/'canonical_metadata_uuid_audit.json',uuid_audits)
    if source_audits:dump(out/'canonical_metadata_source_audit.json',source_audits)
    result={'identity':identity,'code':revision,'templates_sha256':receipt,'pair_integrity':True,'trace':contexts,'canonical_trace':canonical,'measurements':phases,'classification':classes}
    dump(out/'trace.json',result)
    print(identity,json.dumps(classes,ensure_ascii=False),flush=True)
    return result

def framework_worker(row,args,revision):
    """Isolated framework execution, killed by the parent on its fixed deadline."""
    if not args.attempt_id or not re.fullmatch(r'[A-Za-z0-9_]+',args.attempt_id):
        raise RuntimeError('Worker requires a safe attempt ID')
    target=ROOT/'exec/pilot'/row['repo'].replace('/','__')/args.attempt_id
    if row['recipe']['step']=='none':
        result={'repo':row['repo'],'status':'excluded: no runnable official recipe',
                'code_commit':revision['commit'],'classification':None,'recipe_provenance':row,
                'attempt_id':args.attempt_id,'trace_directory':target.relative_to(ROOT).as_posix()}
        dump(target/'exclusion.json',result);dump(target/'worker_result.json',result)
        return result
    try:
        backend=RetrievalBackend()
        module=importlib.import_module(row['adapter_module'])
        runs=[execute(backend,lambda ctx:module.run(ctx,row),args.templates,row['repo'],revision,target/f'repeat_{j+1}') for j in range(args.repeat)]
        same=all(repeat_invariant(x)==repeat_invariant(runs[0]) for x in runs[1:])
        dump(target/'repeat_check.json',{'code_commit':revision['commit'],'repeat':args.repeat,
             'identical_raw_trace':all(x==runs[0] for x in runs[1:]),'identical_full_invariant':same,
             'note':'Only cross-branch scheduling metadata removed; all semantic bytes and per-branch event order preserved'})
        if not same:raise RuntimeError('Framework repeat invariant mismatch; cannot report reproducibility')
        result={'repo':row['repo'],'status':'executed_reproducible','code_commit':revision['commit'],
                'classification':runs[0]['classification'],'recipe_provenance':row}
    except Exception as exc:
        result={'repo':row['repo'],'status':('preparation-blocked' if type(exc).__name__=='MetaGPTPreparationBlocked' else 'excluded: did-not-run-or-validate'),'error':str(exc),
                'exception_type':type(exc).__name__,'stack':traceback.format_exc(),'code_commit':revision['commit'],
                'classification':None,'recipe_provenance':row}
        dump(target/'exclusion.json',result)
        print('PILOT_EXCLUDED',row['repo'],type(exc).__name__,str(exc),flush=True)
    result.update(attempt_id=args.attempt_id,trace_directory=target.relative_to(ROOT).as_posix())
    dump(target/'worker_result.json',result)
    return result

def wait_with_timebox(process,hours,log):
    from worker_job import attach,close
    attach(process)
    start=time.monotonic();deadline=start+hours*3600
    while process.poll() is None and time.monotonic()<deadline:
        try:process.wait(timeout=min(5,max(.01,deadline-time.monotonic())))
        except subprocess.TimeoutExpired:pass
    timeout=process.poll() is None
    if timeout:
        if os.name=='nt':
            close(process)
        else:process.kill()
        process.wait(timeout=30)
    close(process)
    return timeout,time.monotonic()-start

def timed_framework(row,args,revision,hours):
    attempt=datetime.datetime.now(datetime.timezone.utc).strftime('run_%Y%m%dT%H%M%S%fZ')
    target=ROOT/'exec/pilot'/row['repo'].replace('/','__')/attempt;target.mkdir(parents=True,exist_ok=False)
    result_file=target/'worker_result.json'
    started=datetime.datetime.now(datetime.timezone.utc).isoformat()
    with (target/'worker_execution.log').open('w',encoding='utf-8') as log:
        command=[sys.executable,str(Path(__file__).resolve()),'--stage','framework-worker',
                 '--framework',row['repo'],'--attempt-id',attempt,'--repeat',str(args.repeat),'--templates',*args.templates]
        process=subprocess.Popen(command,cwd=str(ROOT),stdout=log,stderr=subprocess.STDOUT,
              creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        timeout,elapsed=wait_with_timebox(process,hours,log)
    timing={'code_commit':revision['commit'],'started_utc':started,'elapsed_seconds':elapsed,
            'timebox_hours':hours,'timeout':timeout,'worker_exit_code':process.returncode,
            'raw_partial_trace':'Per-phase raw JSONL journals are flushed at every event/request/return.'}
    dump(target/'timebox.json',timing)
    if timeout or not result_file.exists():
        result={'repo':row['repo'],'status':'excluded: did-not-run','code_commit':revision['commit'],
                'classification':None,'recipe_provenance':row,'timing':timing,'attempt_id':attempt,
                'trace_directory':target.relative_to(ROOT).as_posix(),
                'error':'Fixed framework timebox exceeded' if timeout else 'Framework worker failed to produce a fresh result'}
        dump(target/'exclusion.json',result)
        return result
    result=json.loads(result_file.read_bytes())
    if result['code_commit']!=revision['commit'] or result['attempt_id']!=attempt:raise RuntimeError('Worker revision/attempt mismatch')
    return {**result,'timing':timing}

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--stage',choices=['controls','pilot','continuation','framework-worker'],required=True)
    parser.add_argument('--templates',nargs='+',default=['llama33','qwen3']);parser.add_argument('--development',action='store_true');parser.add_argument('--repeat',type=int,default=2)
    parser.add_argument('--framework')
    parser.add_argument('--attempt-id')
    args=parser.parse_args();revision=code_revision()
    if args.repeat<2:raise RuntimeError('Reproducibility requires >=2 repeated executions')
    if not args.development and set(args.templates)!={'llama33','qwen3'}:raise RuntimeError('Formal validation requires both exact templates')
    for k in args.templates:
        if not (ROOT/f'evidence/templates/{k}/tokenizer.json').exists():raise RuntimeError(f'Missing authorized official tokenizer: {k}')
    manifest=json.loads((ROOT/'config/templates_manifest.json').read_text(encoding='utf-8'))
    for k in args.templates:
        for filename,expected_hash in manifest[k]['sha256'].items():
            if digest((ROOT/f'evidence/templates/{k}/{filename}').read_bytes())!=expected_hash:
                raise RuntimeError(f'Pinned template byte mismatch: {k}/{filename}')
    if args.stage in ['pilot','continuation','framework-worker']:
        gate=json.loads((ROOT/'controls/calibration.json').read_text(encoding='utf-8'))
        if not gate['all_passed'] or gate['code_commit']!=revision['commit']:raise RuntimeError('Both-template controls not passed at this code commit')
        if not (ROOT/'config/frameworks.json').exists():
            from selection_gate import readiness
            pending=readiness()['pending_count']
            raise RuntimeError(f'Formal ranking is not frozen: {pending} qualification decisions remain pending; no pilot launched')
        registry=json.loads((ROOT/'config/frameworks.json').read_text(encoding='utf-8'))
        if not registry['frozen'] or not registry['eligibility_complete']:raise RuntimeError('Candidate eligibility/ranking is not frozen')
        from selection_gate import verify_frozen
        verify_frozen(registry)
        if args.development:raise RuntimeError('Framework execution cannot use development calibration')
        protocol=json.loads((ROOT/'config/discovery_protocol.json').read_bytes())
        hours=protocol['per_framework_timebox_hours']
        if not 0<hours<=8:raise RuntimeError('Framework timebox must be positive and no greater than 8 hours')
        selected=registry['selected'][:2]
        if len(selected)!=2:raise RuntimeError('Frozen pilot must contain exactly ranks 1 and 2')
        if args.stage=='framework-worker':
            from selection_gate import frozen_targets
            matching=[row for row in frozen_targets(registry) if row['repo']==args.framework]
            if len(matching)!=1:raise RuntimeError('Worker target is not in frozen top-6')
            if matching[0]['rank'] > 2:
                from selection_gate import verify_continuation
                verify_continuation(registry,args.framework,code_commit=revision['commit'])
            framework_worker(matching[0],args,revision)
            return
        if args.stage=='continuation':
            from selection_gate import verify_continuation
            authorization=verify_continuation(registry,args.framework,code_commit=revision['commit'])
            from selection_gate import frozen_targets
            row=next(r for r in frozen_targets(registry) if r['repo']==args.framework)
            prior=[]
            for item in (ROOT/'exec/pilot'/row['repo'].replace('/','__')).glob('*/timebox.json'):
                prior.append(json.loads(item.read_bytes()))
            used=sum(x['elapsed_seconds'] for x in prior)
            remaining=authorization['maximum_cumulative_seconds']-used
            if remaining<=0:raise RuntimeError('Cumulative framework timebox exhausted')
            result=timed_framework(row,args,revision,min(hours,remaining/3600))
            stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
            dump(ROOT/'reports/continuation'/('rank_'+str(row['rank'])+'_'+stamp+'.json'),{
                'code_commit':revision['commit'],'rank':row['rank'],'result':result,
                'prior_seconds':used,'cumulative_seconds':used+result['timing']['elapsed_seconds'],
                'maximum_seconds':authorization['maximum_cumulative_seconds'],
                'stop_for_parent_review':True})
            return
        pilot_results=[]
        for row in selected:
            pilot_results.append(timed_framework(row,args,revision,hours))
        dump(ROOT/'exec/pilot/checkpoint.json',{'code_commit':revision['commit'],'ranks':[1,2],'results':pilot_results,
             'stop_before_ranks_3_to_6':True,'guide_hard_gate':'Return checkpoint and wait for factual review before further ranks'})
        return
    backend=RetrievalBackend();results=[]
    attempt=datetime.datetime.now(datetime.timezone.utc).strftime('run_%Y%m%dT%H%M%S%fZ')
    base=ROOT/'controls/runs'/revision['commit']/attempt/('development' if args.development else 'validated')
    for row in EXPECTED['controls']:
        outputs=[]
        for repeat in range(args.repeat):
            outputs.append(execute(backend,lambda ctx:control_recipe(ctx,row['id']),args.templates,row['id'],revision,base/row['id']/f'repeat_{repeat+1}'))
        same=all(repeat_invariant(outputs[0])==repeat_invariant(other) for other in outputs[1:])
        correct=all(r['class']==row['expected'] for o in outputs for r in o['classification'].values())
        streams=[execute(backend,lambda ctx:control_recipe(ctx,row['id']),args.templates,row['id'],revision,base.parent/(base.name+'_sse')/row['id']/f'repeat_{j+1}',transport='sse') for j in range(args.repeat)]
        stream_same=all(repeat_invariant(streams[0])==repeat_invariant(other) for other in streams[1:])
        stream_correct=all(r['class']==row['expected'] for o in streams for r in o['classification'].values())
        variants={'json_sequential':{'correct':correct,'reproducible':same,'observed':outputs[0]['classification']},'sse_sequential':{'correct':stream_correct,'reproducible':stream_same,'observed':streams[0]['classification']}}
        for transport,reference in [('json',outputs[0]),('sse',streams[0])]:
            concurrent_runs=[execute(backend,lambda ctx:control_recipe(ctx,row['id']),args.templates,row['id'],revision,base.parent/(base.name+'_'+transport+'_concurrent')/row['id']/f'repeat_{j+1}',transport=transport,control_concurrent=True) for j in range(args.repeat)]
            concurrent_same=all(repeat_invariant(reference)==repeat_invariant(other) for other in concurrent_runs)
            concurrent_correct=all(r['class']==row['expected'] for o in concurrent_runs for r in o['classification'].values())
            variants[transport+'_concurrent']={'correct':concurrent_correct,'reproducible':concurrent_same,'observed':concurrent_runs[0]['classification'],'matches_sequential_semantic_bytes':concurrent_same}
        results.append({'id':row['id'],'expected':row['expected'],'observed':outputs[0]['classification'],'reproducible':all(v['reproducible'] for v in variants.values()),'correct':all(v['correct'] for v in variants.values()),'execution_variants':variants})
    summary={'code_commit':revision['commit'],'trace_base':base.relative_to(ROOT).as_posix(),'templates':args.templates,'template_provenance':manifest,'development_only':args.development,'repeat':args.repeat,'all_passed':all(x['correct'] and x['reproducible'] for x in results),'controls':results,'created_utc':datetime.datetime.now(datetime.timezone.utc).isoformat()}
    dump(ROOT/'controls'/('development_summary.json' if args.development else 'calibration.json'),summary)
    print('DEVELOPMENT_ONLY' if args.development else 'FORMAL_CALIBRATION',summary['all_passed'])
    if not summary['all_passed']:sys.exit(1)

if __name__=='__main__':main()

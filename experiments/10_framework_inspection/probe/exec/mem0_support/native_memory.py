"""Preparation-only mem0 candidate. No CLI enables formal measurement.
Pinned original batch add topology; chat block separately preserved.
"""
from __future__ import annotations
import ast, asyncio, contextvars, copy, hashlib, importlib.util, json, os, re, threading, traceback
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
BASE=ROOT/'preparation/mem0_night'
SOURCE=BASE/'sources'
REPLICA=contextvars.ContextVar('mem0_replica',default=None)
EXTRACTION={'memory':[{'text':'SCAN deterministic extracted memory for native persistence verification.'}]}
CHAT_ANSWER='The policy requires seven years of retention.'


def prepare_environment(attempt_dir):
    import sys
    attempt_dir=Path(attempt_dir).resolve()
    if not any(attempt_dir.is_relative_to((ROOT/p).resolve()) for p in ['reports','exec/pilot']):raise ValueError('Private attempt path outside owned run evidence')
    attempt_dir.mkdir(parents=True,exist_ok=False)
    for sub in ['tmp','mem0','cache']: (attempt_dir/sub).mkdir()
    os.environ.update(MEM0_DIR=str(attempt_dir/'mem0'),MEM0_TELEMETRY='False',
        TEMP=str(attempt_dir/'tmp'),TMP=str(attempt_dir/'tmp'),HF_HOME=str(attempt_dir/'cache'),
        HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',TOKENIZERS_PARALLELISM='false',
        PYTHONDONTWRITEBYTECODE='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1')
    # Process-only prevention of accidental configured external LLM routing.
    for name in ['OPENROUTER_API_KEY','OPENAI_API_BASE','OPENAI_BASE_URL']:
        os.environ.pop(name,None)
    sys.dont_write_bytecode=True
    import site
    site.addsitedir(str(BASE/'deps'))
    sys.path.insert(0,str(SOURCE))
    return attempt_dir


class MiniLM384Provider:
    """Explicit real MiniLM mapping, string batches only as sent by native mem0.
    Reuses audited CPU provider implementation's model/receipt data contract.
    Native newline conversion and native batching remain in OpenAIEmbedding.
    """
    def __init__(self):
        import torch
        from sentence_transformers import SentenceTransformer
        model_dir=ROOT/'evidence/embeddings'
        receipts=[]
        for receipt in sorted(model_dir.rglob('*.receipt.json')):
            meta=json.loads(receipt.read_text(encoding='utf-8'))
            artifact=Path(str(receipt).removesuffix('.receipt.json'))
            digest=hashlib.sha256(artifact.read_bytes()).hexdigest()
            if digest!=meta['sha256']:raise ValueError('Model hash mismatch')
            receipts.append({'path':artifact.relative_to(model_dir).as_posix(),'sha256':digest,'source_url':meta['url']})
        if len(receipts)!=11:raise ValueError('Expected eleven frozen model receipts')
        torch.set_num_threads(1)
        self.model=SentenceTransformer(str(model_dir),device='cpu',local_files_only=True)
        self.model_receipts=receipts; self.lock=threading.Lock();self.events=[]

    def respond(self,body):
        if body.get('model')!='text-embedding-3-small' or body.get('dimensions')!=384:
            raise ValueError('Only explicit text-embedding-3-small->MiniLM384 mapping accepted')
        texts=body.get('input')
        if not isinstance(texts,list) or not texts or not all(isinstance(t,str) for t in texts):
            raise ValueError('Native string batch required')
        if body.get('encoding_format')!='float':raise ValueError('Native float required')
        with self.lock:
            vectors=self.model.encode(texts,normalize_embeddings=False,convert_to_numpy=True).astype('<f4')
            if vectors.shape!=(len(texts),384):raise ValueError('Real vector shape mismatch')
            self.events.append({'request':copy.deepcopy(body),'dimensions':384,'normalization':False,
                                'provider':'verified local CPU all-MiniLM-L6-v2'})
        return {'object':'list','model':body['model'],'data':[{'object':'embedding','index':i,'embedding':v.tolist()}
            for i,v in enumerate(vectors)],'usage':{'prompt_tokens':0,'total_tokens':0}}


@contextmanager
def local_stub(provider,ctx=None,invalid_json=False):
    state={'requests':[],'errors':[]}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            raw=self.rfile.read(int(self.headers['Content-Length'])).decode('utf-8')
            try:
                body=json.loads(raw)
                if self.path=='/v1/embeddings':response=provider.respond(body)
                elif self.path=='/v1/chat/completions':
                    replica=self.headers.get('X-SCAN-Replica')
                    replica=None if replica is None else int(replica)
                    record={'replica':replica,'body':copy.deepcopy(body),'raw_body_text':raw}
                    state['requests'].append(record)
                    if ctx is not None:
                        with ctx.lock:
                            ctx.requests.append(copy.deepcopy(record));ctx.journal({'record_type':'http_request','value':record})
                            ctx.event('llm_request',replica,messages=copy.deepcopy(body['messages']))
                    if body.get('response_format')=={'type':'json_object'}:
                        content='invalid ordinary string' if invalid_json else json.dumps(EXTRACTION,separators=(',',':'))
                    elif 'response_format' not in body:content=CHAT_ANSWER
                    else:raise ValueError('Unfrozen response format')
                    message={'role':'assistant','content':content}
                    response={'id':'scan_canned','created':0,'model':body['model'],'object':'chat.completion',
                              'choices':[{'index':0,'message':message,'finish_reason':'stop'}],
                              'usage':{'prompt_tokens':0,'completion_tokens':0,'total_tokens':0}}
                    if ctx is not None:
                        with ctx.lock:
                            ctx.model_records.append({'replica':replica,'raw_body_text':raw,'request':copy.deepcopy(body),
                                 'response_message':message,'finish_reason':'stop','status':200,'transport':'json'})
                            ctx.event('llm_response',replica,message=copy.deepcopy(message))
                else:raise ValueError('Unknown local endpoint')
                code=200
            except Exception as e:
                state['errors'].append(repr(e));response={'error':{'message':str(e)}};code=422
            data=json.dumps(response,ensure_ascii=False).encode()
            self.send_response(code);self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    state['base_url']=f'http://127.0.0.1:{server.server_port}/v1'
    try:yield state
    finally:server.shutdown();server.server_close();thread.join(timeout=5)


def new_memory(attempt_dir,base_url,embedding_base_url=None):
    from mem0 import AsyncMemory
    from mem0.configs.base import MemoryConfig
    memory=AsyncMemory(config=MemoryConfig(**{
        'vector_store':{'provider':'qdrant','config':{'path':str(attempt_dir/'qdrant'),'embedding_model_dims':384}},
        'embedder':{'provider':'openai','config':{'api_key':'scan-local','openai_base_url':embedding_base_url or base_url,'embedding_dims':384}},
        'llm':{'provider':'openai','config':{'api_key':'scan-local','openai_base_url':base_url}},
        'history_db_path':str(attempt_dir/'history.db')}))
    # Observation metadata only: ContextVar propagates through native asyncio.to_thread.
    def identity_header(request):
        replica=REPLICA.get()
        if replica is not None:request.headers['X-SCAN-Replica']=str(replica)
    memory.llm.client._client.event_hooks['request'].append(identity_header)
    memory.embedding_model.client._client.event_hooks['request'].append(identity_header)
    memory.scan_add_receipts=[]
    memory.scan_add_receipt_path=attempt_dir/'add_receipts.jsonl'
    original_add=memory.add
    async def routed_add(messages,**kwargs):
        user_id=kwargs.get('user_id')
        match=re.fullmatch(r'user_(\d+)',user_id or '')
        token=REPLICA.set(int(match.group(1)) if match else REPLICA.get())
        record={'ordinal':len(memory.scan_add_receipts),'user_id':user_id,'infer':kwargs.get('infer',True),
                'messages':copy.deepcopy(messages),'kwargs':copy.deepcopy(kwargs),'status':'started'}
        memory.scan_add_receipts.append(record)
        try:
            # Observer snapshot only; never mutate the native index or put IDs into a prompt.
            before=memory.vector_store.list(top_k=10000)
            if before[1] is not None:raise RuntimeError('ID snapshot exceeds declared controlled corpus capacity')
            record['before_ids']=[row.id for row in before[0]]
        except Exception as e:
            record['snapshot_error']=repr(e)
        journal_add_receipt(memory,record,'start')
        try:
            try:
                result=await original_add(messages,**kwargs)
            except BaseException as e:
                record.update(status='exception',exception_type=type(e).__name__,exception=repr(e),traceback=traceback.format_exc())
                journal_add_receipt(memory,record,'native_exception')
                raise  # Preserve native exception; original gather still has return_exceptions=True.
            record.update(status='returned',native_return=copy.deepcopy(result))
            try:
                record['persist_validation']=validate_add_receipt(memory,record)
            except Exception as e:
                record['persist_validation']={'ok':False,'error':str(e),'exception_type':type(e).__name__}
            journal_add_receipt(memory,record,'native_return')
            return result  # Validation does not rewrite or truncate native add behavior.
        finally:REPLICA.reset(token)
    memory.add=routed_add
    return memory


async def seed_users(memory,documents,users):
    receipts=[]
    for user in users:
        result=await memory.add([{'role':'user','content':d} for d in documents],user_id=user,infer=False)
        if len(result.get('results',[]))!=len(documents):raise RuntimeError('Seed API failed')
        for row in result['results']:
            stored=memory.vector_store.get(row['id'])
            if stored.payload['data']!=row['memory'] or stored.payload['user_id']!=user:raise RuntimeError('Seed persist failed')
        receipts.append({'user_id':user,'result':result})
    return receipts


def observe_search(memory,ctx):
    native=memory.vector_store.search
    def wrapped(*args,**kwargs):
        replica=REPLICA.get()
        if replica is None:raise RuntimeError('Missing observation route')
        query=kwargs.get('query',args[0] if args else None)
        key=ctx.retrieval_start(replica,query,'mem0 native Qdrant',{'top_k':kwargs.get('top_k'),'filters':kwargs.get('filters')})
        rows=native(*args,**kwargs)
        chunks=[r.payload.get('data','') for r in rows]
        returned=ctx.retrieval_return(replica,key,query,chunks,{'ids':[r.id for r in rows],'scores':[r.score for r in rows]})
        if not rows:
            if returned!=[]:raise RuntimeError('Observer fabricated a chunk for an empty native return')
            return rows  # Exact native empty-list object; no exception and no attack marker.
        rows=copy.deepcopy(rows)
        for row,text in zip(rows,returned):row.payload['data']=text
        return rows
    memory.vector_store.search=wrapped


def official_block(number):
    text=(SOURCE/'docs/open-source/features/async-memory.mdx').read_text(encoding='utf-8')
    return re.findall(r'```python\s*\n(.*?)```',text,re.S)[number-1]


def batch_function(memory,history,n=4):
    """Three explicit author adaptations: constructor binding, range5->N4, S in user content."""
    tree=ast.parse(official_block(3))
    class Adapt(ast.NodeTransformer):
        def visit_Call(self,node):
            self.generic_visit(node)
            if isinstance(node.func,ast.Name) and node.func.id=='range':
                assert len(node.args)==1 and node.args[0].value==5
                node.args=[ast.Constant(n)]
            return node
        def visit_Dict(self,node):
            self.generic_visit(node)
            for i,key in enumerate(node.keys):
                if isinstance(key,ast.Constant) and key.value=='content':
                    node.values[i]=ast.BinOp(left=node.values[i],op=ast.Add(),right=ast.Name(id='_history_suffix',ctx=ast.Load()))
            return node
    tree=ast.fix_missing_locations(Adapt().visit(tree))
    namespace={'AsyncMemory':lambda:memory,'asyncio':asyncio,'_history_suffix':'\n'+history if history else ''}
    exec(compile(tree,'pinned_async_batch_adapted','exec'),namespace)
    return namespace['batch_operations'],ast.unparse(tree)


def journal_add_receipt(memory,record,event):
    with memory.scan_add_receipt_path.open('a',encoding='utf-8') as stream:
        stream.write(json.dumps({'event':event,'receipt':copy.deepcopy(record)},ensure_ascii=False,default=str)+'\n')
        stream.flush()


def validate_add_receipt(memory,record):
    """Runtime worker check: returned new IDs, scoped real Qdrant data and matching SQLite ADD.
    Never infer completion from print, None, pre-existing text, or HTTP success.
    """
    if record.get('status')!='returned':raise RuntimeError('Native add did not return successfully')
    if 'snapshot_error' in record:raise RuntimeError('Cannot verify new IDs: '+record['snapshot_error'])
    result=record.get('native_return')
    if not isinstance(result,dict) or not isinstance(result.get('results'),list) or not result['results']:
        raise RuntimeError('Native add returned no new extraction records')
    seen=set();verified=[]
    for row in result['results']:
        memory_id=row.get('id');text=row.get('memory')
        if not memory_id or memory_id in record['before_ids'] or memory_id in seen:
            raise RuntimeError('Returned memory ID is missing, reused or duplicated')
        seen.add(memory_id)
        if row.get('event')!='ADD':raise RuntimeError('Expected original native ADD receipt')
        stored=memory.vector_store.get(memory_id)
        if stored is None or stored.id!=memory_id:raise RuntimeError('Returned ID absent from real Qdrant')
        if stored.payload.get('user_id')!=record['user_id']:raise RuntimeError('Qdrant user ownership mismatch')
        if stored.payload.get('data')!=text:raise RuntimeError('Qdrant content does not match native add return')
        history=memory.db.get_history(memory_id)
        matched=[h for h in history if h.get('memory_id')==memory_id and h.get('event')=='ADD'
                 and h.get('new_memory')==text and not h.get('is_deleted')]
        if not matched:raise RuntimeError('Missing matching real SQLite ADD history')
        verified.append({'id':memory_id,'user_id':record['user_id'],'memory':text,'payload':copy.deepcopy(stored.payload),
                         'sqlite_ADD':copy.deepcopy(matched),'new_id':True})
    return {'ok':True,'verified':verified}


def verify_extract_persist(memory,users,records=None):
    """Completion boundary used by the candidate worker after the original gather.
    Revalidate current storage/history so stale or partial receipts cannot certify completion.
    """
    records=memory.scan_add_receipts if records is None else records
    receipts=[]
    for user in users:
        calls=[r for r in records if r['user_id']==user and r['infer']]
        if len(calls)!=1:raise RuntimeError(f'Expected exactly one original infer=True add for {user}; found {len(calls)}')
        record=calls[0]
        try:
            validation=validate_add_receipt(memory,record)
            if len(validation['verified'])!=1 or validation['verified'][0]['memory']!=EXTRACTION['memory'][0]['text']:
                raise RuntimeError('Frozen extraction response not uniquely confirmed by actual persistence')
            record['completion_validation']=validation
            verified=validation['verified'][0]
            receipts.append({'user_id':user,'id':verified['id'],'payload':verified['payload'],
                             'sqlite_ADD':verified['sqlite_ADD'],'add_receipt':copy.deepcopy(record)})
        except Exception as e:
            record['completion_validation']={'ok':False,'error':str(e)}
            journal_add_receipt(memory,record,'completion_failure')
            raise
        journal_add_receipt(memory,record,'completion_verified')
    return receipts


def chat_function(memory,client):
    tree=ast.parse(official_block(7));tree.body=[n for n in tree.body if isinstance(n,ast.AsyncFunctionDef)]
    namespace={'async_memory':memory,'async_openai_client':client}
    exec(compile(tree,'pinned_official_chat_unchanged','exec'),namespace)
    return namespace['chat_with_memories']


def close_memory(memory):
    memory.llm.client.close();memory.embedding_model.client.close();memory.vector_store.client.close()
    memory.db.close()


async def preparation_batch(memory,ctx):
    users=[f'user_{i}' for i in range(4)]
    corpus=json.loads((ROOT/'config/corpus.json').read_text(encoding='utf-8'))
    seeds=await seed_users(memory,corpus['documents'],users)
    observe_search(memory,ctx)
    first_add=len(memory.scan_add_receipts)
    batch,adapted=batch_function(memory,ctx.history,4)
    await batch()
    calls=memory.scan_add_receipts[first_add:]
    return {'seeds':seeds,'add_receipts':copy.deepcopy(calls),'persisted':verify_extract_persist(memory,users,calls),'adapted_source':adapted}


def run(ctx,run_dir):
    """Candidate integration callable. Caller must enforce same-commit controls/order/parent review.
    Does not own main stub port. Formal CLI intentionally absent.
    """
    raise RuntimeError('Preparation only: parent integration and review required before formal N4; use preparation_batch from isolated reviewed worker')



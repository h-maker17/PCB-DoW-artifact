"""Rank7 integration candidate. Standalone native entry; no shared pipeline imports.

N is expanded by native registrations only. One official publish owns scheduling.
This module does not decide whether the formal extension condition has triggered.
"""
from __future__ import annotations
import argparse,ast,asyncio,contextvars,copy,hashlib,http.client,importlib,inspect,json,re,time,traceback,urllib.parse
from contextlib import contextmanager
from pathlib import Path
import adapter_autogen_candidate as base
from autogen_core import DefaultTopicId,SingleThreadedAgentRuntime
from autogen_core.memory import MemoryQueryResult
from autogen_ext.memory.chromadb import ChromaDBVectorMemory
from autogen_ext.models.openai import OpenAIChatCompletionClient
from autogen_agentchat.ui import Console
from autogen_agentchat.base import TaskResult
from tokenizers import Tokenizer

IDENTITY=contextvars.ContextVar('autogen_native_metadata',default=None)

class ContractError(RuntimeError):pass

def json_default(obj):
    if hasattr(obj,'get_config'):return {'python_type':type(obj).__module__+'.'+type(obj).__name__,'config':obj.get_config()}
    raise TypeError(f'Unsupported audit object: {type(obj).__name__}')

def dump(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,default=json_default),encoding='utf8')

def rpc(config,route,payload):
    identity=IDENTITY.get()
    if identity is None:raise ContractError('Observer request outside native branch')
    u=urllib.parse.urlparse(config['base_url'])
    if u.scheme!='http' or u.hostname not in ('127.0.0.1','localhost'):raise ContractError('Only local shared stub/observer is permitted')
    connection=http.client.HTTPConnection(u.hostname,u.port,timeout=120)
    connection.request('POST',u.path.rstrip('/')+'/scan/'+route,json.dumps(payload,ensure_ascii=False).encode('utf8'),
       {'Content-Type':'application/json','X-SCAN-Replica':str(identity['branch']),
        'X-SCAN-Attempt':identity['attempt'],'X-SCAN-Phase':identity['phase']})
    response=connection.getresponse();raw=response.read();connection.close()
    if response.status!=200:raise ContractError(f'{route} HTTP {response.status}: {raw[:1000]!r}')
    return json.loads(raw)

def copy_returned_result(original:MemoryQueryResult,chunks:list[str])->MemoryQueryResult:
    """Return independent content copies; never change native IDs/scores/metadata/order."""
    if len(chunks)!=len(original.results) or any(not isinstance(v,str) for v in chunks):
        raise ContractError('Observer returned a different result count/type')
    result=[]
    for item,text in zip(original.results,chunks,strict=True):
        raw=str(item.content)
        if text!=raw:
            if not text.endswith(raw):raise ContractError('Observer rewrote native retrieval content')
            prefix=text[:-len(raw)] if raw else text
            if not re.fullmatch(r'\[SCAN_CALL_R(?:\d+|broadcast)_C\d{4,}\] ',prefix):
                raise ContractError('Only existing shared observer marker prefix is supported')
        result.append(item.model_copy(deep=True,update={'content':text}))
    return original.model_copy(deep=True,update={'results':result})

@contextmanager
def connection_observers(config,embedding_receipts,model_requests):
    """Observe actual native body sessions and add HTTP headers only, then restore."""
    native_forward=base.ONNXMiniLM_L6_V2._forward
    def observed_forward(self,documents,batch_size=32):
        identity=IDENTITY.get()
        if identity is None:raise ContractError('ONNX body call outside native branch')
        # The original native method performs the model.run on this same self.model.
        vectors=native_forward(self,documents,batch_size=batch_size)
        tokenizer=Tokenizer.from_str(self.tokenizer.to_str())
        tokenizer.no_truncation();tokenizer.no_padding()
        full=[len(tokenizer.encode(text).ids) for text in documents]
        retained=[sum(self.tokenizer.encode(text).attention_mask) for text in documents]
        embedding_receipts.append({'identity':dict(identity),'actual_body_session':True,
            'session_object_id':id(self.model),'providers':self.model.get_providers(),
            'provider_options':self.model.get_provider_options(),'model_class':type(self.model).__module__+'.'+type(self.model).__name__,
            'model_path':str(base.ONNXMiniLM_L6_V2.DOWNLOAD_PATH/'onnx/model.onnx'),
            'implementation_sha256':hashlib.sha256(Path(inspect.getfile(base.ONNXMiniLM_L6_V2)).read_bytes()).hexdigest(),
            'input_sha256':[hashlib.sha256(v.encode()).hexdigest() for v in documents],
            'input_token_counts':full,'retained_token_counts':retained,'native_truncation':self.tokenizer.truncation,
            'shape':list(vectors.shape),'norms':__import__('numpy').linalg.norm(vectors,axis=1).tolist()})
        return vectors
    base.ONNXMiniLM_L6_V2._forward=observed_forward
    patched=[]
    try:
        for name in ('httpx','httpx2'):
            try:module=importlib.import_module(name)
            except ImportError:continue
            if not hasattr(module,'AsyncClient'):continue
            original=module.AsyncClient.send
            def make_send(native_send):
                async def send(self,request,*args,**kwargs):
                    if str(request.url).startswith(config['base_url']) and request.url.path.endswith('/chat/completions'):
                        identity=IDENTITY.get()
                        if identity is None:raise ContractError('Model request outside native branch')
                        before=bytes(request.content)
                        request.headers['X-SCAN-Replica']=str(identity['branch'])
                        request.headers['X-SCAN-Attempt']=identity['attempt']
                        request.headers['X-SCAN-Phase']=identity['phase']
                        assert before==bytes(request.content)
                        model_requests.append({'identity':dict(identity),'raw_request_body_text':before.decode('utf8'),
                                               'request_sha256':hashlib.sha256(before).hexdigest(),'url':str(request.url)})
                    return await native_send(self,request,*args,**kwargs)
                return send
            module.AsyncClient.send=make_send(original);patched.append((module,original))
        yield
    finally:
        for module,original in reversed(patched):module.AsyncClient.send=original
        base.ONNXMiniLM_L6_V2._forward=native_forward

class ConnectedMemory(ChromaDBVectorMemory):
    def __init__(self,*,config,bridge_config,evidence):
        super().__init__(config)
        self.bridge_config=bridge_config;self.evidence=evidence
    async def clear(self):
        await super().clear()
        self.evidence['storage'].append({'event':'clear','identity':dict(IDENTITY.get()),'count':self._collection.count(),
                                        'path':self._config.persistence_path})
    async def add(self,content,cancellation_token=None):
        await super().add(content,cancellation_token)
        self.evidence['storage'].append({'event':'add','identity':dict(IDENTITY.get()),
                                        'native_content':content.model_dump(mode='json'),'count':self._collection.count()})
    async def query(self,query,cancellation_token=None,**kwargs):
        identity=dict(IDENTITY.get())
        key=rpc(self.bridge_config,'native-start',{'query':query,'backend':'AutoGen native ChromaDBVectorMemory',
              'metadata':{'k':self._config.k,'score_threshold':self._config.score_threshold,'distance_metric':self._config.distance_metric}})['call_key']
        original=await super().query(query,cancellation_token,**kwargs)
        snapshot=original.model_dump(mode='json')
        record={'identity':identity,'call_key':key,'query':query,'native_result':snapshot,
                'memory_config':self._config.model_dump(mode='json'),'collection_metadata':self._collection.metadata,
                'collection_configuration':self._collection.configuration,
                'stored_native_records':self._collection.get(include=['documents','metadatas'])}
        self.evidence['retrieval'].append(record)
        chunks=rpc(self.bridge_config,'native-return',{'call_key':key,'query':query,
                   'chunks':[str(v.content) for v in original.results],
                   'metadata':{'native_results':[v.model_dump(mode='json') for v in original.results],
                               'k':self._config.k,'score_threshold':self._config.score_threshold}})['chunks']
        copied=copy_returned_result(original,chunks)
        assert original.model_dump(mode='json')==snapshot,'Native result was mutated'
        record['returned_result']=copied.model_dump(mode='json')
        record['native_result_unchanged']=True
        return copied

class Connections(base.BodyConnections):
    def visit_Call(self,node):
        node=super().visit_Call(node)
        if isinstance(node.func,ast.Name) and node.func.id=='OpenAIChatCompletionClient':node.func.id='model_factory'
        return node

body_ast=Connections().visit(ast.parse(base.cell(base.NOTEBOOK,17)+'\n'+base.cell(base.NOTEBOOK,18)))
parameters=['state_path','controlled_sources','ordinary_input','stub_url','memory_factory','model_factory','observe_console']
function=ast.AsyncFunctionDef(name='connected_official_body',args=ast.arguments(posonlyargs=[],args=[ast.arg(arg=v) for v in parameters],kwonlyargs=[],kw_defaults=[],defaults=[]),body=body_ast.body+[ast.Return(value=ast.Name(id='rag_assistant',ctx=ast.Load()))],decorator_list=[])
MODULE=ast.fix_missing_locations(ast.Module(body=[function],type_ignores=[]))
GLOBALS=dict(base.BODY_GLOBALS)
exec(compile(MODULE,str(base.MEMORY_PATH)+'#connected-full-body','exec'),GLOBALS)
BODY=GLOBALS['connected_official_body']

async def native_async(config):
    """Native integration entry; caller owns extension gate and shared measurement."""
    attempt=config.get('attempt_id',config.get('run_id',''))
    phase=config['phase'];count=config['N']
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',attempt) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',phase):raise ContractError('Invalid private-storage component')
    if type(count)is not int or count<1:raise ContractError('Invalid branch count')
    state=Path(config['state_dir']).resolve();state.mkdir(parents=True,exist_ok=True)
    location=state/attempt/phase
    location.mkdir(parents=True,exist_ok=False)
    ordinary=config['query']+'\n\n'+config['history']
    evidence={'attempt':attempt,'phase':phase,'branch_count':count,'ordinary_input':ordinary,'source_commit':base.COMMIT,
              'stage':config.get('stage','candidate caller-controlled'),'schedule':[],'storage':[],'retrieval':[],
              'task_results':[],'failures':[],'actual_body_ONNX_sessions':[],'model_requests':[],
              'official_indexer_cell_sha256':hashlib.sha256(base.cell(base.NOTEBOOK,15).encode()).hexdigest(),
              'connected_body_ast':ast.unparse(MODULE),'native_processor_ast':ast.unparse(base.processor_ast)}
    models=[];memories=[]
    async def execute_body(self,message,ctx):
        branch=int(self.id.type.removeprefix('agent_'))-1
        identity={'attempt':attempt,'phase':phase,'branch':branch}
        token=IDENTITY.set(identity)
        directory=location/f'R{branch}';directory.mkdir(parents=True,exist_ok=False)
        evidence['schedule'].append({'event':'start','identity':identity,'time':time.monotonic(),'task_id':message.task_id})
        branch_models=[];branch_memories=[]
        try:
            sources=[]
            for i,document in enumerate(config['documents']):
                path=directory/f'document_{i}.txt';path.write_text(document,encoding='utf8');sources.append(str(path))
            native_results=[]
            async def observe_console(stream):
                async def observed():
                    async for event in stream:
                        if isinstance(event,TaskResult):native_results.append(event)
                        yield event
                return await Console(observed())
            def memory_factory(*,config):
                value=ConnectedMemory(config=config,bridge_config=bridge,evidence=evidence)
                branch_memories.append(value);memories.append(value);return value
            def model_factory(**kwargs):
                value=OpenAIChatCompletionClient(**kwargs)
                branch_models.append(value);models.append(value);return value
            agent=await BODY(str(directory/'chroma'),sources,ordinary,bridge['base_url'],memory_factory,model_factory,observe_console)
            if len(native_results)!=1:raise ContractError('Missing original TaskResult')
            final=native_results[0].messages[-1]
            if final.source!='rag_assistant' or not isinstance(getattr(final,'content',None),str) or not final.content.strip():
                raise ContractError('No original assistant final generation')
            entries=[v for v in evidence['storage'] if v['event']=='add' and v['identity']==identity]
            indexer=base.BODY_GLOBALS['SimpleDocumentIndexer'](memory=branch_memories[0])
            expected=0
            for doc in config['documents']:
                native_text=indexer._strip_html(doc) if '<' in doc and '>' in doc else doc
                expected+=len(indexer._split_text(native_text))
            if len(entries)!=expected:raise ContractError('Original complete indexing chain did not ingest all native chunks')
            evidence['task_results'].append({'identity':identity,'native_TaskResult':{
                'messages':[m.model_dump(mode='json') for m in native_results[0].messages],'stop_reason':native_results[0].stop_reason},
                'expected_native_chunks':expected,'observed_native_chunks':len(entries),'final_assistant_generation':final.content})
        except BaseException as error:
            evidence['failures'].append({'identity':identity,'exception':repr(error),'traceback':traceback.format_exc()})
            raise
        finally:
            for model in branch_models:
                try:await model.close()
                except BaseException as error:evidence['failures'].append({'identity':identity,'resource_cleanup_exception':repr(error)})
            for memory in branch_memories:
                try:await memory.close()
                except BaseException as error:evidence['failures'].append({'identity':identity,'resource_cleanup_exception':repr(error)})
            evidence['schedule'].append({'event':'end','identity':identity,'time':time.monotonic()})
            dump(directory/'branch_evidence.json',{'identity':identity,**{k:[v for v in evidence[k] if v.get('identity')==identity] for k in ['storage','retrieval','task_results','failures','actual_body_ONNX_sessions','model_requests']}})
            IDENTITY.reset(token)
    bridge=config
    # Recreate original official Processor handler/decorator/subscriptions; no scheduler replacement.
    processor_namespace=dict(base.namespace)
    exec(compile(base.processor_ast,str(base.CONCURRENT_PATH)+'#original-decorated-Processor','exec'),processor_namespace)
    processor=processor_namespace['Processor']
    processor.execute_body=execute_body
    runtime=SingleThreadedAgentRuntime()
    try:
        with connection_observers(config,evidence['actual_body_ONNX_sessions'],evidence['model_requests']):
            # Setup-only registration loop. Native runtime performs all branch scheduling.
            for i in range(count):
                await processor.register(runtime,f'agent_{i+1}',lambda i=i:processor(f'Agent {i+1}'))
            runtime.start()
            await runtime.publish_message(base.Task(task_id='scan-native-task'),topic_id=DefaultTopicId())
            await runtime.stop_when_idle()
        if evidence['failures'] or len(evidence['task_results'])!=count:
            raise ContractError('Native completion gate failed; see preserved branch failures')
        receipt={'native_recipe_completed':True,'native_primitive':'SingleThreadedAgentRuntime.publish_message + stop_when_idle',
                 'branch_count':count,'attempt':attempt,'phase':phase,'private_state':str(location),
                 'retrieval_calls':len(evidence['retrieval']),'actual_body_session_count':len(evidence['actual_body_ONNX_sessions']),
                 'all_native_TaskResults_complete':True,'all_native_final_generations_complete':True}
        dump(state/'native_complete.json',receipt)
        return evidence
    except BaseException as error:
        dump(state/'native_failure.json',{'native_recipe_completed':False,'attempt':attempt,'phase':phase,
              'error':repr(error),'traceback':traceback.format_exc(),'branch_failures':evidence['failures']})
        raise
    finally:
        dump(location/'native_evidence.json',evidence)

def native(config):return asyncio.run(native_async(config))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--native-input',required=True)
    native(json.loads(Path(parser.parse_args().native_input).read_text(encoding='utf8')))



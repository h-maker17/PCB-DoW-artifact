"""Isolated rank7 candidate. No formal SCAN runner, classifier, or shared pipeline import.

Native runtime dispatch and native RAG notebook assembly are retained. The only
mock is a loopback LLM completion transport; all embeddings/search are real.
"""
from __future__ import annotations
import ast
import asyncio
import contextvars
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PREP = Path(__file__).resolve().parent
SCAN = PREP.parents[1]
SOURCE = PREP / 'source'
COMMIT = '027ecf0a379bcc1d09956d46d12d44a3ad9cee14'
BRANCH = contextvars.ContextVar('autogen_contract_branch',default=None)
for package in ('autogen-ext','autogen-agentchat','autogen-core'):
    sys.path.insert(0, str(SOURCE/'python/packages'/package/'src'))
os.environ['PYTHONDONTWRITEBYTECODE']='1'
os.environ['TEMP']=str(PREP/'temp')
os.environ['TMP']=str(PREP/'temp')
os.environ['HF_HOME']=str(PREP/'cache/huggingface')
os.environ['XDG_CACHE_HOME']=str(PREP/'cache')
os.environ['ANONYMIZED_TELEMETRY']='False'
os.environ['CUDA_VISIBLE_DEVICES']=''

import autogen_core
import autogen_agentchat
import autogen_ext
from autogen_core import DefaultTopicId,MessageContext,RoutedAgent,SingleThreadedAgentRuntime,default_subscription,message_handler
from autogen_core.memory import MemoryQueryResult
from autogen_agentchat.ui import Console
from autogen_agentchat.base import TaskResult
from autogen_ext.memory.chromadb import ChromaDBVectorMemory
from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import ONNXMiniLM_L6_V2

# Filesystem relocation only; the original native embedding factory/model is retained.
ONNXMiniLM_L6_V2.DOWNLOAD_PATH = PREP/'cache/chroma/all-MiniLM-L6-v2'
for module in (autogen_core,autogen_agentchat,autogen_ext):
    assert Path(module.__file__).resolve().is_relative_to(SOURCE.resolve()),module.__file__

MEMORY_PATH = SOURCE/'python/docs/src/user-guide/agentchat-user-guide/memory.ipynb'
CONCURRENT_PATH = SOURCE/'python/docs/src/user-guide/core-user-guide/design-patterns/concurrent-agents.ipynb'
NOTEBOOK=json.loads(MEMORY_PATH.read_text(encoding='utf8'))
CONCURRENT=json.loads(CONCURRENT_PATH.read_text(encoding='utf8'))

def cell(nb,i):return ''.join(nb['cells'][i]['source'])

@contextmanager
def logging_stub():
    calls=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            assert self.path=='/v1/chat/completions',self.path
            raw_body=self.rfile.read(int(self.headers['Content-Length']))
            value=json.loads(raw_body)
            assert value['model']=='gpt-4o',value
            assert not value.get('tools'), 'Selected memory RAG is not a ReAct/tool recipe'
            calls.append({'request':value,'raw_request_body_text':raw_body.decode('utf8'),'monotonic':time.monotonic()})
            answer={'id':'contract-'+str(len(calls)),'object':'chat.completion','created':int(time.time()),'model':'gpt-4o',
                    'choices':[{'index':0,'message':{'role':'assistant','content':'Audit records are retained for seven years.'},'finish_reason':'stop'}],
                    'usage':{'prompt_tokens':1,'completion_tokens':1,'total_tokens':2}}
            data=json.dumps(answer).encode()
            self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:yield f'http://127.0.0.1:{server.server_port}/v1',calls
    finally:server.shutdown();server.server_close();thread.join(timeout=5)

class ObservedMemory(ChromaDBVectorMemory):
    """Read-only query observer, with an optional retrieval-return hook for parent review.

    No update_context override, prompt editing, ID normalization, or query rewrite.
    """
    def __init__(self,*,config,events,retrieval_return_hook=None):
        super().__init__(config)
        self.events=events
        self.retrieval_return_hook=retrieval_return_hook
    async def query(self,query, cancellation_token=None,**kwargs):
        started=time.monotonic()
        result=await super().query(query,cancellation_token,**kwargs)
        original=result.model_dump(mode='json')
        if self.retrieval_return_hook is not None:
            # Parent may attach existing retrieval boundary observer after formal trigger.
            result=self.retrieval_return_hook(BRANCH.get(),result)
            assert isinstance(result,MemoryQueryResult)
        self.events.append({'branch':BRANCH.get(),'query':query,'query_started':started,'query_finished':time.monotonic(),
                            'native_result':original,'returned_result':result.model_dump(mode='json'),
                            'memory_config':self._config.model_dump(mode='json'),
                            'collection_metadata':self._collection.metadata,
                            'collection_configuration':self._collection.configuration,
                            'embedding_function':self._collection._embedding_function.get_config()})
        return result

class BodyConnections(ast.NodeTransformer):
    def visit_Call(self,node):
        node=self.generic_visit(node)
        if isinstance(node.func,ast.Name) and node.func.id=='PersistentChromaDBVectorMemoryConfig':
            for kw in node.keywords:
                if kw.arg=='persistence_path':kw.value=ast.Name(id='state_path',ctx=ast.Load())
        if isinstance(node.func,ast.Name) and node.func.id=='ChromaDBVectorMemory':node.func.id='memory_factory'
        if isinstance(node.func,ast.Name) and node.func.id=='OpenAIChatCompletionClient':
            node.keywords.extend([ast.keyword(arg='api_key',value=ast.Constant('local-contract-stub')),
                                  ast.keyword(arg='base_url',value=ast.Name(id='stub_url',ctx=ast.Load()))])
        if isinstance(node.func,ast.Attribute) and node.func.attr=='run_stream':
            for kw in node.keywords:
                if kw.arg=='task':kw.value=ast.Name(id='ordinary_input',ctx=ast.Load())
        if isinstance(node.func,ast.Name) and node.func.id=='Console':node.func.id='observe_console'
        return node
    def visit_Assign(self,node):
        node=self.generic_visit(node)
        if any(isinstance(t,ast.Name) and t.id=='sources' for t in node.targets):
            node.value=ast.Name(id='controlled_sources',ctx=ast.Load())
        return node

# Exact official full indexer cell, including fetch/HTML handling/chunking/error behavior.
BODY_GLOBALS={}
exec(compile(cell(NOTEBOOK,15),str(MEMORY_PATH)+'#cell15','exec'),BODY_GLOBALS)
# The necessary RAG cells, with only declared inputs/connections/observation substituted.
body_ast=BodyConnections().visit(ast.parse(cell(NOTEBOOK,17)+'\n'+cell(NOTEBOOK,18)))
func=ast.AsyncFunctionDef(name='official_body',args=ast.arguments(posonlyargs=[],args=[ast.arg(arg=n) for n in ['state_path','controlled_sources','ordinary_input','stub_url','memory_factory','observe_console']],kwonlyargs=[],kw_defaults=[],defaults=[]),body=body_ast.body+[ast.Return(value=ast.Name(id='rag_assistant',ctx=ast.Load()))],decorator_list=[])
module=ast.fix_missing_locations(ast.Module(body=[func],type_ignores=[]))
exec(compile(module,str(MEMORY_PATH)+'#cells17-18-connections','exec'),BODY_GLOBALS)
OFFICIAL_BODY=BODY_GLOBALS['official_body']

# Preserve official Task dataclass and Processor decorator/handler. Replace mock sleep
# with the complete official RAG body. Dispatch is not an author loop or gather.
TASK_GLOBALS={'dataclass':__import__('dataclasses').dataclass}
exec(compile(cell(CONCURRENT,2),str(CONCURRENT_PATH)+'#cell2','exec'),TASK_GLOBALS)
Task=TASK_GLOBALS['Task']
processor_ast=ast.parse(cell(CONCURRENT,4))
for node in ast.walk(processor_ast):
    if isinstance(node,ast.AsyncFunctionDef) and node.name=='on_task':
        node.body=ast.parse('await self.execute_body(message, ctx)').body
namespace={'default_subscription':default_subscription,'RoutedAgent':RoutedAgent,'message_handler':message_handler,
           'Task':Task,'MessageContext':MessageContext}
exec(compile(ast.fix_missing_locations(processor_ast),str(CONCURRENT_PATH)+'#cell4-body-substitution','exec'),namespace)
Processor=namespace['Processor']

async def run_contract(state_dir:Path,ordinary_input:str,documents:list[str],stub_url:str):
    """Two-branch native contract smoke only. It cannot run a formal N4 arm."""
    assert state_dir.resolve().is_relative_to(PREP.resolve())
    state_dir.mkdir(parents=True,exist_ok=False)
    sources=[]
    for i,document in enumerate(documents):
        path=state_dir/f'document_{i}.txt';path.write_text(document,encoding='utf8');sources.append(str(path))
    retrieval=[];completion=[];schedule=[]
    async def execute_body(self,message,ctx):
        identity=self.id.type
        token=BRANCH.set(identity)
        schedule.append({'event':'start','branch':identity,'time':time.monotonic(),'task_id':message.task_id})
        try:
            native_results=[]
            async def observe_console(stream):
                async def observed():
                    async for event in stream:
                        if isinstance(event,TaskResult):native_results.append(event)
                        yield event
                return await Console(observed())
            def factory(*,config):return ObservedMemory(config=config,events=retrieval)
            agent=await OFFICIAL_BODY(str(state_dir/identity/'chroma'),sources,ordinary_input,stub_url,factory,observe_console)
            await agent._model_client.close()
            assert len(native_results)==1 and native_results[0].messages
            completion.append({'branch':identity,'messages':[m.model_dump(mode='json') for m in native_results[0].messages],
                               'stop_reason':native_results[0].stop_reason})
        finally:
            schedule.append({'event':'end','branch':identity,'time':time.monotonic()});BRANCH.reset(token)
    Processor.execute_body=execute_body
    runtime=SingleThreadedAgentRuntime()
    # Original concurrent-agents cell5 two-branch registration/dispatch/idle chain.
    await Processor.register(runtime,'agent_1',lambda:Processor('Agent 1'))
    await Processor.register(runtime,'agent_2',lambda:Processor('Agent 2'))
    runtime.start()
    await runtime.publish_message(Task(task_id='contract-smoke'),topic_id=DefaultTopicId())
    await runtime.stop_when_idle()
    assert len(completion)==2,'Native dispatch is insufficient without completed bodies'
    return {'retrieval':retrieval,'completion':completion,'schedule':schedule,
            'official_indexer_cell_sha256':hashlib.sha256(cell(NOTEBOOK,15).encode()).hexdigest(),
            'connection_transformed_body':ast.unparse(module),'native_processor_body':ast.unparse(processor_ast)}


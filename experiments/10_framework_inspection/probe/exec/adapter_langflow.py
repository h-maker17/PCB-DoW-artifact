"""Official Vector Store RAG bodies feeding native Merge Flows/Graph.

No prompt assembly, hand-coded branch scheduler, or replacement retriever.
"""
import argparse
import asyncio
import copy
import json
import os
from pathlib import Path
import re
import sys
from native_bridge import ROOT,REPLICA,rpc,launch


def run(ctx,row):
    # Retrieval is a pre-Agent graph node. Its calculator/date tools remain
    # available; a grounded canned final answer need not call those tools.
    ctx.allow_canned_answer_with_tools=True
    launch(ctx,row,'env_langflow','lf','adapter_langflow.py')


async def native(config):
    state=Path(config['state_dir'])
    os.environ.update({'LANGFLOW_CONFIG_DIR':str(state/'config'),'LANGFLOW_DATABASE_URL':'sqlite:///'+(state/'langflow.db').as_posix(),
        'LANGFLOW_KNOWLEDGE_BASES_DIR':str(state/'knowledge'),'LANGFLOW_SUPERUSER':'scan',
        'LANGFLOW_SUPERUSER_PASSWORD':'scan-local-only','LANGFLOW_SSRF_ALLOWED_HOSTS':'127.0.0.1',
        'OPENAI_API_KEY':'scan-local','OPENAI_BASE_URL':config['base_url']})
    sys.path[:0]=[str(ROOT/'evidence/runtime/lf/src/lfx/src'),str(ROOT/'evidence/runtime/lf/src/backend/base')]
    import httpx
    from langchain_core.embeddings import Embeddings
    from langchain_core.documents import Document
    from lfx.base.models import unified_models
    from lfx.base.knowledge_bases.backends.base import BaseVectorStoreBackend
    from lfx.base.knowledge_bases.backends.chroma import ChromaLocalBackend
    from lfx.graph import Graph
    from lfx.graph.vertex.base import Vertex
    from lfx.components.files_ingestion.merge_flows import MergeFlowsComponent
    from lfx.custom.utils import build_custom_component_template_from_inputs
    from langflow.services.utils import initialize_services,teardown_services
    from langflow.services.deps import session_scope
    from langflow.services.database.models.user.model import User
    from langflow.services.database.models.message.model import MessageTable
    from langflow.api.utils.knowledge_base_service import create_record
    from langflow.api.utils.kb_helpers import resolve_local_store_path
    from sqlmodel import select

    class CPUEmbeddings(Embeddings):
        def embed_documents(self,texts):return rpc(config,'embeddings',{'texts':texts})['vectors']
        def embed_query(self,text):return self.embed_documents([text])[0]

    embeddings=CPUEmbeddings()
    # Only model/embedding connections are adapted. Graph, Knowledge, parser,
    # prompt, Agent, tools, history loading and coordinator stay native.
    original_embeddings=unified_models.get_embeddings
    unified_models.get_embeddings=lambda *a,**k:embeddings
    native_search=BaseVectorStoreBackend.similarity_search
    async def observe_search(self,query,k,**kwargs):
        key=rpc(config,'native-start',{'query':query,'backend':'Langflow native Chroma','k':k})['call_key']
        result=await native_search(self,query,k,**kwargs)
        contents=[doc.page_content for doc,_ in result]
        value=rpc(config,'native-return',{'call_key':key,'query':query,'backend':'Langflow native Chroma',
            'chunks':contents,'metadata':{'k':k,'scores':[float(s) for _,s in result],
                                       'documents':[doc.metadata for doc,_ in result]}})
        result=[(doc.model_copy(update={'page_content':text}),score) for (doc,score),text in zip(result,value['chunks'],strict=True)]
        return result
    BaseVectorStoreBackend.similarity_search=observe_search

    native_build=Vertex.build
    async def observed_build(self,*args,**kwargs):
        match=re.search(r'-scanR(\d+)$',self.id)
        token=REPLICA.set(int(match[1])) if match else None
        try:return await native_build(self,*args,**kwargs)
        finally:
            if token is not None:REPLICA.reset(token)
    Vertex.build=observed_build
    original_send=httpx.AsyncClient.send
    async def observed_send(self,request,*args,**kwargs):
        if str(request.url).startswith(config['base_url']) and request.url.path.endswith('/chat/completions'):
            replica=REPLICA.get();assert replica is not None,'LLM request outside native branch'
            request.headers['X-SCAN-Replica']=str(replica)
        return await original_send(self,request,*args,**kwargs)
    httpx.AsyncClient.send=observed_send

    await initialize_services()
    try:
        async with session_scope() as session:
            user=(await session.exec(select(User).where(User.username=='scan'))).one()
            user_id=user.id
            for i in range(config['N']):
                session.add(MessageTable(sender='User',sender_name='User',session_id='scan-session',context_id=f'scan_R{i}',
                    text=config['history'],user_id=user_id,flow_id='11111111-1111-4111-8111-111111111111'))
            await session.commit()
        kb='scan_corpus'
        selection=[{'name':'all-MiniLM-L6-v2','provider':'Hugging Face','metadata':{'model_type':'embedding'}}]
        await create_record(user_id=user_id,name=kb,model_selection=selection,backend_type='chroma')
        path=resolve_local_store_path(kb,'scan',backend_type='chroma',backend_config={},create=True)
        backend=ChromaLocalBackend(kb_name=kb,kb_path=path,embedding_function=embeddings,user_id=user_id)
        await backend.add_documents([Document(page_content=t,id=f'scan_document_{i}',metadata={'source':f'scan_document_{i}.txt'}) for i,t in enumerate(config['documents'])])
        await backend.teardown()

        source=ROOT/'evidence/official/langflow-ai__langflow/sources/src/backend/base/langflow/initial_setup/starter_projects/Vector Store RAG.json'
        body=json.loads(source.read_bytes())['data']
        assembled={'nodes':[],'edges':[]}
        for i in range(config['N']):
            branch=copy.deepcopy(body)
            mapping={n['id']:n['id']+f'-scanR{i}' for n in branch['nodes']}
            # UI identifiers are structural routing metadata, never prompt
            # bytes. Replace all serialized handle identifiers consistently.
            serialized=json.dumps(branch,ensure_ascii=False)
            for old,new in mapping.items():serialized=serialized.replace(old,new)
            branch=json.loads(serialized)
            for node in branch['nodes']:
                kind=node['data']['type'];template=node['data'].get('node',{}).get('template',{})
                if kind=='ChatInput':template['input_value']['value']=config['query']
                if kind=='Knowledge':template['knowledge_base']['value']=kb
                if kind in {'ChatInput','Agent','ChatOutput'}:template['context_id']['value']=f'scan_R{i}'
                if kind in {'ChatInput','ChatOutput'}:template['session_id']['value']='scan-session'
                if kind=='Agent':
                    template['api_key']['value']='scan-local'
                    template['model']['value']=[{'name':'gpt-4o','provider':'OpenAI','metadata':{'model_class':'ChatOpenAI','model_type':'language'}}]
            assembled['nodes']+=branch['nodes'];assembled['edges']+=branch['edges']
        merge_source=(ROOT/'evidence/runtime/lf/src/lfx/src/lfx/components/files_ingestion/merge_flows.py').read_text(encoding='utf-8')
        component=MergeFlowsComponent(_code=merge_source)
        node,_=build_custom_component_template_from_inputs(component)
        merge_id='MergeFlows-scan'
        assembled['nodes'].append({'id':merge_id,'type':'genericNode','position':{'x':1000,'y':0},
                                   'data':{'id':merge_id,'type':'MergeFlows','node':node}})
        for n in assembled['nodes']:
            if n['data']['type']!='ChatOutput':continue
            sh={'dataType':'ChatOutput','id':n['id'],'name':'message','output_types':['Message']}
            th={'fieldName':'inputs','id':merge_id,'inputTypes':['Data','DataFrame','Message','Tool','JSON','Table'],'type':'other'}
            assembled['edges'].append({'id':n['id']+'-merge','source':n['id'],'target':merge_id,
                'sourceHandle':json.dumps(sh).replace('"','œ'),'targetHandle':json.dumps(th).replace('"','œ'),
                'data':{'sourceHandle':sh,'targetHandle':th}})
        (state/'native_composed_graph.json').write_text(json.dumps(assembled,ensure_ascii=False,indent=2),encoding='utf-8')
        graph=Graph.from_payload(assembled,flow_id='11111111-1111-4111-8111-111111111111',user_id=str(user_id))
        await graph.arun(inputs=[{}],outputs=[merge_id],session_id='scan-session',fallback_to_env_vars=True)
        (state/'native_complete.json').write_text(json.dumps({'native_recipe_completed':True,'branch_count':config['N'],
            'parallel_executor':'Graph.arun→native default coordinator; MergeFlows dependencies','defaults_preserved':['Knowledge top_k5','parser pattern/separator','Prompt template','Agent tools/stream/history/max_iterations']}),encoding='utf-8')
    finally:
        await teardown_services()
        unified_models.get_embeddings=original_embeddings


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--native-input',required=True);args=parser.parse_args()
    asyncio.run(native(json.loads(Path(args.native_input).read_bytes())))

"""Official file-RAG body in native multi-model chat fan-out (step-2).

Native upload, loader, splitter, Chroma search, middleware and dispatch remain
unchanged. Only connection endpoints and retrieval-return observation adapt.
"""
import argparse
import asyncio
import copy
import io
import importlib
import json
import os
import shutil
from pathlib import Path
import sys
import uuid
from native_bridge import ROOT,REPLICA,rpc,launch


def run(ctx,row):
    # This official file-RAG body retrieves before generation; keep native tools.
    ctx.allow_canned_answer_with_tools=True
    launch(ctx,row,'env_openwebui','ow','adapter_openwebui.py')


def completion_failures(stored,identities):
    """Validate persisted native responses; an initial done flag is insufficient."""
    failures={}
    for key in identities:
        message=stored.get(key) if isinstance(stored,dict) else None
        reasons=[]
        if not isinstance(message,dict):reasons.append('missing branch')
        else:
            if message.get('id')!=key:reasons.append('wrong assistant identity')
            if message.get('role')!='assistant':reasons.append('wrong role')
            if message.get('error'):reasons.append('native error')
            if message.get('done') is not True:reasons.append('not done')
            content=message.get('content')
            if not isinstance(content,str) or not content.strip():reasons.append('empty persisted content')
            output=message.get('output')
            if not isinstance(output,list) or not output:reasons.append('missing terminal output')
            elif any(not isinstance(item,dict) or item.get('status')!='completed' for item in output):
                reasons.append('nonterminal output')
            elif not any(item.get('type')=='message' and item.get('role')=='assistant'
                         and any(isinstance(part,dict) and part.get('type')=='output_text'
                                 and isinstance(part.get('text'),str) and part['text'].strip()
                                 for part in item.get('content',[])) for item in output):
                reasons.append('empty terminal assistant output')
        if reasons:failures[key]={'reasons':reasons,'message':message}
    return failures


async def observe_completion(chats,chat_id,identities,state):
    # This is the official preferred getter, preserving native persistence.
    stored=await chats.get_messages_map_by_chat_id(chat_id)
    saved=await chats.get_chat_by_id(chat_id)
    legacy=saved.chat.get('history',{}).get('messages',{}) if saved else {}
    observed={key:stored.get(key) for key in identities} if isinstance(stored,dict) else None
    diagnostic={'preferred_getter':'Chats.get_messages_map_by_chat_id',
                'persisted_assistant_records':observed,
                'legacy_assistant_records':{key:legacy.get(key) for key in identities},
                'legacy_difference':observed!={key:legacy.get(key) for key in identities},
                'lost_update_explanation':'Hypothesis only; no database repair performed.'}
    (state/'completion_observation.json').write_text(json.dumps(diagnostic,indent=2),encoding='utf-8')
    failures=completion_failures(stored,identities)
    if failures:raise RuntimeError(f'Native branch completion failed: {failures}')
    return diagnostic


async def native(config):
    state=Path(config['state_dir'])
    (state/'data').mkdir(parents=True,exist_ok=True)
    # Native config clears top-level static files at import. Keep that native
    # behavior in this attempt's writable resource directory, preserving the
    # immutable archived source tree for the paired pass and later repeats.
    shutil.copytree(ROOT/'evidence/runtime/ow/backend/open_webui/static',state/'static')
    os.environ.update({'DATA_DIR':str(state/'data'),'STATIC_DIR':str(state/'static'),'OFFLINE_MODE':'true',
        'WEBUI_SECRET_KEY':'scan-local-isolated-only','OPENAI_API_BASE_URL':config['base_url'],
        'OPENAI_API_KEY':'scan-local','ENABLE_OLLAMA_API':'false','ENABLE_VERSION_UPDATE_CHECK':'false',
        'HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1','DO_NOT_TRACK':'true',
        'SCARF_NO_ANALYTICS':'true','ANONYMIZED_TELEMETRY':'false'})
    sys.path.insert(0,str(ROOT/'evidence/runtime/ow/backend'))
    import numpy as np
    import aiohttp
    from starlette.requests import Request
    from starlette.datastructures import UploadFile,Headers
    main=importlib.import_module('open_webui.main')
    from open_webui.config import Config
    from open_webui.env import RAG_SYSTEM_CONTEXT
    from open_webui.models.users import Users
    from open_webui.models.chats import Chats,ChatForm
    from open_webui.routers import files
    from open_webui.retrieval import utils as retrieval

    class UploadUUIDs:
        """Fixed corpus identity at ingestion; native upload/parser stays intact."""
        @staticmethod
        def uuid4():return uuid.UUID('22222222-2222-4222-8222-222222222222')
        def __getattr__(self,name):return getattr(uuid,name)

    class CPUModel:
        def encode(self,texts,**kwargs):
            single=isinstance(texts,str)
            texts=[texts] if single else texts
            prefix=kwargs.get('prompt','')
            values=rpc(config,'embeddings',{'texts':[prefix+t for t in texts],
                       'normalize_embeddings':kwargs.get('normalize_embeddings',False)})['vectors']
            result=np.asarray(values,dtype=np.float32)
            return result[0] if single else result

    # Same official default MiniLM bytes, loaded in parent CPU runtime. This
    # preserves native get_embedding_function batching and async behavior.
    main.get_ef=lambda *args,**kwargs:CPUModel()
    native_payload=main.process_chat_payload
    async def observed_payload(request,body,user,metadata,model):
        identity=metadata.get('message_id')
        if identity not in identities:raise RuntimeError('Unknown native model branch')
        REPLICA.set(identities[identity])
        return await native_payload(request,body,user,metadata,model)
    main.process_chat_payload=observed_payload
    native_query=retrieval.query_collection
    async def observed_query(request,collection_names,queries,embedding_function,k):
        query=json.dumps(queries,ensure_ascii=False,sort_keys=True)
        key=rpc(config,'native-start',{'query':query,'backend':'Open WebUI native Chroma',
                 'metadata':{'k':k,'collection_names':sorted(collection_names)}})['call_key']
        result=await native_query(request,collection_names,queries,embedding_function,k)
        chunks=result.get('documents',[[]])[0]
        changed=rpc(config,'native-return',{'call_key':key,'query':query,'chunks':chunks,
             'metadata':{p:v for p,v in result.items() if p!='documents'}})['chunks']
        result=copy.deepcopy(result)
        result['documents'][0]=changed
        return result
    retrieval.query_collection=observed_query
    native_request=aiohttp.ClientSession._request
    async def observed_request(self,method,url,*args,**kwargs):
        if str(url).startswith(config['base_url']) and str(url).rstrip('/').endswith('/chat/completions'):
            identity=REPLICA.get()
            assert identity is not None,'Unobserved native model request'
            kwargs['headers']={**(kwargs.get('headers') or {}),'X-SCAN-Replica':str(identity)}
        return await native_request(self,method,url,*args,**kwargs)
    aiohttp.ClientSession._request=observed_request

    # Observe native task handles; do not create or schedule replica branches.
    created=[]
    native_create=main.create_task
    async def observed_create(*args,**kwargs):
        answer=await native_create(*args,**kwargs)
        created.append(answer[1])
        return answer
    main.create_task=observed_create
    identities={f'scan_assistant_{i}':i for i in range(config['N'])}
    app=main.app
    def request():
        return Request({'type':'http','method':'POST','path':'/api/chat/completions',
          'query_string':b'','headers':[],'client':('127.0.0.1',1),'server':('127.0.0.1',80),
          'scheme':'http','app':app})

    async with app.router.lifespan_context(app):
        # Credentials/connection configuration only; native RAG defaults are
        # asserted and recorded before ingestion or any measured generation.
        await Config.upsert({'openai.api_base_urls':[config['base_url']],
                             'openai.api_keys':['scan-local']})
        defaults=await Config.get_many('rag.top_k','rag.top_k_reranker','rag.enable_hybrid_search',
          'rag.full_context','rag.embedding_engine','rag.embedding_model','rag.chunk_size',
          'rag.chunk_overlap','rag.template','task.query.retrieval.enable')
        defaults['env.RAG_SYSTEM_CONTEXT']=RAG_SYSTEM_CONTEXT
        assert RAG_SYSTEM_CONTEXT is False
        assert defaults['rag.top_k']==3 and defaults['rag.full_context'] is False
        assert defaults['rag.enable_hybrid_search'] is False and defaults['rag.embedding_engine']==''
        assert defaults['rag.embedding_model']=='sentence-transformers/all-MiniLM-L6-v2'
        (state/'native_defaults.json').write_text(json.dumps(defaults,indent=2),encoding='utf-8')
        user=await Users.insert_new_user('scan_user','SCAN','scan@local.invalid',role='admin')
        assert user is not None
        await main.get_all_models(request(),user=user)
        assert 'scan-stub' in app.state.MODELS
        # Corpus input is a declared single text file. Native default splitter
        # determines its chunks; no chunk concatenation occurs after retrieval.
        original_uuid=files.uuid
        files.uuid=UploadUUIDs()
        try:
            uploaded=await files.upload_file_handler(request(),
              UploadFile(io.BytesIO('\n\n'.join(config['documents']).encode()),filename='scan_corpus.txt',
                         headers=Headers({'content-type':'text/plain'})),
              metadata=None,process=True,process_in_background=False,user=user,background_tasks=None,db=None)
        finally:files.uuid=original_uuid
        file_id=uploaded['id'] if isinstance(uploaded,dict) else uploaded.id
        chat_id='11111111-1111-4111-8111-111111111111'
        history_message={'id':'scan_history','role':'user','content':config['history'],
                         'parentId':None,'childrenIds':['scan_question']}
        question={'id':'scan_question','role':'user','content':config['query'],
                  'parentId':'scan_history','childrenIds':list(identities)}
        assistants={key:{'id':key,'role':'assistant','model':'scan-stub','content':'',
                        'parentId':'scan_question','childrenIds':[]} for key in identities}
        messages={'scan_history':history_message,'scan_question':question,**assistants}
        await Chats.insert_new_chat(chat_id,user.id,ChatForm(chat={'title':'SCAN',
             'models':['scan-stub']*config['N'],'history':{'messages':messages,'currentId':'scan_question'}}))
        body={'model':'scan-stub','messages':[{'role':'user','content':config['history']},
             {'role':'user','content':config['query']}],'files':[{'type':'file','id':file_id}],
             'chat_id':chat_id,'session_id':'scan_observer_session','user_message':question,
             'message_ids':[{'model_id':'scan-stub','message_id':key} for key in identities]}
        (state/'native_input_payload.json').write_text(json.dumps(body,indent=2),encoding='utf-8')
        result=await main.chat_completion(request(),body,user=user)
        assert len(created)==config['N'],f'Native fan-out did not create N tasks: {result}'
        outputs=await asyncio.gather(*created,return_exceptions=True)
        errors=[repr(x) for x in outputs if isinstance(x,BaseException)]
        if errors:raise RuntimeError(errors)
        # Tasks are complete before reading the official preferred persisted map.
        await observe_completion(Chats,chat_id,identities,state)
        (state/'native_complete.json').write_text(json.dumps({'native_recipe_completed':True,
             'branch_count':len(created),'parallel_executor':'main.chat_completion→native create_task/process_chat',
             'step':2,'defaults_preserved':defaults},indent=2),encoding='utf-8')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--native-input',required=True);args=parser.parse_args()
    asyncio.run(native(json.loads(Path(args.native_input).read_bytes())))

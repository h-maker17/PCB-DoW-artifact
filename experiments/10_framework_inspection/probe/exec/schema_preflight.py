"""Inspect installed native interfaces without lifespan, tools or generation."""
import argparse
import inspect
import importlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from native_bridge import ROOT,verify_runtime


def main():
    p=argparse.ArgumentParser();p.add_argument('runtime',choices=['lf','ow']);args=p.parse_args()
    state=Path(tempfile.mkdtemp(prefix=f'schema_{args.runtime}_',dir=ROOT/'reports'))
    manifest=verify_runtime(args.runtime)
    os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',OFFLINE_MODE='true',
        DO_NOT_TRACK='true',SCARF_NO_ANALYTICS='true',ANONYMIZED_TELEMETRY='false')
    if args.runtime=='ow':
        (state/'data').mkdir()
        shutil.copytree(ROOT/'evidence/runtime/ow/backend/open_webui/static',state/'static')
        os.environ.update(DATA_DIR=str(state/'data'),STATIC_DIR=str(state/'static'),WEBUI_SECRET_KEY='scan-schema-local-only',
            ENABLE_OLLAMA_API='false',ENABLE_VERSION_UPDATE_CHECK='false',OPENAI_API_BASE_URL='http://127.0.0.1:1/v1',OPENAI_API_KEY='scan-local')
        sys.path.insert(0,str(ROOT/'evidence/runtime/ow/backend'))
        native=importlib.import_module('open_webui.main')
        from open_webui.config import Config
        from open_webui.models.users import Users
        from open_webui.models.chats import Chats
        from open_webui.routers import files
        from open_webui.retrieval.utils import query_collection
        functions=[native.chat_completion,native.create_task,native.process_chat_payload,Config.upsert,
            Users.insert_new_user,Chats.insert_new_chat,files.upload_file_handler,query_collection]
    else:
        os.environ.update(LANGFLOW_CONFIG_DIR=str(state/'config'),LANGFLOW_DATABASE_URL='sqlite:///'+(state/'langflow.db').as_posix(),
            LANGFLOW_KNOWLEDGE_BASES_DIR=str(state/'knowledge'),OPENAI_API_KEY='scan-local',OPENAI_BASE_URL='http://127.0.0.1:1/v1')
        sys.path[:0]=[str(ROOT/'evidence/runtime/lf/src/lfx/src'),str(ROOT/'evidence/runtime/lf/src/backend/base')]
        from lfx.graph import Graph
        from lfx.graph.vertex.base import Vertex
        from lfx.base.knowledge_bases.backends.base import BaseVectorStoreBackend
        from lfx.base.knowledge_bases.backends.chroma import ChromaLocalBackend
        from langflow.api.utils.knowledge_base_service import create_record
        from langflow.api.utils.kb_helpers import resolve_local_store_path
        functions=[Graph.arun,Vertex.build,BaseVectorStoreBackend.similarity_search,ChromaLocalBackend.add_documents,
                   create_record,resolve_local_store_path]
    signatures={f.__qualname__:str(inspect.signature(f)) for f in functions}
    verify_runtime(args.runtime)
    result={'runtime':args.runtime,'framework_commit':manifest['commit'],'source_files_verified':len(manifest['files']),
        'signatures':signatures,'state_directory':state.relative_to(ROOT).as_posix(),
        'source_bytes_intact_after_import':True,'lifespan_invoked':False,'retrieval_invoked':False,
        'tool_invoked':False,'model_invoked':False,'framework_pilot_invoked':False}
    (ROOT/f'reports/schema_preflight_{args.runtime}.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('SCHEMA_ONLY_COMPLETE',args.runtime,'SOURCE_BYTES_INTACT',len(manifest['files']))


if __name__=='__main__':main()

"""Fixed rank4 native executor; only provider connections and return observers.

Uses the shared SCAN model service. No framework scheduler/source/default patches.
"""
import argparse
import asyncio
import copy
from contextlib import contextmanager
import importlib
import hashlib
import json
import os
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parent))
from native_bridge import ROOT, REPLICA, rpc, launch

SUPPORT = ROOT/'exec/deerflow_support'
RUNTIME = ROOT/'preparation/deerflow_night/python312/python.exe'


def run(ctx, row):
    ctx.stub_tool_call = {'name':'knowledge_search','arguments':{'query':ctx.query}}
    launch(ctx, row, 'preparation/deerflow_night/python312', 'df',
           'adapter_deerflow.py', executable=RUNTIME)


@contextmanager
def branch_observer(replica):
    token = REPLICA.set(replica)
    try:
        yield
    finally:
        REPLICA.reset(token)


def observe_http(config):
    """Transport headers only; native request bodies and methods are untouched."""
    restores = []
    for name in ['httpx','httpx2']:
        try:
            module = importlib.import_module(name)
        except ImportError:
            continue
        for cls, asynchronous in [(module.Client,False),(module.AsyncClient,True)]:
            original = cls.send
            def decorate(native_send, asynchronous):
                def headers(request):
                    allowed = (str(request.url).startswith(config['base_url']) or
                               str(request.url).startswith(config['retrieval_url']))
                    if allowed:
                        identity = REPLICA.get()
                        if identity is None:
                            raise RuntimeError('Unattributed native model/retrieval request')
                        request.headers['X-SCAN-Replica'] = str(identity)
                if asynchronous:
                    async def send(self, request, *args, **kwargs):
                        headers(request)
                        return await native_send(self,request,*args,**kwargs)
                else:
                    def send(self, request, *args, **kwargs):
                        headers(request)
                        return native_send(self,request,*args,**kwargs)
                return send
            cls.send = decorate(original,asynchronous)
            restores.append((cls,original))
    return lambda: [setattr(cls,'send',original) for cls,original in restores]


def native(config):
    state = Path(config['state_dir']).resolve()
    os.environ['SCAN_DF_PHASE_ROOT'] = str(state)
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    os.environ['NO_PROXY'] = '127.0.0.1,localhost'
    sys.path.insert(0,str(SUPPORT))
    from isolation import prepare
    prepare()
    from candidate_adapter import make_config,dispatch_native,ordinary_task,collect_native,snapshot_result
    from run_artifacts import RunArtifacts
    from local_retrieval import LocalIndex,LocalService
    artifacts = RunArtifacts(config['run_id'],config['phase'],artifact_root=state/'artifacts')
    contract = json.loads((ROOT/'evidence/rank4_integration/controlled_provider_contract.json').read_bytes())
    index = LocalIndex(artifacts=artifacts)
    assert index.manifest['corpus_sha256'] == contract['embedding']['corpus_sha256']
    assert index.manifest['vector_sha256'] == contract['embedding']['vector_sha256']
    assert index.catalog == contract['datasets']
    assert index.documents == contract['documents'] and index.chunks == contract['chunks']
    observer_records = []
    def observed_return(replica,body,operation):
        if replica is None or not str(replica).isdigit() or not 0 <= int(replica) < config['N']:
            raise RuntimeError('Unknown branch at actual retrieval service')
        replica = int(replica)
        key = rpc(config,'native-start',{'query':body['question'],
            'backend':'controlled-local-minilm-faiss-ascii-coverage-v1',
            'metadata':{'parameters':copy.deepcopy(body)}},replica)['call_key']
        data = operation()
        chunks = [c['content'] for c in data['chunks']]
        changed = rpc(config,'native-return',{'call_key':key,'query':body['question'],
            'chunks':chunks,'metadata':{'chunks':copy.deepcopy(data['chunks']),
            'doc_aggs':copy.deepcopy(data['doc_aggs']),'total':data['total']}},replica)['chunks']
        output = copy.deepcopy(data)
        for chunk,text in zip(output['chunks'],changed,strict=True):
            chunk['content'] = text
        record = {'replica':replica,'call_key':key,'native_return':data,'observed_return':output}
        observer_records.append(record)
        artifacts.append(return_log,record)
        return output
    return_log = artifacts.new_log('native-retrieval-return')
    service = LocalService(index,retrieval_observer=observed_return)
    model_log = artifacts.new_log('shared-model-http')
    dispatch = None
    restore = None
    def evidence(replica,handle):
        models = rpc(config,'model-evidence',{},replica)['records']
        models = [{**r,'run_id':artifacts.run_id,'phase':artifacts.phase} for r in models]
        # Append snapshots with their observed provenance; no synthetic results.
        artifacts.append(model_log,{'replica':replica,'handle':handle,'records':models})
        with service.lock:
            retrieval = [copy.deepcopy(r) for r in service.events if str(r['replica'])==str(replica)]
        return {'model':models,'retrieval':retrieval}
    try:
        with service.serving() as retrieval_url:
            config['retrieval_url'] = retrieval_url
            restore = observe_http(config)
            app = make_config(config['base_url'],retrieval_url,service.token,
                contract['formal_default_scope']['dataset_ids'])
            assert app.subagent_batches.enabled is False
            dispatch = dispatch_native(app,ordinary_task(config['history'],config['query']),
                replicas=config['N'],parent_admission={'rank':4,'approved':True},
                artifacts=artifacts,evidence_provider=evidence,observer_context=branch_observer)
            results = asyncio.run(collect_native(dispatch))
            receipt = {'native_recipe_completed':True,'native_primitive':'SubagentExecutor.execute_async / native create_agent / astream',
                'branch_count':len(results),'run_id':artifacts.run_id,'phase':artifacts.phase,
                'artifacts':str(artifacts.root),'shared_stub':True,
                'ordinary_task':ordinary_task(config['history'],config['query']),
                'context_snapshot':None,'knowledge_scope_override':None,
                'capacity_defaults':app.subagent_runtime.model_dump(),
                'capacity_after_collection':__import__('dataclasses').asdict(dispatch['capacity'].snapshot()),
                'native_results':[snapshot_result(r) for r in results],
                'provider_contract_sha256':hashlib.sha256((ROOT/'evidence/rank4_integration/controlled_provider_contract.json').read_bytes()).hexdigest(),
                'original_candidate_provider_code_sha256':contract['local_retrieval_code_sha256'],
                'integrated_provider_code_sha256':hashlib.sha256((SUPPORT/'local_retrieval.py').read_bytes()).hexdigest(),
                'model_visible_native_uuid_preserved':True,
                'terminal_status_log':str(dispatch['status_log']),
                'native_retrieval_return_count':len(observer_records),
                'successful_HTTP_retrieval_count':sum(r['method']=='POST' and r['status']==200 for r in service.events)}
            (state/'native_complete.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    except BaseException as exc:
        artifacts.write_new('integration_failure.json',{'exception_type':type(exc).__name__,'error':str(exc),
            'dispatch_started':dispatch is not None,'classification':None})
        raise
    finally:
        if restore is not None:
            restore()
        (state/'artifact_reference.json').write_text(json.dumps({'root':str(artifacts.root),
            'run_id':artifacts.run_id,'phase':artifacts.phase,'formal_completion':(state/'native_complete.json').exists()},indent=2),encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--native-input',required=True)
    native(json.loads(Path(parser.parse_args().native_input).read_bytes()))

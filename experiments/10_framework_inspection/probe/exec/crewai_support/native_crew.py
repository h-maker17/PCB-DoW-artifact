"""Conditional CrewAI adapter candidate. Native N4 dispatch is implemented but disabled.
No classifier, render, protocol rewrite or custom branch scheduler is present.
"""
from pathlib import Path
import ast,asyncio,copy,hashlib,json,os,sys,threading
from contextlib import contextmanager
SCAN=Path(__file__).resolve().parents[2];OLD=SCAN/'preparation/crewai_night';ROOT=OLD/'revision2'
FORMAL_EXECUTION_ENABLED=False
class CandidateAdmissionRequired(RuntimeError):pass
def frozen_contract():
    return json.loads((ROOT/'evidence/frozen_input_contract.json').read_text(encoding='utf-8'))
def validate_input(config):
    frozen=frozen_contract()
    for key in ['query','history','documents']:
        if config[key]!=frozen[key]:raise ValueError('Frozen input drift: '+key)
    if not isinstance(config['N'],int) or not 1<=config['N']<=4:raise ValueError('N outside declared interface')
    if config.get('phase') not in ('benign','attacked'):raise ValueError('Unknown phase')
    state=Path(config['state_dir']).resolve()
    if not (state.is_relative_to(SCAN/'reports') or state.is_relative_to(SCAN/'exec/pilot')):raise ValueError('Shared private state outside authorized project run directories')
    return frozen
def ordinary_inputs(config):
    validate_input(config)
    question=config['query']+'\n\n'+config['history']
    return [{'question':question} for _ in range(config['N'])]
def plan_n4(ctx,row):
    frozen=frozen_contract()
    config={'query':ctx.query,'history':ctx.history,'documents':frozen['documents'],'N':4,'phase':ctx.phase,'state_dir':str(ROOT/'data/N4_admission_pending'),'base_url':ctx.base_url,'framework':row}
    return {'config':config,'inputs':ordinary_inputs(config),'formal_execution_enabled':False,'native_primitive':'Crew.kickoff_for_each_async','inner_process':'sequential','dispatch_status':'disabled until parent admission/integration'}
def run(ctx,row):
    plan_n4(ctx,row)
    raise CandidateAdmissionRequired('Rank8 formal extension is not admitted; candidate only, shared integration untouched')
def configure_runtime(state):
    temp=state/'temp';temp.mkdir(parents=True,exist_ok=True)
    os.environ.update({'TMP':str(temp),'TEMP':str(temp),'CREWAI_STORAGE_DIR':str(state/'native_crewai'),'OTEL_SDK_DISABLED':'true','CREWAI_DISABLE_TELEMETRY':'true','CREWAI_TRACING_ENABLED':'false','ANONYMIZED_TELEMETRY':'false','OPENAI_API_KEY':'SCAN-local-candidate-placeholder','PYTHONDONTWRITEBYTECODE':'1','NO_PROXY':'127.0.0.1,localhost','HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1','OMP_NUM_THREADS':'1','MKL_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1'})
    os.chdir(state)
    for package in ['crewai','crewai-core','cli']:sys.path.insert(0,str(OLD/'source/lib'/package/'src'))
    receipt=json.loads((ROOT/'evidence/shared_stub_source_receipt.json').read_text(encoding='utf-8'))
    if hashlib.sha256((SCAN/'exec/native_bridge.py').read_bytes()).hexdigest()!=receipt['native_bridge_snapshot_sha256']:raise RuntimeError('Shared native_bridge source drift; preparation must stop')
    sys.path.insert(0,str(SCAN/'exec'))
def construction_ast(content):
    source=(ROOT/'evidence/original_basic_string.py').read_text(encoding='utf-8')
    tree=ast.parse(source);body=[];changes=[]
    for node in tree.body:
        if isinstance(node,ast.Assign) and any(isinstance(x,ast.Name) and x.id=='result' for x in node.targets):
            assert isinstance(node.value,ast.Call) and isinstance(node.value.func,ast.Attribute) and node.value.func.attr=='kickoff'
            continue
        if isinstance(node,ast.Assign) and len(node.targets)==1 and isinstance(node.targets[0],ast.Name) and node.targets[0].id=='content':
            node.value=ast.Name(id='__scan_controlled_content',ctx=ast.Load());changes.append('content assignment RHS only')
        body.append(node)
    assert changes==['content assignment RHS only']
    return ast.fix_missing_locations(ast.Module(body=body,type_ignores=[]))
def build_crew(documents):
    content='\n\n'.join(documents);namespace={'__scan_controlled_content':content}
    exec(compile(construction_ast(content),str(ROOT/'evidence/original_basic_string.py')+'#corpus_connection','exec'),namespace)
    return namespace['crew']
async def invoke_official_parallel(crew,inputs):
    # One native call. Native code alone copies and schedules branch bodies.
    return await crew.kickoff_for_each_async(inputs=inputs)
def changed_return_rows(native_rows,changed_chunks):
    if len(native_rows)!=len(changed_chunks):raise RuntimeError('Observer changed cardinality')
    return [dict(row,content=text) for row,text in zip(native_rows,changed_chunks,strict=True)]
@contextmanager
def native_observers(config,observer,proxy_base):
    from crewai import Crew
    from crewai.knowledge.storage.knowledge_storage import KnowledgeStorage
    from native_bridge import REPLICA
    oldcopy=Crew.copy;oldkickoff=Crew.kickoff;oldsearch=KnowledgeStorage.search
    ids={};copies=[];returns=[];mutex=threading.Lock();transport_patches=[]
    def observe_copy(self,*args,**kwargs):
        copied=oldcopy(self,*args,**kwargs)
        with mutex:
            replica=len(copies)
            if replica>=config['N']:raise RuntimeError('Unexpected native copy count')
            ids[str(copied.id)]=replica
            copies.append({'replica':replica,'crew_uuid':str(copied.id),'agent_uuid':str(copied.agents[0].id),'task_uuid':str(copied.tasks[0].id),'source_object_ids':[id(s) for s in copied.knowledge_sources]})
        return copied
    def observe_kickoff(self,*args,**kwargs):
        replica=ids.get(str(self.id))
        if replica is None:raise RuntimeError('Native kickoff has no observed copy identity')
        token=REPLICA.set(replica)
        try:return oldkickoff(self,*args,**kwargs)
        finally:REPLICA.reset(token)
    def observe_search(self,query,*args,**kwargs):
        replica=REPLICA.get()
        if replica is None:raise RuntimeError('Native search outside an identified copy')
        metadata={'collection_name':self.collection_name,'native_search_kwargs':kwargs}
        key=observer.retrieval_start(replica,query,'CrewAI native KnowledgeStorage/ChromaDBClient',metadata)
        raw=oldsearch(self,query,*args,**kwargs);raw_snapshot=copy.deepcopy(raw)
        details={**metadata,'native_rows':copy.deepcopy(raw_snapshot)}
        changed=observer.retrieval_return(replica,key,query,[r['content'] for r in raw],details)
        result=changed_return_rows(raw,changed)
        if raw!=raw_snapshot:raise RuntimeError('Native raw results were mutated')
        with mutex:returns.append({'replica':replica,'call_key':key,'query':query,'native_rows':raw_snapshot,'returned_rows':copy.deepcopy(result),'raw_unchanged':True})
        return result
    Crew.copy=observe_copy;Crew.kickoff=observe_kickoff;KnowledgeStorage.search=observe_search
    import importlib
    for name in ['httpx','httpx2']:
        try:module=importlib.import_module(name)
        except ImportError:continue
        previous=module.Client.send
        def make_send(native_send):
            def send(self,request,*args,**kwargs):
                if str(request.url).startswith(proxy_base) and request.url.path.endswith('/chat/completions'):
                    identity=REPLICA.get()
                    if identity is None:raise RuntimeError('Native model request outside branch')
                    request.headers['X-SCAN-Replica']=str(identity)
                return native_send(self,request,*args,**kwargs)
            return send
        module.Client.send=make_send(previous);transport_patches.append((module,previous))
    try:yield {'copies':copies,'retrievals':returns}
    finally:
        Crew.copy=oldcopy;Crew.kickoff=oldkickoff;KnowledgeStorage.search=oldsearch
        for module,previous in transport_patches:module.Client.send=previous
class SharedObserver:
    def __init__(self,config):self.config=config
    def retrieval_start(self,replica,query,backend,metadata=None):
        from native_bridge import rpc
        return rpc(self.config,'native-start',{'query':query,'backend':backend,'metadata':metadata},replica)['call_key']
    def retrieval_return(self,replica,key,query,chunks,metadata=None):
        from native_bridge import rpc
        return rpc(self.config,'native-return',{'call_key':key,'query':query,'chunks':chunks,'metadata':metadata},replica)['chunks']
def enforce_admission(config,admission_receipt=None):
    # Actual preparation authorization is distinct from a formal approval.
    # No simulated top6/rank7/rank8 acceptance booleans are accepted here.
    from selection_gate import verify_rank8_preparation,verify_continuation,read
    verify_rank8_preparation(config['framework'])
    state=Path(config['state_dir']).resolve()
    if state.is_relative_to((SCAN/'exec/pilot').resolve()):
        from pipeline import code_revision
        verify_continuation(read('config/frameworks.json'),'crewAIInc/crewAI',code_revision()['commit'])
    elif not state.is_relative_to((SCAN/'reports').resolve()):
        raise CandidateAdmissionRequired('Pre-run state outside project reports')

async def execute_candidate(config,*,admission_receipt=None):
    validate_input(config)
    enforce_admission(config,admission_receipt)
    state=Path(config['state_dir']).resolve();state.mkdir(parents=True,exist_ok=False);configure_runtime(state)
    from method_audit import MethodExecutionAudit
    from shared_proxy import SharedStubProxy
    audit=MethodExecutionAudit();observer=SharedObserver(config)
    with audit:
        with SharedStubProxy(config) as proxy:
            os.environ['OPENAI_BASE_URL']=proxy.base_url
            with native_observers(config,observer,proxy.base_url) as observation:
                crew=build_crew(config['documents'])
                outputs=await invoke_official_parallel(crew,ordinary_inputs(config))
                snapshot=crew.knowledge.storage._get_client().client.get_collection('knowledge_crew').get(include=['documents','metadatas'])
            report={'formal_execution':state.is_relative_to((SCAN/'exec/pilot').resolve()),'classification_performed':False,'native_recipe_completed':len(outputs)==config['N'] and all(o.raw for o in outputs) and len(observation['copies'])==config['N'] and {x['replica'] for x in observation['retrievals']}==set(range(config['N'])) and not any(x['kind']=='transport_error' for x in proxy.receipts),'branch_count':len(outputs),'native_primitive':'Crew.kickoff_for_each_async','inner_process':str(crew.process),'tools':crew.agents[0].tools,'outputs':[o.model_dump(mode='json') for o in outputs],'observer':observation,'storage_snapshot_after':snapshot,'transport_receipts':proxy.receipts,'S_utf8_sha256':hashlib.sha256(config['history'].encode()).hexdigest(),'query':config['query'],'knowledge_content':crew.knowledge_sources[0].content}
    report['actual_method_source_audit']=audit.receipt()
    report['integration_code_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    report['candidate_code_sha256']=hashlib.sha256((ROOT/'candidate/crewai_n4_candidate.py').read_bytes()).hexdigest()
    report['frozen_input_contract_sha256']=hashlib.sha256((ROOT/'evidence/frozen_input_contract.json').read_bytes()).hexdigest()
    if not report['native_recipe_completed']:
        (state/'native_incomplete_evidence.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str),encoding='utf8')
        raise RuntimeError('Original native bodies did not complete retrieval for every native copy')
    (state/'native_candidate_complete.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    (state/'native_complete.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    return report

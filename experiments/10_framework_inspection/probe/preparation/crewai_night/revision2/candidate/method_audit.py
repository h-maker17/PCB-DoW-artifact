"""Source audit using executable methods, never inspect.getfile(class)."""
from pathlib import Path
import json,hashlib,threading,sys,time
OLD=Path(__file__).resolve().parents[2]
def sha(data):return hashlib.sha256(data).hexdigest()
class MethodExecutionAudit:
    def __init__(self):
        from crewai import Crew,Agent,Task,LLM
        from crewai.knowledge.knowledge import Knowledge
        from crewai.knowledge.storage.knowledge_storage import KnowledgeStorage
        from crewai.knowledge.source.string_knowledge_source import StringKnowledgeSource
        from crewai.rag.chromadb.client import ChromaDBClient
        from crewai.crews.utils import run_for_each_async
        from crewai.llms.providers.openai.completion import OpenAICompletion
        items={'Crew.kickoff':Crew.kickoff,'Crew.kickoff_for_each_async':Crew.kickoff_for_each_async,'Crew.kickoff_async':Crew.kickoff_async,'Crew.copy':Crew.copy,'Agent.execute_task':Agent.execute_task,'Task.execute_sync':Task.execute_sync,'LLM.__new__':LLM.__new__,'OpenAICompletion.call':OpenAICompletion.call,'Knowledge.query':Knowledge.query,'KnowledgeStorage.search':KnowledgeStorage.search,'StringKnowledgeSource.add':StringKnowledgeSource.add,'ChromaDBClient.search':ChromaDBClient.search,'run_for_each_async':run_for_each_async}
        manifest=json.loads((OLD/'evidence/source_manifest.json').read_text());index={x['path']:x for x in manifest['files']}
        self.methods=[];self.bycode={};self.calls=[];self.lock=threading.Lock()
        for name,method in items.items():
            code=method.__code__;f=Path(code.co_filename).resolve();rel=f.relative_to(OLD/'source').as_posix();h=sha(f.read_bytes());assert h==index[rel]['sha256']
            record={'entry':name,'code_filename':str(f),'code_firstlineno':code.co_firstlineno,'code_qualname':code.co_qualname,'source_sha256':h,'verified_git_blob':index[rel]['git_blob'],'bytecode_sha256':sha(code.co_code),'class_getfile_used':False}
            self.methods.append(record);self.bycode[code]=record
    def profile(self,frame,event,arg):
        if event=='call' and frame.f_code in self.bycode:
            item=self.bycode[frame.f_code];obj=frame.f_locals.get('self');uid=str(getattr(obj,'id','')) if obj is not None else None
            with self.lock:self.calls.append({'entry':item['entry'],'code_filename':frame.f_code.co_filename,'code_firstlineno':frame.f_code.co_firstlineno,'thread_id':threading.get_ident(),'native_object_uuid':uid,'monotonic_ns':time.perf_counter_ns()})
    def __enter__(self):
        self.old_sys=sys.getprofile();self.old_thread=threading.getprofile();sys.setprofile(self.profile);threading.setprofile(self.profile);return self
    def __exit__(self,*args):
        sys.setprofile(self.old_sys);threading.setprofile(self.old_thread)
    def receipt(self):
        counts={x['entry']:sum(e['entry']==x['entry'] for e in self.calls) for x in self.methods}
        return {'mechanism':'sys/threading profile matched actual method __code__ object; coroutine resume entries are code-entry events, not invocation counts','methods':self.methods,'execution_receipts':self.calls,'code_entry_counts':counts,'all_methods_executed':all(counts.values()),'prior_class_getfile_claim_valid':False,'official_init_modified':False}

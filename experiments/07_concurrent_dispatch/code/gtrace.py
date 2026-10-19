"""G-only vLLM state, scheduled-step, and executor-service observation.
RUNNING residency and actual step service are separate evidence. No kernel claims.
"""
import os,json,time,threading,hashlib,inspect
from pathlib import Path
_ENABLED=os.environ.get("G_TRACE_ENABLED")=="1"
_ROOT=Path(os.environ.get("G_ROOT","."))
_FD=None;_SEQ=0;_LOCK=threading.RLock();_STEPS={};_STEP_COUNTER=0
_FLAG_CHECK=0.;_FLAG_ON=True
def trace_active():
    global _FLAG_CHECK,_FLAG_ON
    if not _ENABLED:return False
    t=time.monotonic()
    if t-_FLAG_CHECK>.1:
        _FLAG_CHECK=t
        flag=os.environ.get("G_TRACE_FLAG")
        _FLAG_ON=Path(flag).read_text().strip()=="1" if flag else True
    return _FLAG_ON
def emit(kind,**fields):
    global _FD,_SEQ
    if not trace_active():return
    t0=time.monotonic_ns()
    with _LOCK:
        if _FD is None:
            folder=Path(os.environ["G_TRACE_DIR"]);folder.mkdir(parents=True,exist_ok=True)
            _FD=os.open(str(folder/f"events-{os.getpid()}.jsonl"),os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
        _SEQ+=1
        row=dict(kind=kind,pid=os.getpid(),seq=_SEQ,t_ns=t0,clock="monotonic_ns_same_host",**fields)
        b=(json.dumps(row,separators=(",",":"))+"\n").encode()
        if os.write(_FD,b)!=len(b):raise RuntimeError("Incomplete G trace write")
def status_name(x):return getattr(x,"name",str(x)) if x is not None else None
def install_request(cls):
    if not _ENABLED or getattr(cls,"_g_observed",False):return
    original=cls.__setattr__
    def set_attr(self,name,value):
        old=getattr(self,name,None) if name=="status" else None
        original(self,name,value)
        if name=="status" and old!=value:
            emit("status_transition",request_id=getattr(self,"request_id",None),old=status_name(old),new=status_name(value))
    cls.__setattr__=set_attr;cls._g_observed=True
def snapshot(self):
    return {"running":[{"id":r.request_id,"status":status_name(r.status),"computed":r.num_computed_tokens,"prompt":r.num_prompt_tokens} for r in self.running],
            "waiting":[{"id":r.request_id,"status":status_name(r.status)} for r in self.waiting]}
def install_scheduler(cls):
    global _STEP_COUNTER
    if not _ENABLED or getattr(cls,"_g_observed",False):return
    init=cls.__init__;schedule=cls.schedule;update=cls.update_from_output
    add=cls.add_request;finish=cls.finish_requests
    def init_g(self,*a,**k):
        init(self,*a,**k);emit("scheduler_init",class_name=type(self).__name__,snapshot=snapshot(self))
    def schedule_g(self,*a,**k):
        global _STEP_COUNTER
        emit("schedule_before",snapshot=snapshot(self))
        start=time.monotonic_ns();out=schedule(self,*a,**k);end=time.monotonic_ns()
        _STEP_COUNTER+=1;sid=f"{os.getpid()}-{_STEP_COUNTER}";_STEPS[id(out)]=sid
        emit("scheduled_step",step_id=sid,scheduled_tokens=dict(out.num_scheduled_tokens),snapshot=snapshot(self),
             computed_token_snapshot_phase="after_scheduler_reservation",
             native_schedule_duration_ns=end-start)
        return out
    def update_g(self,out,*a,**k):
        sid=_STEPS.get(id(out))
        runner=a[0] if a else k.get("model_runner_output")
        emit("update_begin",step_id=sid,scheduled_tokens=dict(out.num_scheduled_tokens),snapshot=snapshot(self),output_request_ids=list(getattr(runner,"req_ids",[]) or []))
        result=update(self,out,*a,**k)
        emit("update_done",step_id=sid,snapshot=snapshot(self))
        _STEPS.pop(id(out),None)
        return result
    def add_g(self,request,*a,**k):
        emit("request_added",request_id=request.request_id,status=status_name(request.status))
        return add(self,request,*a,**k)
    def finish_g(self,*a,**k):
        emit("finish_before",snapshot=snapshot(self))
        out=finish(self,*a,**k);emit("finish_done",snapshot=snapshot(self));return out
    cls.__init__=init_g;cls.schedule=schedule_g;cls.update_from_output=update_g;cls.add_request=add_g;cls.finish_requests=finish_g;cls._g_observed=True
def install_core(cls):
    if not _ENABLED or getattr(cls,"_g_observed",False):return
    original=cls.__init__
    def init_g(self,*a,**k):
        original(self,*a,**k)
        executor=self.model_executor
        method=executor.execute_model
        def execute(out,*args,**kwargs):
            sid=_STEPS.get(id(out));scheduled=dict(out.num_scheduled_tokens)
            emit("executor_begin",step_id=sid,scheduled_tokens=scheduled)
            def done(result=None,error=None):
                emit("executor_end",step_id=sid,scheduled_tokens=scheduled,error=error,
                     result_type=type(result).__name__,output_request_ids=list(getattr(result,"req_ids",[]) or []))
            try:
                result=method(out,*args,**kwargs)
                if hasattr(result,"add_done_callback"):
                    def callback(f):
                        try:done(f.result())
                        except Exception as e:done(error=type(e).__name__)
                    result.add_done_callback(callback)
                else:done(result)
                return result
            except Exception as e:done(error=type(e).__name__);raise
        executor.execute_model=execute
        emit("engine_core_init",executor=type(executor).__name__,max_concurrent_batches=getattr(self,"max_concurrent_batches",None))
    cls.__init__=init_g;cls._g_observed=True
def install_cache(cls):
    if not _ENABLED or getattr(cls,"_g_observed",False):return
    original_init=cls.__init__
    def init_cache(self,*a,**k):
        original_init(self,*a,**k)
        from gchecks import kv_layout
        emit("cache_layout",**kv_layout(self.kv_cache_config))
    cls.__init__=init_cache
    original=cls.reset_prefix_cache
    def reset(self,*a,**k):
        result=original(self,*a,**k)
        pool=self.block_pool
        emit("cache_reset",success=bool(result),free_blocks=pool.get_num_free_blocks(),
             total_blocks=getattr(pool,"num_gpu_blocks",None))
        return result
    cls.reset_prefix_cache=reset;cls._g_observed=True

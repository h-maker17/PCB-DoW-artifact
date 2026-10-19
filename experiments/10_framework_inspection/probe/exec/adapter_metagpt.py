"""Rank5 selected official primitive compatibility prerequisite, run anew each time.
No old diagnostic result decides the outcome. Full RAG remains beyond first publish.
"""
import argparse, asyncio, builtins, datetime, hashlib, importlib, inspect, json, os
from pathlib import Path
import shutil, subprocess, sys, time, traceback
ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'preparation/metagpt_night/native_diagnostic'
class MetaGPTPreparationBlocked(RuntimeError):pass

def write(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf8')

def copy_native_source(destination):
    """Copy exact source bytes using Windows extended paths, without OS policy changes."""
    def copy_path(path):
        value=str(Path(path).resolve())
        return "\\\\?\\"+value if os.name=='nt' else value
    shutil.copytree(copy_path(BASE/'src'),copy_path(destination),
                   ignore=shutil.ignore_patterns('__pycache__','logs','workspace'))


def run(ctx,row):
    from native_bridge import verify_runtime
    verify_runtime('mg')
    state=ctx.journal_path.parent/('native_mg_'+ctx.phase);state.mkdir(parents=True,exist_ok=False)
    # Read only trusted source bytes and closed runtime; attempt writes stay private.
    copy_native_source(state/'source')
    for name in ['tmp','private_config','cache']: (state/name).mkdir()
    config={'state_dir':str(state.resolve()),'base_url':ctx.base_url,'phase':ctx.phase}
    write(state/'input.json',config)
    allow=['SystemRoot','WINDIR','COMSPEC','PATHEXT','PATH','USERPROFILE','HOMEDRIVE','HOMEPATH','PROCESSOR_ARCHITECTURE','NUMBER_OF_PROCESSORS']
    env={k:os.environ[k] for k in allow if k in os.environ}
    env.update(TEMP=str(state/'tmp'),TMP=str(state/'tmp'),PYTHONDONTWRITEBYTECODE='1',PYTHONUTF8='1',
      METAGPT_PROJECT_ROOT=str(state/'source'),SCAN_METAGPT_PRIVATE_CONFIG_ROOT=str(state/'private_config'),
      HF_HOME=str(state/'cache/hf'),XDG_CACHE_HOME=str(state/'cache'),TIKTOKEN_CACHE_DIR=str(state/'cache/tiktoken'),
      MPLCONFIGDIR=str(state/'cache/matplotlib'),JUPYTER_CONFIG_DIR=str(state/'cache/jupyter'),IPYTHONDIR=str(state/'cache/ipython'),
      HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',DO_NOT_TRACK='1')
    started=time.monotonic()
    with (state/'stdout.txt').open('w',encoding='utf8') as out,(state/'stderr.txt').open('w',encoding='utf8') as err:
        result=subprocess.run([str(BASE/'venv/Scripts/python.exe'),'-B',str(ROOT/'exec/adapter_metagpt.py'),
            '--native-input',str(state/'input.json')],cwd=state/'source',env=env,stdout=out,stderr=err)
    write(state/'process_timing.json',{'elapsed_seconds':time.monotonic()-started,'exit_code':result.returncode,
          'command_role':'fresh original official main subprocess','runtime_manifest_sha256':hashlib.sha256((ROOT/'evidence/runtime_manifests/mg.json').read_bytes()).hexdigest()})
    record=json.loads((state/'native_result.json').read_bytes())
    if record.get('unexpected_primitive_pass'):
        raise MetaGPTPreparationBlocked('Selected primitive first publish unexpectedly passed; full RAG preparation must be reviewed before proceeding')
    if result.returncode==0:
        raise MetaGPTPreparationBlocked('Native process exited0 without expected failure; this is not validated N4/RAG completion')
    error=record.get('exception')
    if not error:raise RuntimeError('Native subprocess failed without archived exception')
    cls=getattr(builtins,error['name'],RuntimeError)
    if not isinstance(cls,type) or not issubclass(cls,Exception):cls=RuntimeError
    exc=cls(error['message']);exc.add_note('Original native traceback archived at '+str(state/'native_result.json'))
    raise exc


def make_native_boundary(report,state):
    import socket
    def audit(event,args):
        if event=='open' and isinstance(args[0],(str,bytes,os.PathLike)):
            path=Path(os.fsdecode(args[0])).resolve();mode=args[1];flags=args[2]
            if path.as_posix().lower().rstrip('/') in ['//./nul','nul']:return
            writing=(isinstance(mode,str) and any(t in mode for t in 'wax+')) or (isinstance(flags,int) and bool(flags&(os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND)))
            if '/.metagpt/' in path.as_posix().lower() or (writing and not path.is_relative_to(state)):
                report['boundary_denials'].append({'event':event,'path':str(path),'writing':writing});raise PermissionError('Native private attempt boundary')
        elif event=='socket.connect':
            address=args[1]
            host=str(address[0]) if isinstance(address,tuple) else str(address)
            # Windows default asyncio creates a socketpair self-pipe on loopback.
            # Permit only that stdlib call chain; other loopback/network remains denied.
            frame=sys._getframe(1);codes=set()
            while frame is not None:
                codes.add(frame.f_code);frame=frame.f_back
            from asyncio.proactor_events import BaseProactorEventLoop
            from asyncio.selector_events import BaseSelectorEventLoop
            self_pipe=(socket.socketpair.__code__ in codes and
                any(cls._make_self_pipe.__code__ in codes for cls in [BaseProactorEventLoop,BaseSelectorEventLoop]))
            if host in ['127.0.0.1','localhost','::1'] and self_pipe:
                report.setdefault('allowed_asyncio_self_pipe',[]).append({'event':event,'host':host,'stdlib_call_chain_verified':True})
                return
            report['boundary_denials'].append({'event':event,'host':host})
            raise PermissionError('Unexpected network call before selected primitive prerequisite completion')
    return audit


def native(config):
    state=Path(config['state_dir']).resolve();source=state/'source'
    sys.dont_write_bytecode=True;sys.path.insert(0,str(source))
    (state/'private_config/config2.yaml').write_text('llm:\n  api_type: openai\n  base_url: '+config['base_url']+'\n  model: gpt-4o\n',encoding='utf8')
    report={'started_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
      'stage':'import_official_main','classification':None,'original_main_executed_directly':False,
      'RAG_main_executed':False,'ES_started':False,'N4_executed':False,'six_stages_executed':False,
      'models_invoked':0,'retrieval_calls':0,'boundary_denials':[],'observer_errors':[],
      'source_commit':'11cdf466d042aece04fc6cfd13b28e1a70341b1f',
      'configuration_changes':json.loads((ROOT/'evidence/rank5_integration/source_preparation.json').read_bytes())['patches'],
      'ambient_env_or_user_yaml_merge':False}
    sys.addaudithook(make_native_boundary(report,state))
    def observe(frame,event,arg):
        filename=frame.f_code.co_filename.replace('\\','/');name=frame.f_code.co_name
        if filename.endswith('/examples/build_customized_multi_agents.py') and name=='main' and event=='call':
            report['original_main_executed_directly']=True
            report['original_main_body_entered']=True
        if filename.endswith('/metagpt/team.py'):
            obj=frame.f_locals.get('self')
            if name=='run' and event=='call':
                report['unexpected_primitive_pass']=True
                raise MetaGPTPreparationBlocked('First publish passed; stop before Team.run pending RAG preparation')
            try:
                if name=='__init__' and event=='return' and obj is not None and getattr(obj,'env',None) is not None:
                    report['team']={'class':type(obj).__module__+'.'+type(obj).__qualname__,'use_mgx':obj.use_mgx,
                        'environment_class':type(obj.env).__module__+'.'+type(obj.env).__qualname__,
                        'mro':[c.__module__+'.'+c.__qualname__ for c in type(obj.env).__mro__]}
                if name=='hire' and event=='return' and obj is not None:
                    report['roles']=[{'name':r.name,'class':type(r).__module__+'.'+type(r).__qualname__,
                      'profile':r.profile,'actions':[type(a).__module__+'.'+type(a).__qualname__ for a in r.actions],
                      'watch':sorted(r.rc.watch),'is_human':r.is_human} for r in obj.env.roles.values()]
                if name=='run_project' and event=='call':report['stage']='original_run_project_first_publish'
            except Exception as exc:report['observer_errors'].append(repr(exc))
        elif filename.endswith('/metagpt/environment/mgx/mgx_env.py') and name=='publish_message' and event=='call':
            try:
                msg=frame.f_locals['message'];report.setdefault('messages',[]).append({
                    'class':type(msg).__module__+'.'+type(msg).__qualname__,'content':msg.content,
                    'role':msg.role,'send_to':sorted(msg.send_to),'sent_from':msg.sent_from})
            except Exception as exc:report['observer_errors'].append(repr(exc))
    started=time.monotonic()
    try:
        example=importlib.import_module('examples.build_customized_multi_agents')
        assert Path(example.__file__).resolve()==source/'examples/build_customized_multi_agents.py'
        report['official_defaults']={k:v.default for k,v in inspect.signature(example.main).parameters.items()}
        report['original_main_coroutine_created']=True;report['stage']='execute_official_main'
        sys.setprofile(observe)
        try:asyncio.run(example.main())
        finally:sys.setprofile(None)
        report['unexpected_primitive_pass']=True
        raise MetaGPTPreparationBlocked('Original main returned; full RAG/N4 preparation remains blocked')
    except BaseException as exc:
        report['exception']={'class':type(exc).__module__+'.'+type(exc).__qualname__,
            'name':type(exc).__name__,'message':str(exc),'traceback':traceback.format_exc()}
        raise
    finally:
        report['elapsed_seconds']=time.monotonic()-started
        report['loaded_modules']=[];report['loaded_file_hashes']={}
        for name,module in sorted(sys.modules.items()):
            file=getattr(module,'__file__',None)
            if not file or not Path(file).is_file():continue
            path=Path(file).resolve();report['loaded_modules'].append({'name':name,'file':str(path),'loader':type(getattr(module,'__loader__',None)).__name__})
            if str(path) not in report['loaded_file_hashes']:
                report['loaded_file_hashes'][str(path)]={'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'bytes':path.stat().st_size}
        write(state/'native_result.json',report)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--native-input',required=True)
    native(json.loads(Path(parser.parse_args().native_input).read_bytes()))

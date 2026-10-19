from pathlib import Path
import subprocess,os,json,traceback
r=Path('/path/to/workspace/多智能体系统实验数据/fcb-dow-research/experiments/2026-10-05-task-generalization');env=os.environ.copy();env['PYTHONPATH']=str(r/'code');py=str(r/'env/client/bin/python')
try:
 for module,args in [('test_metrics',[]),('probe',[]),('runner',['sanity'])]:
  subprocess.run([py,'-u','-m','tasklab.'+module]+args,env=env,check=True)
except Exception as e:
 (r/'evidence/preformal-failure.json').write_text(json.dumps({'error':str(e),'traceback':traceback.format_exc()}));raise

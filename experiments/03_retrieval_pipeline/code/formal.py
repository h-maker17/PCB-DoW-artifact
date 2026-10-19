import asyncio,traceback
from common import *
from pipeline import run_round
from native_boundary import run_native
async def main():
 cfg=read(R/'config/formal-protocol.json');freeze=read(R/'evidence/formal-freeze.json');assert cfg['formal_authorized'] and digest((R/'config/formal-protocol.json').read_bytes())==freeze['protocol_sha256']
 docs=read(R/'datasets/formal-queries.json');assert len(docs)==60
 completed=0
 for doc in docs:
  for N in [5,20]:
   for mode in cfg['formal_plan']['arms']:
    result=await (run_native(mode,doc,N) if mode.startswith('functiontool') else run_round(mode,doc,N,phase='formal'));completed+=1
    save(R/'evidence/formal-state.json',{'stage':'formal-running','completed_rounds':completed,'expected_rounds':960,'latest':{'task':doc['task'],'id':doc['id'],'N':N,'mode':mode,'warm_hit':result['warm_hit']},'updated_ns':now_ns()});print('SAVED',completed,doc['task'],N,mode,result['warm_hit'],flush=True)
 save(R/'evidence/formal-state.json',{'stage':'formal-model-requests-complete','completed_rounds':completed,'expected_rounds':960,'updated_ns':now_ns()})
if __name__=='__main__':
 try:asyncio.run(main())
 except Exception as e:save(R/'evidence/formal-failure.json',{'error':str(e),'traceback':traceback.format_exc(),'time_ns':now_ns()});raise

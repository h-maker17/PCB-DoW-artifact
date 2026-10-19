"""Thin loopback observer and isolated native-runtime launch support."""
import contextvars
import hashlib
import http.client
import json
import os
from pathlib import Path
import subprocess
import urllib.parse

ROOT=Path(__file__).resolve().parents[1]
REPLICA=contextvars.ContextVar('scan_native_replica',default=None)


def rpc(config,route,value,replica=None):
    url=urllib.parse.urlparse(config['base_url'])
    conn=http.client.HTTPConnection(url.hostname,url.port,timeout=120)
    identity=REPLICA.get() if replica is None else replica
    assert identity is not None or route=='embeddings', 'Native branch identity was not observed'
    headers={'Content-Type':'application/json','X-SCAN-Replica':str(identity if identity is not None else 0)}
    conn.request('POST',url.path+'/scan/'+route,json.dumps(value),headers)
    response=conn.getresponse();raw=response.read();conn.close()
    if response.status!=200:raise RuntimeError(f'Native observer {route}: {response.status}: {raw[:1000]!r}')
    return json.loads(raw)


def verify_runtime(directory):
    manifest=json.loads((ROOT/'evidence/runtime_manifests'/(directory+'.json')).read_bytes())
    def check(b):
        assert hashlib.sha256((ROOT/b['file']).read_bytes()).hexdigest()==b['sha256'],b['file']
    if directory=='df':
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=8) as pool:list(pool.map(check,manifest['files']))
    else:
        for b in manifest['files']:check(b)
    return manifest


def launch(ctx,row,runtime,directory,script,*,executable=None):
    verify_runtime(directory)
    location=ctx.journal_path.parent/('native_'+directory+'_'+ctx.phase)
    location.mkdir(parents=True,exist_ok=False)
    from pipeline import CORPUS,PROTOCOL
    value={'base_url':ctx.base_url,'query':ctx.query,'history':ctx.history,'N':PROTOCOL['N'],
           'documents':CORPUS['documents'],'state_dir':str(location.resolve()),'framework':row,
           'run_id':ctx.journal_path.parent.parent.name+'-'+ctx.journal_path.parent.name,'phase':ctx.phase}
    inputs=location/'input.json';inputs.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    executable=executable or ROOT/runtime/'Scripts/python.exe'
    command=[str(executable),str(ROOT/'exec'/script),'--native-input',str(inputs)]
    with (location/'runtime.log').open('w',encoding='utf-8') as output:
        process=subprocess.Popen(command,cwd=str(ROOT),stdout=output,stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        result=process.wait()
    if result:
        tail=(location/'runtime.log').read_text(encoding='utf-8',errors='replace')[-7000:]
        raise RuntimeError(f'Native {directory} runtime failed ({result}):\n{tail}')
    complete=location/'native_complete.json'
    if not complete.exists():raise RuntimeError('Native runtime ended without completion receipt')
    receipt=json.loads(complete.read_bytes())
    assert receipt['native_recipe_completed'] is True

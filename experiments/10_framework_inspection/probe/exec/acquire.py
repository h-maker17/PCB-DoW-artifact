"""Read-only public-source acquisition. Never executes downloaded source."""
import argparse, concurrent.futures, datetime, hashlib, json, pathlib, urllib.request, urllib.parse, sys
sys.stdout.reconfigure(encoding='utf-8')

ROOT = pathlib.Path(__file__).resolve().parents[1]
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({'https':'http://127.0.0.1:10809','http':'http://127.0.0.1:10809'}))

def fetch(url, target):
    target = ROOT / target
    target.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
    req = urllib.request.Request(url, headers={'User-Agent':'SCAN-academic-reproducibility/7.3','Accept':'application/json'})
    try:
        with OPENER.open(req, timeout=45) as response:
            data = response.read()
            target.write_bytes(data)
            meta = {'url':url,'retrieved_utc':stamp,'status':response.status,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}
    except Exception as e:
        meta = {'url':url,'retrieved_utc':stamp,'error':str(e)}
    receipt=target.with_suffix(target.suffix+'.receipt.json')
    if receipt.exists():
        history=target.with_suffix(target.suffix+'.attempts.jsonl')
        with history.open('a',encoding='utf-8') as f:f.write(receipt.read_text(encoding='utf-8').replace('\n','')+'\n')
    receipt.write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf-8')
    return meta

def discover():
    cfg=json.loads((ROOT/'config/discovery_protocol.json').read_text(encoding='utf-8'))
    jobs=[('https://api.github.com/search/repositories?'+urllib.parse.urlencode({'q':q,'sort':'stars','order':'desc','per_page':100}),f'evidence/discovery/search_{i}.json') for i,q in enumerate(cfg['discovery_queries']) if not (ROOT/f'evidence/discovery/search_{i}.json').exists()]
    for j in jobs: print(json.dumps(fetch(*j)))
    pool={}
    for p in sorted((ROOT/'evidence/discovery').glob('search_*.json')):
        if p.name.endswith('receipt.json'):continue
        for row in json.loads(p.read_bytes()).get('items',[]):
            row['discovery_file']=p.name
            pool[row['full_name']]=row
    rows=sorted(pool.values(),key=lambda r:(-r['stargazers_count'],r['full_name']))
    (ROOT/'evidence/discovery/pool_raw.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
    print('DISCOVERED',len(rows))
    print('DISCOVERY_COMPLETE',all((ROOT/f'evidence/discovery/search_{i}.json').exists() for i in range(len(cfg['discovery_queries']))))
    for r in rows[:65]:print(r['full_name'],r['stargazers_count'],r.get('description'))

def templates():
    for model,key in [('meta-llama/Llama-3.3-70B-Instruct','llama33'),('Qwen/Qwen3-8B','qwen3')]:
        r=fetch('https://huggingface.co/api/models/'+model,f'evidence/templates/{key}/model_api.json');print(json.dumps(r))
        p=ROOT/f'evidence/templates/{key}/model_api.json'
        if not p.exists():continue
        cfg=json.loads(p.read_bytes()); sha=cfg.get('sha')
        if not sha:continue
        names=['tokenizer.json','tokenizer_config.json','config.json','special_tokens_map.json','chat_template.jinja']
        files={x['rfilename'] for x in cfg.get('siblings',[])}
        jobs=[(f'https://huggingface.co/{model}/resolve/{sha}/{n}',f'evidence/templates/{key}/{n}') for n in names if n in files]
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
            for out in ex.map(lambda j:fetch(*j),jobs):print(json.dumps(out))

def embeddings():
    model='sentence-transformers/all-MiniLM-L6-v2'
    print(json.dumps(fetch('https://huggingface.co/api/models/'+model,'evidence/embeddings/model_api.json')))
    cfg=json.loads((ROOT/'evidence/embeddings/model_api.json').read_bytes())
    sha=cfg['sha']
    files=[x['rfilename'] for x in cfg['siblings'] if (x['rfilename'] in ['modules.json','config.json','sentence_bert_config.json','config_sentence_transformers.json','model.safetensors','tokenizer.json','tokenizer_config.json','special_tokens_map.json','vocab.txt','1_Pooling/config.json'])]
    jobs=[(f'https://huggingface.co/{model}/resolve/{sha}/{n}',f'evidence/embeddings/{n}') for n in files]
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
        for out in ex.map(lambda j:fetch(*j),jobs):print(json.dumps(out))

def inventory():
    import csv
    rows=json.loads((ROOT/'evidence/discovery/pool_raw.json').read_bytes())
    with (ROOT/'tables/discovery_pool.csv').open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=['discovery_rank','repo','stars','language','homepage','description','eligibility_status'])
        w.writeheader()
        for i,r in enumerate(rows,1):w.writerow({'discovery_rank':i,'repo':r['full_name'],'stars':r['stargazers_count'],'language':r['language'],'homepage':r['homepage'],'description':r['description'],'eligibility_status':'unverified; not a selected sample'})
    # Factual source retrieval only. This list has no experimental outcomes and does not constitute a selected sample.
    repos=['Significant-Gravitas/AutoGPT','langflow-ai/langflow','open-webui/open-webui','langchain-ai/langchain','bytedance/deer-flow','FoundationAgents/MetaGPT','mem0ai/mem0','microsoft/autogen','crewAIInc/crewAI','run-llama/llama_index','HKUDS/nanobot','vectorize-io/hindsight','langchain-ai/langgraph','agno-agi/agno','HKUDS/LightRAG','microsoft/graphrag','agentscope-ai/agentscope','openai/openai-agents-python','deepset-ai/haystack','pydantic/pydantic-ai']
    index={r['full_name']:r for r in rows}
    for repo in repos:
        if repo not in index:continue
        key=repo.replace('/','__')
        branch=index[repo]['default_branch']
        target=f'evidence/official/{key}/commit.json'
        if not (ROOT/target).exists():print(json.dumps(fetch(f'https://api.github.com/repos/{repo}/commits/{branch}',target)))
        if not (ROOT/target).exists():continue
        cfg=json.loads((ROOT/target).read_bytes())
        sha=cfg['sha']
        jobs=[(f'https://raw.githubusercontent.com/{repo}/{sha}/README.md',f'evidence/official/{key}/README.md'),(f'https://api.github.com/repos/{repo}/git/trees/{sha}?recursive=1',f'evidence/official/{key}/tree.json')]
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
            for out in ex.map(lambda j:fetch(*j),[j for j in jobs if not (ROOT/j[1]).exists()]):print(json.dumps(out))
        tree=ROOT/f'evidence/official/{key}/tree.json'
        if tree.exists():
            cfg=json.loads(tree.read_bytes());paths=[r['path'] for r in cfg.get('tree',[]) if r['type']=='blob' and any(x in r['path'].lower() for x in ['rag','retriev']) and any(x in r['path'].lower() for x in ['example','docs','cookbook','sample'])]
            (ROOT/f'evidence/official/{key}/rag_paths.json').write_text(json.dumps({'repo':repo,'commit':sha,'tree_truncated':cfg.get('truncated'),'paths':paths},indent=2),encoding='utf-8')
            print(repo,'RAG_PATHS',len(paths),paths[:12])

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('mode',choices=['discover','templates','embeddings','inventory']);args=parser.parse_args()
    globals()[args.mode]()

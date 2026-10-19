"""Mandatory live Llama smoke, exact P=2000 calibration and rendered-order probe."""
import argparse
import asyncio
import sys
import uuid
from pathlib import Path
import httpx

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'runtime'))
from experiment_common import ROOT,MODEL,URL,read,save,args_for,validate,now
from model_settings import PROFILE,request_settings
from shared_prefix_lab import ASSET,SYSTEM,calibrate_shared_document,run as run_b,lcp,marker
from e5_tool_chain import run as run_e5


def ids(dest,i):
    return read(dest/f'{i:04d}-worker_{i}.response.json')['prompt_token_ids']


def order_check(dest,kind):
    document=(dest/'shared_doc.used.txt').read_text(encoding='utf-8')
    calls=read(dest/'tool-calls.json',[])
    result=[]
    for i in (1,2):
        stem=f'{i:04d}-worker_{i}'
        req=read(dest/(stem+'.request.json'))
        res=read(dest/(stem+'.response.json'))
        rendered=(dest/(stem+'.prompt.txt')).read_text(encoding='utf-8')
        tag=marker(i) if kind=='front' else calls[i-1]['tool_return']
        tag_at=rendered.find(tag)
        doc_at=rendered.find(document[:80])
        system_at=rendered.find(SYSTEM)
        tools=[t['function']['name'] for t in req['tools']]
        tool_positions={name:rendered.find('"'+name+'"') for name in tools}
        positions_ok=(0<=tag_at<doc_at) if kind!='late' else (0<=doc_at<tag_at)
        header_ok=0<=system_at<doc_at and all(0<=p<doc_at for p in tool_positions.values())
        if kind!='late':
            header_ok=header_ok and system_at<tag_at and all(p<tag_at for p in tool_positions.values())
        result.append(dict(worker=i,marker_char_position=tag_at,document_char_position=doc_at,
            system_char_position=system_at,tool_schema_positions=tool_positions,
            marker_document_order_valid=positions_ok,system_tools_form_prefix_header=header_ok,
            actual_ids_match_tokenize=res['prompt_token_ids']==read(dest/(stem+'.tokenize.json'))['tokens']))
    return result


async def probe(session):
    session=Path(session).resolve()
    out=session/'probe'
    if read(out/'calibration.json',{}).get('assembly_verified'):
        print('PROBE_ALREADY_VERIFIED',out,flush=True)
        return
    async with httpx.AsyncClient(base_url=URL,timeout=300) as client:
        request=dict(model=MODEL,messages=[dict(role='user',content='Reply briefly: ready.')],
            temperature=0,seed=42,max_tokens=128,stream=False,return_token_ids=True,cache_salt=uuid.uuid4().hex,**request_settings())
        save(out/'smoke.request.json',request)
        response=await client.post('/v1/chat/completions',json=request)
        save(out/'smoke.response.json',response.json())
        response.raise_for_status()
        body=response.json()
        if not body['choices'][0]['message'].get('content') or body['usage']['completion_tokens']<=0:
            raise RuntimeError('Smoke did not generate tokens')
        if body['usage'].get('prompt_tokens_details',{}).get('cached_tokens')!=0:
            raise RuntimeError('Cold cached_tokens=0 was not reported by server')
    document,p=await calibrate_shared_document(ASSET,2000,URL,MODEL)
    if p!=2000:
        raise RuntimeError(f'Exact model P=2000 calibration failed: {p}')
    (out/'shared_doc.calibrated.txt').write_text(document,encoding='utf-8')
    runs={}
    for label,kind,variant in (('front','part_b','prefix'),('none','part_b','none'),
                               ('tool-front','e5','nonce-tool'),('late','e5','nonce-late')):
        a=args_for(out,2,kind,variant)
        dest=await (run_e5(a) if kind=='e5' else run_b(a))
        validate(dest,2,kind)
        runs[label]=dest
        save(out/'probe-index.json',{k:str(v.relative_to(session)) for k,v in runs.items()})
    checks={label:order_check(runs[label], 'front' if label=='front' else
                             'late' if label=='late' else 'tool-front')
            for label in ('front','tool-front','late')}
    input_reasoning=[]
    for label,dest in runs.items():
        for txt in dest.glob('*.prompt.txt'):
            rendered=txt.read_text(encoding='utf-8')
            if '<think>' in rendered or '<|channel|>analysis' in rendered or '<|meta_sep|>analysis' in rendered:
                input_reasoning.append(str(txt))
    save(out/'reasoning-input-audit.json',dict(files_with_analysis_content=input_reasoning,request_policy=PROFILE['thinking_policy']))
    if input_reasoning: raise RuntimeError('Reasoning channel found in input prefix; stop without rewriting prompts')
    assembled=all(c['marker_document_order_valid'] and c['system_tools_form_prefix_header']
                  and c['actual_ids_match_tokenize'] for entries in checks.values() for c in entries)
    h=lcp(ids(runs['front'],1),ids(runs['front'],2))
    prompt=len(ids(runs['none'],2))
    async with httpx.AsyncClient(base_url=URL,timeout=120) as client:
        response=await client.post('/detokenize',json=dict(model=MODEL,tokens=ids(runs['front'],1)[:h]))
        response.raise_for_status()
        save(out/'H.detokenize.json',response.json())
    save(out/'assembly-check.json',checks)
    save(out/'calibration.json',dict(model=MODEL,P_shared_document_tokens=p,
        calibration='incremental /tokenize count with native model template, no tools',
        H_actual_pre_marker_LCP_tokens=h,P_plus_s_full_prompt_tokens=prompt,
        predicted_floor=h/prompt,block_size=16,block_rounded_floor=(h//16*16)/prompt,
        template='native model template; unmodified; positions individually verified',
        template_kwargs=PROFILE['template_kwargs'],thinking_policy=PROFILE['thinking_policy'],
        smoke_reasoning=body['choices'][0]['message'].get('reasoning_content') or body['choices'][0]['message'].get('reasoning'),
        assembly_verified=assembled,probe_run_ids={k:str(v.relative_to(session)) for k,v in runs.items()},
        verified_utc=now()))
    if not assembled:
        raise RuntimeError('NO-GO: actual model marker/document/tools assembly differs; inspect probe, do not alter injection')
    print('PROBE_VERIFIED',out,'P=',p,'H=',h,'P+s=',prompt,'floor=',h/prompt,flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--session',type=Path,required=True)
    asyncio.run(probe(p.parse_args().session))

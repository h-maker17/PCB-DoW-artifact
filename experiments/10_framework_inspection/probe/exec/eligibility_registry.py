"""Build the complete factual audit ledger without fabricating eligibility.

Only explicit collection/course self-descriptions cause a scope exclusion.
Unknown repositories remain pending. Source links/AST are provenance leads,
not execution results, human framework labels or a selected sample.
"""
import csv
import hashlib
import json
import posixpath
import re
import sys
import urllib.parse
from collections import defaultdict, Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.stdout.reconfigure(encoding='utf-8')


def read(path):
    return json.loads((ROOT/path).read_bytes())


def write(path, value):
    (ROOT/path).write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n',encoding='utf-8')


def resolve_source_link(source, target):
    parsed=urllib.parse.urlparse(target)
    path=urllib.parse.unquote(parsed.path)
    if parsed.netloc in ['github.com','raw.githubusercontent.com']:
        bits=path.strip('/').split('/')
        if len(bits)>=5 and bits[2] in ['blob','tree']:
            return '/'.join(bits[:2]),'/'.join(bits[4:])
        if parsed.netloc=='raw.githubusercontent.com' and len(bits)>=4:
            return '/'.join(bits[:2]),'/'.join(bits[3:])
        return None
    if parsed.netloc or not path or path.startswith('/'):
        # Hosted documentation slugs require their own audited mapping.
        return None
    path=posixpath.normpath(posixpath.join(posixpath.dirname(source['path']),path))
    return source['repo'],path


def exclusion(description):
    # These match the project's direct self-description, not incidental mentions
    # of tutorials elsewhere. Preserve the exact matched source text for audit.
    patterns=[
        r'^(?:A |An |The )?(?:curated )?collection of (?:projects|resources|agent recipes|practical AI projects)\b',
        r'^(?:A )?curated list\b',
        r'^(?:A |The )?(?:tri\w+ )?learning roadmap\b',
        r'^(?:free, )?hands-on (?:workshop|AI courses)\b',
        r'^The LLM.s practical guide:',
        r'^Explore .*Proof of Concepts',
        r'全书正文|实战教程|实战一.*指南|智能体原理与实践教程',
        r'^100\+ AI Agents, Agent Skills and RAG Apps',
        r'𝗟𝗲𝗮𝗿𝗻 for 𝗳𝗿𝗲𝗲',
    ]
    for pattern in patterns:
        if re.search(pattern,description or '',re.I):
            return {'status':'excluded_scope','reason':'explicit collection/course/book/tutorial self-description',
                    'rule':pattern,'quote':description}
    component_patterns=[
        (r'^Turn any PDF or image document into structured data .* OCR toolkit', 'OCR/document extraction toolkit'),
        (r'^Open-source web crawler and scraper for LLMs and AI agents:', 'crawler/scraper component'),
        (r'^Compress tool outputs, logs, files, and RAG chunks before they reach the LLM\.', 'context compression component'),
        (r'^Open-source AI penetration testing tool to find and fix your app', 'task-specific penetration testing application'),
    ]
    for pattern,role in component_patterns:
        if re.search(pattern,description or '',re.I):
            return {'status':'excluded_scope','reason':'Explicit self-described role is outside Python agent/RAG framework scope: '+role,
                    'rule':pattern,'quote':description,'not_due_to_ui_or_expected_outcome':True}
    return {'status':'pending','reason':'framework scope and pinned official runnable-recipe pointer not yet jointly verified'}


def main():
    pool=read('evidence/discovery/pool_raw.json')
    sources=read('evidence/universe_sources/merged_source_index.json')['sources']
    facts=read('evidence/universe_sources/structure_facts.json')
    source_lookup={(s['repo'],s['path']):s for s in sources if s['status']=='archived'}
    facts_by_file=defaultdict(list)
    for f in facts['snippets']:facts_by_file[f['file']].append(f)
    by_repo=defaultdict(list);links=[];recipe_leads=defaultdict(list);inline_recipes=defaultdict(list)
    for s in sources:
        by_repo[s['repo']].append(s)
        if s['status']!='archived':continue
        file_facts=facts_by_file[s['file']]
        retrieval=[c for f in file_facts for c in f.get('retrieval_related_calls',[])]
        generation=[c for f in file_facts for c in f.get('generation_related_calls',[])]
        for f in file_facts:
            functions=f.get('defined_functions',[])
            if re.search(r'rag|retriev|chat_with_file|knowledge', ' '.join(functions),re.I):
                inline_recipes[s['repo']].append({'source':s,'snippet':f['snippet'],'line_or_cell':f['line_or_cell'],
                        'functions':functions,'http_endpoint_literals':f.get('http_endpoint_literals',[]),
                        'not_verified_runnable':True})
        if (retrieval and generation) or any(f.get('flow_node_types') for f in file_facts):
            recipe_leads[s['repo']].append({**s,'retrieval_calls':retrieval,'generation_calls':generation,
                    'placeholder_search_functions':[x for f in file_facts for x in f.get('placeholder_search_functions',[])],
                    'flow_node_types':[t for f in file_facts for t in f.get('flow_node_types',[])],
                    'qualification':'structural lead only; syntax/imports/defaults and official-doc pointer require verification'})
        if Path(s['path']).suffix.lower() not in ['.md','.mdx','.rst']:continue
        text=(ROOT/s['file']).read_text(encoding='utf-8',errors='replace')
        matches=list(re.finditer(r'\[([^\]\n]+)\]\(([^\s)]+)(?:\s+[^)]*)?\)',text))
        matches+=list(re.finditer(r'<a\s[^>]*href=["\']([^"\']+)["\'][^>]*>([^<]*)',text,re.I))
        for match in matches:
            if match[0].startswith('<a'):target,label=match[1],match[2]
            else:label,target=match[1],match[2]
            resolved=resolve_source_link(s,target)
            linked=source_lookup.get(resolved) if resolved else None
            if linked and Path(linked['path']).suffix in ['.py','.ipynb','.json','.yaml','.yml']:
                links.append({'document_repo':s['repo'],'document_file':s['file'],'document_url':s['url'],
                              'document_commit':s['commit'],'line':text[:match.start()].count('\n')+1,
                              'label':label,'original_target':target,'linked_source':linked,
                              'same_repository':s['repo']==linked['repo'],
                              'not_verified_runnable':True})
    readmes={r['repo']:r for r in read('evidence/universe_sources/readme_index.json')['repositories']}
    dossiers={r['repo']:r for r in read('config/qualification_facts.json')['dossiers']} if (ROOT/'config/qualification_facts.json').exists() else {}
    dossier_files={repo:'config/qualification_facts.json' for repo in dossiers}
    for config in ['config/qualification_additional.json', 'config/qualification_scope_facts.json', 'config/qualification_positive_extra.json', 'config/qualification_scope_extra.json', 'config/qualification_small_recipe.json', 'config/qualification_external_positive.json', 'config/qualification_verified_batch.json']:
        if (ROOT/config).exists():
            for dossier in read(config)['dossiers']:
                assert dossier['repo'] not in dossiers, dossier['repo']
                dossiers[dossier['repo']] = dossier
                dossier_files[dossier['repo']] = config
    unknown_dossiers = set(dossiers) - {p['full_name'] for p in pool}
    assert not unknown_dossiers, ('Dossier repository identity not in frozen discovery pool', sorted(unknown_dossiers))
    rows=[]
    for rank,p in enumerate(pool,1):
        repo=p['full_name'];meta=read('evidence/universe_sources/'+repo.replace('/','__')+'/metadata.json')
        decision=exclusion(p.get('description'))
        dossier=dossiers.get(repo)
        if dossier:
            assert dossier['framework_commit']==meta['commit']
            for source in dossier['evidence']:
                assert hashlib.sha256((ROOT/source['file']).read_bytes()).hexdigest()==source['sha256'],source['file']
            decision={'status':dossier['status'],'reason':dossier.get('decision_reason','Pinned official runnable RAG documentation/source dossier verified'),
                      'dossier_file':dossier_files[repo],'dossier_repo':repo}
        refs=[link for link in links if link['document_repo']==repo or link['linked_source']['repo']==repo]
        row={'discovery_rank':rank,'repo':repo,'stars_snapshot':p['stargazers_count'],'commit':meta['commit'],
             'decision':decision,'description':p.get('description'),
             'self_description_source':{'file':'evidence/discovery/pool_raw.json','query_snapshot':p['discovery_file'],'json_pointer':f'/{rank-1}/description'},
             'pinned_readmes':readmes[repo]['sources'],'tree_complete':not meta.get('tree_truncated',True),
             'archived_source_leads':sum(s['status']=='archived' for s in by_repo[repo]),
             'unavailable_source_leads':sum(s['status']!='archived' for s in by_repo[repo]),
             'structural_recipe_leads':recipe_leads[repo],
             'inline_recipe_leads':inline_recipes[repo],
             'document_to_source_links':refs,'selected':False,'framework_execution':False,
             'qualification_dossier':dossier,
             'missing_evidence':[] if decision['status']!='pending' else ['verify self-framework scope','verify official document pointer and full runnable RAG dependency/default path']}
        rows.append(row)
    counts=dict(Counter(r['decision']['status'] for r in rows))
    write('evidence/universe_sources/document_recipe_links.json',{'links':links,'not_recipe_selection':True})
    doc_repos=sorted(set(by_repo)-{p['full_name'] for p in pool})
    write('evidence/universe_sources/independent_document_repositories.json',{'not_automatic_framework_attribution':True,
         'repositories':[{'repo':repo,'sources':by_repo[repo],'structural_recipe_leads':recipe_leads[repo],
                          'inline_recipe_leads':inline_recipes[repo]} for repo in doc_repos]})
    write('evidence/universe_sources/candidate_registry.json',{'frozen':False,'eligibility_complete':counts.get('pending',0)==0,'outcome_labels':0,
          'acquisition_complete':facts['acquisition_complete'],'counts':counts,'repositories':rows})
    fields=['discovery_rank','repo','stars_snapshot','commit','eligibility_status','reason','scope_quote','tree_complete',
            'archived_source_leads','unavailable_source_leads','structural_recipe_leads','document_to_source_links','missing_evidence']
    with (ROOT/'tables/candidate_eligibility.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        w=csv.DictWriter(stream,fieldnames=fields);w.writeheader()
        for r in rows:
            w.writerow({**{k:r.get(k,'') for k in fields[:4]+fields[7:10]},
                        'eligibility_status':r['decision']['status'],'reason':r['decision']['reason'],
                        'scope_quote':r['decision'].get('quote',''),
                        'structural_recipe_leads':len(r['structural_recipe_leads']),
                        'document_to_source_links':len(r['document_to_source_links']),
                        'missing_evidence':'; '.join(r['missing_evidence'])})
    print('CANDIDATE_FACT_REGISTRY',len(rows),counts,'doc-to-source pointers',len(links),'no sample selected')


if __name__=='__main__':main()

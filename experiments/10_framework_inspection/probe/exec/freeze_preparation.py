"""Freeze complete stars pool and audited pilot recipe facts without running it."""
import ast
import csv
import datetime
import hashlib
import json
import math
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def read(file):return json.loads((ROOT/file).read_bytes())
def sha(file):return hashlib.sha256((ROOT/file).read_bytes()).hexdigest()
def reference(file,required=()):
    text=(ROOT/file).read_text(encoding='utf-8')
    assert all(literal in text for literal in required),(file,required)
    return {'file':file,'sha256':sha(file),'required_literals':list(required)}


def pilot_recipes():
    lf='evidence/official/langflow-ai__langflow/'
    ow='evidence/official/open-webui__docs/sources/'
    definitions={
        'langflow-ai/langflow':{
            'adapter_module':'adapter_langflow',
            'step':'step-2',
            'evidence':[
                reference(lf+'document_corpus/docs/docs/Tutorials/chat-with-rag.mdx',['select the **Vector Store RAG** template']),
                reference(lf+'sources/src/backend/base/langflow/initial_setup/starter_projects/Vector Store RAG.json',['Knowledge','Prompt','Agent']),
                reference(lf+'document_corpus/docs/docs/Components/bundles-files-ingestion.mdx',['### Merge Flows','synchronize parallel setup pipelines']),
                reference(lf+'sources/src/lfx/src/lfx/components/files_ingestion/merge_flows.py',['class MergeFlowsComponent','All will run when this component executes.']),
                reference('evidence/runtime/lf/src/lfx/src/lfx/graph/graph/base.py',['aget_default_coordinator','coordinator.run_to_completion']),
                reference('evidence/runtime_manifests/lf.json'),
                reference('exec/adapter_langflow.py'),reference('reports/schema_preflight_lf.json',['"model_invoked": false']),
                reference(lf+'document_corpus_index.json'),
            ],
            'body':'Pinned current shipped Vector Store RAG JSON, selected by official tutorial',
            'parallel_primitive':'Official Merge Flows component; native Graph.arun/default coordinator builds its upstream branches',
            'step1_review':'Current tutorial-selected shipped Vector Store RAG has Knowledge -> parser -> Prompt -> Agent and no instantiated parallel/ensemble branch primitive. Archived current official documentation and shipped-flow inventory were reviewed; implementation-level asynchronous scheduling alone is not a same-file official redundant RAG example. Step-2 is explicitly an author minimal composition.',
            'default_contract':{'top_k':5,'retrieval_mode':'Retrieve','agent_max_iterations':15,'agent_stream':True,
                'prompt':'Exact shipped context/question template and Agent system instructions retained',
                'parser':'Original parser pattern/separator and source formatting retained',
                'tools':'Original calculator/date tools retained; grounded canned answer may complete without calling them',
                'model':'Shipped Agent model is blank (requires connection configuration); harness connects gpt-4o native provider to local stub, not an asserted author default model',
                'history':'Original database loading with branch-scoped context_id; declared shared sentinel history only',
                'corpus':'Declared controlled local corpus; actual native Chroma search and original top_k',
                'embedding':'Declared CPU MiniLM transport, no hand replacement of search results'},
        },
        'open-webui/open-webui':{
            'adapter_module':'adapter_openwebui','step':'step-2',
            'evidence':[
                reference(ow+'docs/reference/api-endpoints.md',['chat_with_file']),
                reference(ow+'docs/features/chat-conversations/rag/index.md'),
                reference(ow+'docs/features/chat-conversations/chat-features/multi-model-chats.mdx',['multiple models simultaneously','Select Models']),
                reference('evidence/runtime/ow/backend/open_webui/main.py',['for idx, entry in enumerate(message_ids):','await create_task(']),
                reference('evidence/runtime/ow/backend/open_webui/retrieval/utils.py',['async def query_collection']),
                reference('evidence/runtime_manifests/ow.json'),reference('exec/adapter_openwebui.py'),
                reference('reports/schema_preflight_ow.json',['"model_invoked": false']),
                reference('reports/runtime_resource_restoration.json'),
            ],
            'body':'Official complete upload/process plus chat_with_file API example',
            'parallel_primitive':'Official Multi-Model Chats; original main.chat_completion creates native process_chat tasks for message_ids',
            'step1_review':'Official file-RAG API example is one model and does not instantiate multi-model fan-out. Separate official Multi-Model Chats documents fan-out without a file-RAG body. Backend retrieval-utils gather/EnsembleRetriever and middleware asynchronous calls are implementation details, not documentation-linked same-file redundant generation examples. Step-2 uses file-RAG as the body of native multi-model chat.',
            'default_contract':{'top_k':3,'hybrid_search':False,'full_context':False,'RAG_SYSTEM_CONTEXT':False,
                'embedding_model':'sentence-transformers/all-MiniLM-L6-v2',
                'splitter':'Original upload processing/chunk_size/chunk_overlap; asserted and logged before ingestion',
                'prompt':'Original RAG template, context formatting and prompt placement; no injected prompt rewrites',
                'history':'Declared shared sentinel history; native chat/message record format and history assembly retained',
                'retrieval_query':'Original conditional query-generation task setting retained',
                'models':'N same-provider controlled replicas via native message_ids API; model connections share logging stub; source multi-model guide examples are illustrative choices, not fixed defaults',
                'corpus':'Declared single text upload; fixed ingest file UUID prevents incidental corpus identity drift',
                'completion':'Native task handles and stored done/error states required; dispatch success is insufficient'},
        },
    }
    for repo,spec in definitions.items():
        ast.parse((ROOT/'exec'/f"{spec['adapter_module']}.py").read_text(encoding='utf-8'))
        spec.update(frozen=True,qualification_is_not_runtime_success=True,
            wording='official RAG example used as the per-branch body of its official parallel primitive',
            adaptations=['local logging stub model connection','declared local corpus and CPU embedding connection',
                'shared sentinel history','structural branch identities outside semantic prompt',
                'native retrieval-return observer and per-invocation marker; never assembled-prompt edit'],
            forbidden_repairs=['no prompt/default retuning','no forced retrieval/tools','no native cache disabling',
                'no author-written branch scheduler','no failure recovery or missing tool loop repair'],
            step1_review_evidence_frozen=True)
    return definitions


def robustness(rows, audit):
    by_repo={r['repo']:r for r in audit['mappings']}
    ledger=[]
    for rank,row in enumerate(rows,1):
        item=by_repo[row['repo']]
        primary=item['proposed_primary_package']
        package=next((p for p in item['packages'] if p['package']==primary),None)
        owned=bool(package and package['ownership_verified'])
        complete=bool(owned and package['window_complete'])
        ledger.append({'repo':row['repo'],'stars_rank':rank,'stars_snapshot':row['stars_snapshot'],'primary_package':primary,
            'ownership_verified':owned,'window_complete':complete,
            'downloads_90d':package['downloads_90d'] if complete else None,
            'missing_reason':None if complete else ('no attributable primary package' if not owned else 'incomplete fixed90-day window'),
            'package_audit':package,'official_document_leads':item['official_document_leads']})
    known=sorted([r for r in ledger if r['downloads_90d'] is not None],key=lambda r:(-r['downloads_90d'],r['repo']))
    top=[r['repo'] for r in known[:6]]
    stars=[r['repo'] for r in rows[:6]]
    # Correlation is explicitly complete-case, never a same-full-pool estimate.
    n=len(known)
    def average_ranks(field):
        ordered=sorted(known,key=lambda r:r[field]);result={};i=0
        while i<n:
            end=i+1
            while end<n and ordered[end][field]==ordered[i][field]:end+=1
            for row in ordered[i:end]:result[row['repo']]=(i+1+end)/2
            i=end
        return result
    x=average_ranks('stars_snapshot');y=average_ranks('downloads_90d');center=(n+1)/2
    numerator=sum((x[r['repo']]-center)*(y[r['repo']]-center) for r in known)
    denominator=math.sqrt(sum((v-center)**2 for v in x.values())*sum((v-center)**2 for v in y.values()))
    rho=numerator/denominator if denominator else None
    result={'frozen':True,'used_for_selection':False,'eligible_repositories':[r['repo'] for r in rows],
        'window_start':audit['window_start'],'window_end':audit['window_end'],'window_days':90,
        'package_policy':'one documented primary native distribution per independent repository; no companion aggregation',
        'coverage':{'eligible':len(rows),'owned_complete_window':len(known),'unavailable':len(rows)-len(known)},
        'missing_values_are_not_zero':True,'full_pool_download_top6':None,'full_pool_overlap':None,'full_pool_spearman':None,
        'full_pool_comparison_available':len(known)==len(rows),
        'complete_case_top6':top,'complete_case_overlap':len(set(stars)&set(top)),
        'complete_case_spearman':rho,'complete_case_count':n,
        'correlation_method':'Spearman Pearson correlation of average ranks of actual stars/download counts; ties averaged; complete cases only',
        'limitation':'Incomplete ownership/statistics prevent a full-pool download top6 or full-pool correlation. Available-case results are descriptive only; unknown candidates can change this ranking. Stars selection is unchanged.',
        'ledger':ledger}
    if len(known)==len(rows):
        result.update(full_pool_download_top6=top,full_pool_overlap=result['complete_case_overlap'],full_pool_spearman=rho)
    return result


def main():
    facts=read('evidence/universe_sources/candidate_registry.json');protocol=read('config/discovery_protocol.json')
    assert facts['eligibility_complete'] and not any(r['decision']['status']=='pending' for r in facts['repositories'])
    rows=sorted([r for r in facts['repositories'] if r['decision']['status']=='qualified'],key=lambda r:(-r['stars_snapshot'],r['repo']))
    audit=read('reports/pypi_mapping_audit_draft.json');assert not audit['qualified_without_draft_mapping']
    report=robustness(rows,audit);recipes=pilot_recipes()
    selected=[]
    for rank,row in enumerate(rows[:protocol['top_K']],1):
        item={'rank':rank,'repo':row['repo'],'stars_snapshot':row['stars_snapshot'],
            'framework_commit':read('evidence/universe_sources/'+row['repo'].replace('/','__')+'/metadata.json')['commit'],
            'ecosystem':row['repo'].split('/')[0],'qualification':row['decision']}
        if row['repo'] in recipes:
            item['recipe']=recipes[row['repo']]
            item['adapter_module']=item['recipe']['adapter_module']
        else:
            item['recipe']={'frozen':False,'stage':'after pilot checkpoint; current preparation freezes pilot adapters only',
                            'qualification_dossier':row['decision'],'execution_authorized_in_current_phase':False}
        selected.append(item)
    frozen={'frozen':True,'eligibility_complete':True,'primary_metric':'GitHub stars',
        'ranking_date_D':protocol['date_D_local'],'frozen_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'source_registry':reference('evidence/universe_sources/candidate_registry.json'),
        'ranking_order':'descending archived stars_snapshot, canonical repo lexicographic tie-break',
        'ranked_eligible_repositories':[r['repo'] for r in rows], 'selected':selected,
        'extension_order':[r['repo'] for r in rows[6:10]],
        'top_K':6,'pilot_ranks':[1,2],'extension_max':4,'per_framework_timebox_hours':8,
        'dedup':protocol['dedup'],'robustness':report,'stage_boundary':'No framework execution in preparation; stop before ranks3-6 after later pilot'}
    from selection_gate import verify_frozen
    verify_frozen(frozen)
    for file,value in [('config/frameworks.json',frozen),('reports/pypi_robustness.json',report),('reports/pilot_recipe_preparation.json',recipes)]:
        (ROOT/file).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    with (ROOT/'tables/frozen_stars_ranking.csv').open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.writer(f);w.writerow(['rank','repo','stars_snapshot','ecosystem','stage'])
        for i,row in enumerate(rows,1):w.writerow([i,row['repo'],row['stars_snapshot'],row['repo'].split('/')[0],'pilot' if i<=2 else 'top6 later' if i<=6 else 'extension order' if i<=10 else 'qualified pool'])
    print('FROZEN_STARS_POOL',len(rows),'TOP6',[r['repo'] for r in selected])
    print('ROBUSTNESS_AVAILABLE_CASES',report['coverage'],'FULL_POOL_COMPARISON_AVAILABLE',report['full_pool_comparison_available'])


if __name__=='__main__':main()

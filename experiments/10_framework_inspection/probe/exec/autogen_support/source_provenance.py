"""Canonicalize only proven incidental AutoGen document source audit paths."""
from pathlib import Path
import hashlib,json,re
from urllib.parse import unquote,unquote_plus

def digest(data):return hashlib.sha256(data).hexdigest()

def string_leaves(value,path=''):
    if isinstance(value,str):yield path,value
    elif isinstance(value,list):
        for index,item in enumerate(value):yield from string_leaves(item,path+'/'+str(index))
    elif isinstance(value,dict):
        for key,item in value.items():
            if isinstance(key,str):yield path+'/key',key
            yield from string_leaves(item,path+'/'+str(key))

def unicode_inspection(text):
    # Consume the whole escaping backslash run. Decoding only its final slash
    # leaves a spurious slash between every Unicode-encoded character.
    decoded=re.sub(r'\\+u([0-9a-fA-F]{4})',lambda m:chr(int(m.group(1),16)),text)
    # Valid JSON decoding below handles surrogate pairs. Keep any invalid
    # surrogate as its explicit escape in this conservative fallback evidence.
    return decoded.encode('utf8',errors='backslashreplace').decode('utf8')

def guard_form(text):
    # A single inspection representation only; model/prompt bytes never change.
    return re.sub(r'/+','/',unquote_plus(unquote(unicode_inspection(text))).replace('\\','/').casefold()).replace('/?/','/')

def inspection_representations(text):
    pending=[(text,'original')];seen=set();forms=[];complete=True
    while pending:
        current,method=pending.pop(0)
        if current in seen:continue
        if len(seen)>=128:complete=False;break
        seen.add(current)
        forms.append({'text':current,'method':method,'guard_text':guard_form(current),
                      'sha256':digest(current.encode('utf8',errors='surrogatepass'))})
        # Inspect raw JSON and escaped string fragments independently, without
        # serializing an already escaped request into another escape layer.
        for document,label in [(current,'JSON document'),('"'+current+'"','JSON string fragment')]:
            try:decoded=json.loads(document)
            except (ValueError,TypeError):continue
            for path,leaf in string_leaves(decoded):
                safe=leaf.encode('utf8',errors='backslashreplace').decode('utf8')
                if safe!=current:pending.append((safe,label+path))
        for changed,label in [(unquote(current),'URI percent decode'),(unquote_plus(current),'URI plus decode'),(unicode_inspection(current),'conservative Unicode escape decode')]:
            if changed!=current:pending.append((changed,label))
    return forms,complete

def inspect_inventory(requests,prompts):
    scanned=[];complete=True
    for index,request in enumerate(requests or []):
        if not isinstance(request,dict):continue
        origins=[]
        if isinstance(request.get('raw_body_text'),str):origins.append(('raw HTTP',request['raw_body_text']))
        origins.extend(('parsed payload'+path,text) for path,text in string_leaves(request.get('body')))
        for origin,text in origins:
            forms,ok=inspection_representations(text);complete=complete and ok
            scanned.append({'origin':origin,'request_index':index,'replica':request.get('replica'),
                            'scan_arm':request.get('scan_arm'),'scan_round':request.get('scan_round'),
                            'original_string':text,'representations':forms})
    for index,text in enumerate(prompts or []):
        if not isinstance(text,str):continue
        forms,ok=inspection_representations(text);complete=complete and ok
        scanned.append({'origin':'rendered prompt','rendered_index':index,'original_string':text,'representations':forms})
    return scanned,complete

def source_bindings(out,phase,project_root,uuid_bindings,storage_evidence):
    state=out/('native_ag_'+phase);receipt=state/'native_complete.json'
    if not receipt.is_file():return {},{'missing_native_completion':True}
    complete=json.loads(receipt.read_bytes());private=Path(complete['private_state']).resolve()
    if not private.is_relative_to(state.resolve()):return {},{'outside_private_state':True}
    config=json.loads((state/'input.json').read_bytes());documents=config.get('documents')
    if not isinstance(documents,list) or not documents or not all(isinstance(x,str) for x in documents):return {},{'missing_document_inventory':True}
    result={};reports=[]
    counts={x['branch']:x['actual_rows'] for x in storage_evidence.get('databases',[])}
    for branch in range(complete['branch_count']):
        directory=(private/f'R{branch}').resolve();files=list(directory.glob('document_*.txt'))
        branch_bindings={i:b for i,b in uuid_bindings.items() if b.get('branch')==branch}
        if len(files)!=len(documents) or counts.get(branch)!=len(documents) or len(branch_bindings)!=len(documents):
            reports.append({'branch':branch,'protected_reason':'Actual file/Chroma/document inventory count differs; one-chunk scope only'});continue
        manifest=[];receipts=[];valid=True
        for ordinal,document in enumerate(documents):
            file=directory/f'document_{ordinal}.txt'
            if not file.is_file() or file.resolve().parent!=directory:valid=False;break
            data=file.read_bytes()
            if file.read_text(encoding='utf8')!=document or len(document)>1500:valid=False;break
            logical={'namespace':'frozen AutoGen SimpleDocumentIndexer input.documents','ordinal':ordinal,
                     'document_count':len(documents),'input_text_sha256':digest(document.encode('utf8')),'file_bytes_sha256':digest(data)}
            manifest.append(logical);receipts.append({'file':file.relative_to(project_root).as_posix(),'sha256':digest(data),'bytes':len(data),'logical_document':logical})
        if not valid:
            reports.append({'branch':branch,'protected_reason':'Actual file missing, transformed, outside private source directory or multi-chunk'});continue
        catalog_sha=digest(json.dumps(manifest,ensure_ascii=False,separators=(',',':')).encode('utf8'))
        relation_counts={}
        for b in branch_bindings.values():
            key=(b.get('document_ordinal'),b.get('raw_source'));relation_counts[key]=relation_counts.get(key,0)+1
        for identity,b in branch_bindings.items():
            ordinal=b.get('document_ordinal');source=b.get('raw_source')
            if type(ordinal)is not int or not 0<=ordinal<len(documents) or not isinstance(source,str):continue
            expected=directory/f'document_{ordinal}.txt'
            if Path(source).resolve()!=expected or b.get('document_sha256')!=receipts[ordinal]['sha256'] or b.get('text')!=documents[ordinal] or b.get('chunk_index')!=0 or relation_counts[(ordinal,source)]!=1:continue
            entry={'raw_source':source,'branch':branch,'collection_name':b['collection_name'],
                   'document_ordinal':ordinal,'document_count':len(documents),'ordered_document_catalog':manifest,
                   'catalog_sha256':catalog_sha,'file_receipt':receipts[ordinal],
                   'chunk_index':0,'text':b['text'],'mime_type':b['mime_type'],
                   'actual_SQLite_embedding_id':identity,'actual_Chroma_binding':b,
                   'owned_private_source_verified':True,'one_plain_chunk_verified':True}
            # Namespace + ordered full catalog + ordinal distinguish separate
            # logical documents even when filenames/bytes happen to be equal.
            entry['stable_source_key']=[manifest[ordinal]['namespace'],catalog_sha,branch,b['collection_name'],ordinal,receipts[ordinal]['sha256']]
            result[identity]=entry
        reports.append({'branch':branch,'ordered_document_catalog_sha256':catalog_sha,'files':receipts,'actual_Chroma_rows':counts[branch]})
    return result,{'native_completion_sha256':digest(receipt.read_bytes()),'branches':reports,'storage_evidence':storage_evidence}

def canonicalize_sources(raw,canonical,rendered,bindings,inventory=None):
    requests=raw.get('requests',[]) if inventory is None else inventory.get('requests')
    prompts=rendered if inventory is None else inventory.get('rendered')
    ready=(isinstance(requests,list) and bool(requests)
           and all(isinstance(x,dict) and isinstance(x.get('body'),dict) and isinstance(x.get('raw_body_text'),str) and x['raw_body_text'] for x in requests)
           and isinstance(prompts,list) and bool(prompts) and all(isinstance(x,str) and x for x in prompts))
    inspected,inspection_complete=inspect_inventory(requests,prompts)
    ready=ready and inspection_complete
    report={'policy':'AutoGen exact native_results[].metadata.source audit path only','normalized':[],'protected':[],
            'complete_all_replica_round_arm_template_inventory_available':ready,
            'request_inventory_sha256':digest(json.dumps(requests,ensure_ascii=False).encode('utf8')),
            'rendered_inventory_sha256':digest(json.dumps(prompts,ensure_ascii=False).encode('utf8')),
            'raw_source_scores_IDs_text_order_requests_prompts_unchanged':True,
            'inspection_complete':inspection_complete,'inspection_representations':inspected,
            'whole_request_serialization_used_for_scanning':False}
    # Iterate the original raw trace so the existing UUID alias never substitutes
    # for the real embedding ID used to bind this separate source audit field.
    candidates={};keys={}
    for identity,b in bindings.items():
        if b.get('owned_private_source_verified') and b.get('one_plain_chunk_verified') and 'stable_source_key' in b:
            key=json.dumps(b['stable_source_key'],ensure_ascii=False,separators=(',',':'));keys[key]=keys.get(key,0)+1
    for row in raw.get('returns',[]):
        replica=row.get('replica');results=row.get('native_result_metadata',{}).get('native_results',[]);chunks=row.get('raw_chunks')
        # Find the corresponding local return ordinal in the already canonical
        # replica grouping; global arrival order is not a logical document ID.
        peers=[x for x in raw.get('returns',[]) if x.get('replica')==replica]
        local=next(i for i,x in enumerate(peers) if x is row)
        for index,item in enumerate(results):
            m=item.get('metadata',{});source=m.get('source');identity=m.get('id')
            if not isinstance(source,str):continue
            entry=candidates.setdefault(source,{'reason':None,'locations':[],'binding':None})
            entry['locations'].append({'replica':str(replica),'return_ordinal':local,'result_index':index})
            b=bindings.get(identity)
            if not ready:entry['reason']='complete model input inventory missing'
            elif any(guard_form(source) in form['guard_text'] for scan in inspected for form in scan['representations']):
                entry['reason']='Source appears in independently scanned raw HTTP, parsed string leaf or rendered prompt'
                entry['matched_inspections']=[{'origin':scan['origin'],'request_index':scan.get('request_index'),'replica':scan.get('replica'),'scan_arm':scan.get('scan_arm'),'scan_round':scan.get('scan_round'),'rendered_index':scan.get('rendered_index'),'representation_sha256':form['sha256'],'method':form['method']} for scan in inspected for form in scan['representations'] if guard_form(source) in form['guard_text']]
            elif not isinstance(b,dict) or not b.get('owned_private_source_verified') or not b.get('one_plain_chunk_verified') or 'stable_source_key' not in b:entry['reason']='actual owned one-chunk source binding unavailable'
            elif keys[json.dumps(b['stable_source_key'],ensure_ascii=False,separators=(',',':'))]!=1:entry['reason']='nonunique actual logical document relation'
            elif b['branch']!=replica or source!=b['raw_source'] or not isinstance(chunks,list) or len(chunks)!=len(results) or item.get('content')!=chunks[index] or chunks[index]!=b['text'] or m.get('chunk_index')!=b['chunk_index'] or m.get('mime_type')!=b['mime_type']:entry['reason']='ordered source/ID/branch/chunk/text/MIME storage relation differs'
            elif entry['binding'] is not None and entry['binding']!=b:entry['reason']='one source references distinct logical stored documents'
            else:entry['binding']=b
    for source,entry in candidates.items():
        if entry['reason'] is not None:report['protected'].append({'raw_source':source,'reason':entry['reason'],'locations':entry['locations'],'matched_inspections':entry.get('matched_inspections',[])});continue
        b=entry['binding'];alias='SCAN_AG_AUDIT_SOURCE_'+digest(json.dumps(b['stable_source_key'],ensure_ascii=False,separators=(',',':')).encode('utf8'))
        for loc in entry['locations']:
            canonical['returns'][loc['replica']][loc['return_ordinal']]['native_result_metadata']['native_results'][loc['result_index']]['metadata']['source']=alias
        report['normalized'].append({'raw_source':source,'alias':alias,'actual_source_binding':b,'locations':entry['locations']})
    return report

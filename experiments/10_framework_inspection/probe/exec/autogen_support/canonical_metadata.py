"""Audited AutoGen metadata UUID adapter; separate guarded source audit path policy; no score/prompt normalization."""
from pathlib import Path
import copy,hashlib,json,re,sqlite3
def digest(data):return hashlib.sha256(data).hexdigest()
def storage_bindings(out,phase,project_root):
    state=out/('native_ag_'+phase);receipt=state/'native_complete.json'
    if not receipt.is_file():return {},{'missing_native_completion':True}
    complete=json.loads(receipt.read_bytes());private=Path(complete['private_state']).resolve()
    if not private.is_relative_to(state.resolve()):raise RuntimeError('Chroma storage outside private native state')
    config=json.loads((state/'input.json').read_bytes());bindings={};evidence=[]
    for branch in range(complete['branch_count']):
        directory=private/f'R{branch}';db=directory/'chroma/chroma.sqlite3'
        if not db.is_file():continue
        connection=sqlite3.connect(db.as_uri()+'?mode=ro',uri=True)
        try:
            collections=connection.execute('SELECT name FROM collections').fetchall()
            if len(collections)!=1:raise RuntimeError('Native Chroma collection binding is not unique')
            collection_name=collections[0][0]
            records=connection.execute('SELECT id,embedding_id FROM embeddings').fetchall()
            for key,identity in records:
                metadata={}
                for k,s,i,f,b in connection.execute('SELECT key,string_value,int_value,float_value,bool_value FROM embedding_metadata WHERE id=?',(key,)):
                    metadata[k]=next((v for v in [s,i,f,b] if v is not None),None)
                source=metadata.get('source');text=metadata.get('chroma:document');index=metadata.get('chunk_index')
                if not isinstance(source,str) or not isinstance(text,str) or type(index)is not int:continue
                path=Path(source).resolve();match=re.fullmatch(r'document_(\d+)\.txt',path.name)
                if not match or path.parent!=directory.resolve() or not path.is_file():continue
                ordinal=int(match.group(1))
                if not 0<=ordinal<len(config['documents']):continue
                original=path.read_text(encoding='utf8')
                # Current frozen corpus has plain one-chunk files. Missing or
                # transformed/multi-chunk evidence is protected, never invented.
                if original!=config['documents'][ordinal] or text!=original or index!=0 or len(original)>1500:continue
                binding={'branch':branch,'document_ordinal':ordinal,'document_sha256':digest(path.read_bytes()),
                         'chunk_index':index,'text':text,'mime_type':metadata.get('mime_type'),'raw_source':source,
                         'collection_name':collection_name,
                         'actual_SQLite_embedding_id':identity}
                binding['stable_key']=[branch,binding['collection_name'],ordinal,binding['document_sha256'],index,text,binding['mime_type']]
                if identity in bindings and bindings[identity]!=binding:bindings[identity]={'ambiguous_storage_ID':True}
                else:bindings[identity]=binding
        finally:connection.close()
        evidence.append({'branch':branch,'file':db.relative_to(project_root).as_posix(),'sha256':digest(db.read_bytes()),'actual_rows':len(records)})
    return bindings,{'native_completion':{'file':receipt.relative_to(project_root).as_posix(),'sha256':digest(receipt.read_bytes())},'databases':evidence}

def canonicalize(raw,rendered,bindings,audit=None,source_bindings=None,source_inventory=None):
    from pipeline import canonical_trace
    result=canonical_trace(raw)
    report={'policy':'AutoGen exact native_results[].metadata.id audit UUID path only','normalized':[],'protected':[],
            'raw_source_paths_scores_text_order_raw_prompts_unchanged':True,'storage_kind':'actual read-only Chroma SQLite; no synthetic user/text binding'}
    ready=isinstance(rendered,list) and bool(rendered) and all(isinstance(v,str) and v for v in rendered)
    requests=json.dumps(raw['requests'],ensure_ascii=False)
    report['rendered_inventory_available']=ready;report['request_inventory_sha256']=digest(requests.encode('utf8'))
    report['rendered_inventory_sha256']=digest(json.dumps(rendered,ensure_ascii=False).encode('utf8'))
    counts={}
    for binding in bindings.values():
        if 'stable_key' in binding:
            key=json.dumps(binding['stable_key'],ensure_ascii=False,separators=(',',':'));counts[key]=counts.get(key,0)+1
    candidates={}
    for replica,returns in result['returns'].items():
        for ordinal,row in enumerate(returns):
            metadata=row.get('native_result_metadata')
            if not isinstance(metadata,dict) or not isinstance(metadata.get('native_results'),list):continue
            results=metadata['native_results'];chunks=row.get('raw_chunks')
            for index,item in enumerate(results):
                m=item.get('metadata',{});identity=m.get('id')
                if not isinstance(identity,str) or not re.fullmatch(r'[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}',identity):continue
                entry=candidates.setdefault(identity,{'reason':None,'locations':[]});entry['locations'].append({'replica':replica,'return_ordinal':ordinal,'result_index':index})
                b=bindings.get(identity)
                if not ready:entry['reason']='complete rendered inventory missing'
                elif identity in requests.lower() or any(identity in v.lower() for v in rendered):entry['reason']='ID occurs in complete request or rendered prompt'
                elif not isinstance(b,dict) or 'stable_key' not in b:entry['reason']='actual unique Chroma storage binding unavailable'
                elif counts[json.dumps(b['stable_key'],ensure_ascii=False,separators=(',',':'))]!=1:entry['reason']='nonunique storage relation; retain distinct-ID multiplicity'
                elif b['branch']!=int(replica) or not isinstance(chunks,list) or len(chunks)!=len(results) or item['content']!=chunks[index] or chunks[index]!=b['text']:
                    entry['reason']='ordered branch/ID/text relation differs from actual storage'
                elif m.get('source')!=b['raw_source'] or m.get('chunk_index')!=b['chunk_index'] or m.get('mime_type')!=b['mime_type']:
                    entry['reason']='native source/chunk/mime relation differs from actual storage'
    for identity,entry in candidates.items():
        if entry['reason'] is not None:report['protected'].append({'raw_id':identity,**entry});continue
        binding=bindings[identity];alias='SCAN_AG_AUDIT_UUID_'+digest(json.dumps(binding['stable_key'],ensure_ascii=False,separators=(',',':')).encode('utf8'))
        for loc in entry['locations']:
            result['returns'][loc['replica']][loc['return_ordinal']]['native_result_metadata']['native_results'][loc['result_index']]['metadata']['id']=alias
        report['normalized'].append({'raw_id':identity,'alias':alias,'actual_Chroma_binding':binding,'locations':entry['locations']})
    if source_bindings is not None:
        from autogen_support.source_provenance import canonicalize_sources
        report['source_audit']=canonicalize_sources(raw,result,rendered,source_bindings,source_inventory)
    if audit is not None:audit.update(report)
    return result

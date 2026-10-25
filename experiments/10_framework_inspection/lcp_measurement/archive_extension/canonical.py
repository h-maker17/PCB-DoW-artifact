"""Pure raw/canonical recomputation, using only extracted frozen data functions."""
import ast, copy, json, hashlib, os, re, sqlite3
from pathlib import Path
from urllib.parse import unquote, unquote_plus
from archive_extension.integrity import ROOT, EXT, read_json, sha, objhash, verify_file, ArchiveGateClosed

SOURCE_ROOT = Path(os.environ.get('PCB_SCAN_ROOT', ROOT.parent / 'probe'))


def digest(b): return hashlib.sha256(b).hexdigest()


def extracted_functions(path, names, overrides=None):
    text = Path(path).read_text(encoding='utf8');
    tree = ast.parse(text);
    nodes = [];
    proof = {}

    class BindDataImports(ast.NodeTransformer):
        def visit_ImportFrom(self, node):
            if node.module in ('pipeline', 'autogen_support.source_provenance'): return None
            raise ArchiveGateClosed('unexpected_canonical_import:' + str(node.module))

    for n in tree.body:
        if isinstance(n, ast.FunctionDef) and n.name in names:
            proof[n.name] = digest(ast.dump(n, include_attributes=False).encode());
            nodes.append(BindDataImports().visit(copy.deepcopy(n)))
    if {n.name for n in nodes} != set(names): raise ArchiveGateClosed('canonical_functions_missing')
    namespace = {'Path': Path, 'copy': copy, 'json': json, 're': re, 'hashlib': hashlib, 'digest': digest,
                 'unquote': unquote, 'unquote_plus': unquote_plus}
    namespace.update(overrides or {})
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])), str(path) + '#pure-data-functions',
                 'exec'), namespace)
    return namespace, proof


def support_records(): return {r['original_file']: r for r in read_json(EXT / 'support_manifest.json')['files']}


def supporting(original):
    p = Path(original)
    rel = p.resolve().relative_to(SOURCE_ROOT.resolve()).as_posix() if p.is_absolute() else p.as_posix()
    m = support_records()
    if rel not in m: raise ArchiveGateClosed('unregistered_canonical_support:' + rel)
    return verify_file(m[rel])


def prove_autogen_binding(binding, evidence):
    branch = binding['branch'];
    row = next((r for r in evidence['databases'] if r['branch'] == branch), None)
    if row is None: raise ArchiveGateClosed('Chroma_branch_evidence_missing')
    p = supporting(row['file'])
    if sha(p) != row['sha256']: raise ArchiveGateClosed('Chroma_evidence_SHA')
    db = sqlite3.connect(p.as_uri() + '?mode=ro&immutable=1', uri=True)
    try:
        collections = db.execute('SELECT name FROM collections').fetchall()
        if collections != [(binding['collection_name'],)]: raise ArchiveGateClosed('Chroma_collection_identity')
        rows = db.execute('SELECT id,embedding_id FROM embeddings WHERE embedding_id=?',
                          (binding['actual_SQLite_embedding_id'],)).fetchall()
        if len(rows) != 1: raise ArchiveGateClosed('Chroma_embedding_identity')
        metadata = {}
        for k, s, i, f, b in db.execute(
                'SELECT key,string_value,int_value,float_value,bool_value FROM embedding_metadata WHERE id=?',
                (rows[0][0],)):
            metadata[k] = next((x for x in (s, i, f, b) if x is not None), None)
        if metadata.get('chroma:document') != binding['text'] or metadata.get('source') != binding[
            'raw_source'] or metadata.get('chunk_index') != binding['chunk_index'] or metadata.get('mime_type') != \
                binding['mime_type']: raise ArchiveGateClosed('Chroma_stored_relation_mismatch')
        if db.execute('SELECT COUNT(*) FROM embeddings').fetchone()[0] != row['actual_rows']: raise ArchiveGateClosed(
            'Chroma_row_count_mismatch')
    finally:
        db.close()
    document = supporting(binding['raw_source'])
    if sha(document) != binding['document_sha256'] or document.read_text(encoding='utf8') != binding['text'] or len(
        binding['text']) > 1500 or binding['chunk_index'] != 0: raise ArchiveGateClosed(
        'native_document_binding_mismatch')
    stable = [branch, binding['collection_name'], binding['document_ordinal'], binding['document_sha256'],
              binding['chunk_index'], binding['text'], binding['mime_type']]
    if binding['stable_key'] != stable: raise ArchiveGateClosed('Chroma_stable_relation_mismatch')
    return binding


def autogen_storage_and_sources(rec, phase, evidence):
    complete = read_json(supporting(evidence['native_completion']['file']))
    config = read_json(supporting(Path(rec['original_file']).parent / ('native_ag_' + phase) / 'input.json'))
    state = (SOURCE_ROOT / Path(rec['original_file']).parent / ('native_ag_' + phase)).resolve();
    private = Path(complete['private_state']).resolve()
    if not private.is_relative_to(state) or complete['branch_count'] != 4: raise ArchiveGateClosed(
        'actual_native_private_state_relation')
    documents = config['documents'];
    bindings = {};
    sources = {}
    if not documents or not all(isinstance(x, str) and len(x) <= 1500 for x in documents): raise ArchiveGateClosed(
        'frozen_document_inventory')
    for dbrec in evidence['databases']:
        branch = dbrec['branch'];
        directory = private / f'R{branch}';
        p = supporting(dbrec['file']);
        db = sqlite3.connect(p.as_uri() + '?mode=ro&immutable=1', uri=True)
        try:
            collections = db.execute('SELECT name FROM collections').fetchall()
            if len(collections) != 1: raise ArchiveGateClosed('native_collection_not_unique')
            rows = db.execute('SELECT id,embedding_id FROM embeddings').fetchall()
            if len(rows) != len(documents) or len(rows) != dbrec['actual_rows']: raise ArchiveGateClosed(
                'actual_document_Chroma_count')
            catalog = [];
            receipts = []
            for ordinal, document in enumerate(documents):
                original = directory / f'document_{ordinal}.txt';
                copied = supporting(original)
                if copied.read_text(encoding='utf8') != document: raise ArchiveGateClosed(
                    'input_document_actual_file_mismatch')
                logical = {'namespace': 'frozen AutoGen SimpleDocumentIndexer input.documents', 'ordinal': ordinal,
                           'document_count': len(documents), 'input_text_sha256': digest(document.encode('utf8')),
                           'file_bytes_sha256': sha(copied)}
                catalog.append(logical);
                receipts.append({'file': original.relative_to(SOURCE_ROOT).as_posix(), 'sha256': sha(copied),
                                 'bytes': copied.stat().st_size, 'logical_document': logical})
            catalog_sha = digest(json.dumps(catalog, ensure_ascii=False, separators=(',', ':')).encode('utf8'));
            seen_ordinals = set()
            for rowid, identity in rows:
                metadata = {}
                for k, sv, iv, fv, bv in db.execute(
                        'SELECT key,string_value,int_value,float_value,bool_value FROM embedding_metadata WHERE id=?',
                        (rowid,)):
                    metadata[k] = next((v for v in (sv, iv, fv, bv) if v is not None), None)
                source = metadata.get('source');
                source_path = Path(source).resolve();
                match = re.fullmatch(r'document_(\d+)\.txt', source_path.name)
                if not match or source_path.parent != directory: raise ArchiveGateClosed('owned_source_file_relation')
                ordinal = int(match.group(1))
                if ordinal not in range(len(documents)) or ordinal in seen_ordinals or metadata.get(
                    'chunk_index') != 0 or metadata.get('chroma:document') != documents[
                    ordinal]: raise ArchiveGateClosed('stored_ordered_one_chunk_document_relation')
                seen_ordinals.add(ordinal)
                binding = {'branch': branch, 'document_ordinal': ordinal,
                           'document_sha256': receipts[ordinal]['sha256'], 'chunk_index': 0, 'text': documents[ordinal],
                           'mime_type': metadata.get('mime_type'), 'raw_source': source,
                           'collection_name': collections[0][0], 'actual_SQLite_embedding_id': identity}
                binding['stable_key'] = [branch, binding['collection_name'], ordinal, binding['document_sha256'], 0,
                                         binding['text'], binding['mime_type']]
                if identity in bindings: raise ArchiveGateClosed('storage_embedding_ID_not_unique')
                bindings[identity] = binding
                entry = {'raw_source': source, 'branch': branch, 'collection_name': binding['collection_name'],
                         'document_ordinal': ordinal, 'document_count': len(documents),
                         'ordered_document_catalog': catalog, 'catalog_sha256': catalog_sha,
                         'file_receipt': receipts[ordinal], 'chunk_index': 0, 'text': binding['text'],
                         'mime_type': binding['mime_type'], 'actual_SQLite_embedding_id': identity,
                         'actual_Chroma_binding': binding, 'owned_private_source_verified': True,
                         'one_plain_chunk_verified': True}
                entry['stable_source_key'] = [catalog[ordinal]['namespace'], catalog_sha, branch,
                                              binding['collection_name'], ordinal, receipts[ordinal]['sha256']];
                sources[identity] = entry
        finally:
            db.close()
    return bindings, sources


def prove_source_binding(binding, uuid_binding):
    if binding['actual_Chroma_binding'] != uuid_binding or binding['actual_SQLite_embedding_id'] != uuid_binding[
        'actual_SQLite_embedding_id'] or binding['raw_source'] != uuid_binding['raw_source']: raise ArchiveGateClosed(
        'source_to_embedding_binding_mismatch')
    catalog = binding['ordered_document_catalog']
    if len(catalog) != binding['document_count'] or [x['ordinal'] for x in catalog] != list(
        range(len(catalog))): raise ArchiveGateClosed('document_catalog_order_mismatch')
    raw_path = Path(binding['raw_source']);
    dirpath = raw_path.parent
    for item in catalog:
        p = supporting(dirpath / f"document_{item['ordinal']}.txt")
        if sha(p) != item['file_bytes_sha256'] or digest(p.read_text(encoding='utf8').encode()) != item[
            'input_text_sha256']: raise ArchiveGateClosed('document_catalog_SHA')
    catalog_hash = digest(json.dumps(catalog, ensure_ascii=False, separators=(',', ':')).encode())
    if binding['catalog_sha256'] != catalog_hash: raise ArchiveGateClosed('catalog_hash_mismatch')
    ordinal = binding['document_ordinal'];
    receipt = binding['file_receipt'];
    p = supporting(receipt['file'])
    if sha(p) != receipt['sha256'] or p.stat().st_size != receipt['bytes'] or receipt['logical_document'] != catalog[
        ordinal]: raise ArchiveGateClosed('source_file_receipt_mismatch')
    key = [catalog[ordinal]['namespace'], catalog_hash, binding['branch'], binding['collection_name'], ordinal,
           receipt['sha256']]
    if binding['stable_source_key'] != key or not binding.get('owned_private_source_verified') or not binding.get(
        'one_plain_chunk_verified'): raise ArchiveGateClosed('stable_source_key_mismatch')
    # Booleans are accepted only after actual copied DB/file/catalog relations above were recomputed.
    return binding


def recompute_canonical(run, rec):
    ns, proof = extracted_functions(ROOT / rec['pipeline_source'], {'canonical_trace'})
    canonical_trace = ns['canonical_trace'];
    raw = run['trace'];
    out = {};
    audit_proof = {}
    if rec['dataset'] not in ('autogen_main', 'mem0_diagnostic'):
        for phase in ('benign', 'attacked'): out[phase] = canonical_trace(raw[phase])
    else:
        audit_file = Path(rec['original_file']).parent / 'canonical_metadata_uuid_audit.json';
        saved_audit = read_json(supporting(audit_file))
        for phase in ('benign', 'attacked'):
            arm = raw[phase];
            rendered = [x['rendered'] for m in run['measurements'][phase].values() for x in m['all_prompts']];
            a = saved_audit[phase]
            if digest(json.dumps(arm['requests'], ensure_ascii=False).encode()) != a[
                'request_inventory_sha256'] or digest(json.dumps(rendered, ensure_ascii=False).encode()) != a[
                'rendered_inventory_sha256']: raise ArchiveGateClosed('canonical_guard_inventory_SHA_mismatch')
            if rec['dataset'] == 'mem0_diagnostic':
                storage = read_json(supporting(a['storage_evidence']['file']));
                bindings = {}
                for row in storage['rows']:
                    if row['id'] in bindings: raise ArchiveGateClosed('duplicate_native_memory_ID')
                    bindings[row['id']] = {'user_id': row['payload'].get('user_id'), 'text': row['payload'].get('data')}
                fresh = {};
                out[phase] = canonical_trace(arm, rendered, bindings, fresh)
                if fresh != {k: v for k, v in a.items() if k != 'storage_evidence'}: raise ArchiveGateClosed(
                    'mem0_guarded_canonical_audit_mismatch')
            else:
                evidence = a['storage_evidence'];
                bindings, sources = autogen_storage_and_sources(rec, phase, evidence)
                for row in a['normalized']:
                    if bindings.get(row['raw_id']) != row['actual_Chroma_binding']: raise ArchiveGateClosed(
                        'archived_UUID_binding_not_rebuilt_from_storage')
                source_a = \
                read_json(supporting(Path(rec['original_file']).parent / 'canonical_metadata_source_audit.json'))[phase]
                for row in source_a['normalized']:
                    b = row['actual_source_binding'];
                    identity = b['actual_SQLite_embedding_id']
                    if identity not in bindings: raise ArchiveGateClosed('source_UUID_not_bound')
                    if sources.get(identity) != prove_source_binding(b, bindings[identity]): raise ArchiveGateClosed(
                        'archived_source_binding_not_rebuilt_from_input_storage')
                helpers = {'digest', 'string_leaves', 'unicode_inspection', 'guard_form', 'inspection_representations',
                           'inspect_inventory', 'canonicalize_sources'}
                source_ns, source_proof = extracted_functions(
                    EXT / 'sources/3b5aaf4a_exec__autogen_support__source_provenance.py.txt', helpers)
                ag_ns, ag_proof = extracted_functions(
                    EXT / 'sources/3b5aaf4a_exec__autogen_support__canonical_metadata.py.txt', {'canonicalize'},
                    {'canonical_trace': canonical_trace, 'canonicalize_sources': source_ns['canonicalize_sources']})
                inventory = {'requests': [r for v in raw.values() for r in v['requests']],
                             'rendered': [x['rendered'] for arm in run['measurements'].values() for m in arm.values()
                                          for x in m['all_prompts']]}
                fresh = {};
                out[phase] = ag_ns['canonicalize'](arm, rendered, bindings, fresh, sources, inventory);
                source_fresh = fresh.pop('source_audit')
                if fresh != {k: v for k, v in a.items() if k != 'storage_evidence'}: raise ArchiveGateClosed(
                    'AutoGen_UUID_canonical_audit_mismatch')
                if source_fresh != {k: v for k, v in source_a.items() if
                                    k != 'actual_source_evidence'}: raise ArchiveGateClosed(
                    'AutoGen_source_canonical_audit_mismatch')
                audit_proof.update(source_proof);
                audit_proof.update(ag_proof)
    if 'canonical_trace' in run and out != run['canonical_trace']: raise ArchiveGateClosed(
        'saved_canonical_not_recomputed_from_raw')
    return out, {'pipeline_function_AST': proof, 'exception_function_AST': audit_proof,
                 'recomputed_canonical_sha256': objhash(out), 'saved_canonical_present': 'canonical_trace' in run,
                 'canonical_from_raw_verified': True}

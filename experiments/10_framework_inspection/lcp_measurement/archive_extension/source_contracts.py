"""Actual-source plans for the unchanged Observer; no framework-name locator branches."""
import ast, json
from observer import ast_hash
from archive_extension.integrity import ROOT, EXT, read_json, sha, ArchiveGateClosed


def selected_node(src, spec):
    tree = ast.parse(src);
    fn = next((n for n in ast.walk(tree) if
               isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == spec['function']), None)
    if fn is None or ast_hash(fn) != spec['function_ast_sha256']: raise ArchiveGateClosed(
        'actual_source_function_AST_mismatch')
    k = spec['selector_kind'];
    v = spec['selector_value']
    if k == 'assignment':
        nodes = [n.value for n in ast.walk(fn) if
                 isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == v for t in n.targets)]
    elif k == 'attribute':
        nodes = [n for n in ast.walk(fn) if isinstance(n, ast.Attribute) and n.attr == 'content' and isinstance(n.value,
                                                                                                                ast.Name) and n.value.id == 'memory']
    elif k == 'subscript':
        nodes = [n for n in ast.walk(fn) if isinstance(n, ast.Subscript) and isinstance(n.value,
                                                                                        ast.Name) and n.value.id == 'result' and isinstance(
            n.slice, ast.Constant) and n.slice.value == 'content']
    else:
        raise ArchiveGateClosed('unsupported_actual_selector')
    if len(nodes) != 1 or ast_hash(nodes[0]) != spec['expression_ast_sha256']: raise ArchiveGateClosed(
        'actual_assembly_expression_AST_mismatch')
    return nodes[0]


class ArchiveSourceStore:
    def __init__(self):
        self.root = ROOT;
        self.policy = read_json(ROOT / 'config/policy.json');
        self.registry = read_json(EXT / 'source_registry.json')['contracts']

    def plan(self, contract_id, count):
        if contract_id not in self.registry: raise ValueError('unregistered_actual_source_contract')
        spec = self.registry[contract_id]
        if spec['scope'] != 'framework_archive': raise ValueError('actual_contract_scope_mismatch')
        if spec['kind'] == 'unsupported_diagnostic': raise ValueError(
            'unsupported_actual_G_structure_source_diagnostic_only')
        file = ROOT / spec['source_file']
        if sha(file) != spec['source_sha256']: raise ValueError('actual_source_SHA_mismatch')
        src = file.read_text(encoding='utf8')
        try:
            node = selected_node(src, spec)
        except ArchiveGateClosed as exc:
            raise ValueError(str(exc))
        if spec['kind'] == 'native_join':
            # Exact rag_bot native document.page_content empty join, derived from the verified AST.
            if not (isinstance(node, ast.Call) and isinstance(node.func,
                                                              ast.Attribute) and node.func.attr == 'join' and isinstance(
                node.func.value, ast.Constant) and node.func.value.value == '' and len(node.args) == 1 and isinstance(
                node.args[0], ast.GeneratorExp)): raise ValueError('native_join_primitive_mismatch')
            g = node.args[0];
            it = g.generators[0]
            if len(g.generators) != 1 or it.ifs or it.is_async or not isinstance(it.iter,
                                                                                 ast.Name) or it.iter.id != 'docs' or not isinstance(
                g.elt, ast.Attribute) or g.elt.attr != 'page_content': raise ValueError(
                'native_document_field_chain_mismatch')
            fragments = [('chunk', i) for i in range(count)]
        elif spec['kind'] == 'native_scalar':
            if count != 1: raise ValueError('source_proved_scalar_arity_required')
            if isinstance(node, ast.IfExp):
                if not (isinstance(node.body, ast.Name) and node.body.id == 'doc' and isinstance(node.test,
                                                                                                 ast.Name) and node.test.id == 'include_content' and isinstance(
                    node.orelse, ast.Constant) and node.orelse.value == ''): raise ValueError(
                    'native_scalar_source_mapping_mismatch')
            elif not isinstance(node, (ast.Attribute, ast.Subscript)):
                raise ValueError('native_scalar_field_not_direct')
            fragments = [('chunk', 0)]
        else:
            raise ValueError('unknown_actual_source_primitive')
        return {'fragments': fragments, 'codec': 'identity', 'source_file': spec['source_file'],
                'source_sha256': spec['source_sha256'], 'expression_ast_sha256': spec['expression_ast_sha256'],
                'expression_text': spec['expression_text'], 'native_field': 'raw_chunks', 'scope': 'framework_archive'}


def source_request_binding(native, request, contract_id):
    store = ArchiveSourceStore()
    try:
        plan = store.plan(contract_id, len(native['raw_chunks']))
    except ValueError as exc:
        return {'status': 'unproven', 'reason': str(exc)}
    # No rendered searching, G/S coordinates, token-LCP or classification occurs here.
    parts = [native['returned_chunks'][i] if k == 'chunk' else i for k, i in plan['fragments']];
    block = ''.join(parts)
    candidates = []
    for index, msg in enumerate(request['body']['messages']):
        field = msg.get('content')
        if isinstance(field, str) and field.count(block) == 1: candidates.append((index, msg['role'], field))
    if len(candidates) != 1: return {'status': 'unproven',
                                     'reason': 'native_source_block_not_unique_in_one_final_message_field'}
    i, role, field = candidates[0]
    return {'status': 'source_bound', 'contract_id': contract_id, 'source_file': plan['source_file'],
            'source_sha256': plan['source_sha256'], 'assembly_AST_sha256': plan['expression_ast_sha256'],
            'native_call_key': native['call_key'], 'actual_message_field': ['messages', i, 'content'],
            'actual_role': role, 'field_sha256': __import__('hashlib').sha256(field.encode()).hexdigest(),
            'native_source_block_sha256': __import__('hashlib').sha256(block.encode()).hexdigest(),
            'native_chunk_count': len(native['raw_chunks'])}

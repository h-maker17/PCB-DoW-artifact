"""Pinned LangChain rag_bot grounding locator; never rewrites native data."""
import ast
import hashlib

REPO = 'langchain-ai/langchain'
SOURCE_FILE = 'evidence/official/langchain-ai__docs/sources/src/code-samples/langsmith/evaluate-rag-tutorial.py'
SOURCE_SHA256 = 'f1fe97035f865c12be33abddccf3006c9f2d6fdc1ad4f42c9605298a0af75c51'
MAPPING = 'pinned-rag-bot-empty-join-v1'


def unique_occurrence(text, needle):
    """Count overlapping starts; the generic locator stays unchanged."""
    first = text.find(needle)
    return first >= 0 and text.find(needle, first + 1) < 0


def source_contract(root):
    """Read the exact official body, not its evaluator's double-newline join."""
    try:
        data = (root / SOURCE_FILE).read_bytes()
    except OSError:
        return False
    if hashlib.sha256(data).hexdigest() != SOURCE_SHA256:
        return False
    tree = ast.parse(data.decode('utf-8'))
    expected = ast.dump(ast.parse('docs_string = "".join(doc.page_content for doc in docs)').body[0], include_attributes=False)
    bodies = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'rag_bot']
    return len(bodies) == 1 and sum(ast.dump(n, include_attributes=False) == expected for n in bodies[0].body) == 1


def locate_native_g(root, control, rendered, offsets, returns, replica, locate):
    """Locate exact ordered native bytes, requiring every character adjacency."""
    def reject(reason):
        return {'status': 'ambiguous', 'reason': 'Pinned LangChain mapping refused: ' + reason}
    if control != REPO:
        return reject('different framework/control')
    if not source_contract(root):
        return reject('official formatter source/hash mismatch')
    if not isinstance(returns, list) or len(returns) != 4 or any(not isinstance(r, dict) for r in returns) or {r.get('replica') for r in returns} != {0, 1, 2, 3}:
        return reject('not one actual return per frozen replica')
    chunk_sets = []
    for record in returns:
        chunks = record.get('raw_chunks')
        metadata = record.get('native_result_metadata', {})
        if not isinstance(chunks, list) or len(chunks) != 3 or any(not isinstance(c, str) or not c for c in chunks):
            return reject('expected exactly three nonempty native chunks')
        if not isinstance(metadata, dict) or metadata.get('effective_k') != 4 or not isinstance(metadata.get('documents'), list) or len(metadata['documents']) != 3:
            return reject('frozen native retrieval contract mismatch')
        if record.get('raw') != '\n\n'.join(chunks):
            return reject('original return audit bytes inconsistent')
        chunk_sets.append(tuple(chunks))
    if len(set(chunk_sets)) != 1:
        return reject('native chunk content/order differs across replicas')
    chunks = chunk_sets[0]
    if any(not unique_occurrence(rendered, chunk) for chunk in chunks):
        return reject('a chunk is missing or has overlapping/repeated occurrences')
    chunk_locations = [locate(rendered, chunk, offsets) for chunk in chunks]
    if any(x['status'] != 'ok' for x in chunk_locations):
        return reject('a chunk is missing or not unique')
    if any(left['chars'][1] != right['chars'][0] for left, right in zip(chunk_locations, chunk_locations[1:])):
        return reject('chunks are reordered or not character-adjacent')
    native_text = ''.join(chunks)
    if not unique_occurrence(rendered, native_text):
        return reject('exact native grounding has overlapping/repeated occurrences')
    result = locate(rendered, native_text, offsets)
    if result['status'] != 'ok':
        return reject('exact native assembled grounding is not unique')
    if result['chars'] != [chunk_locations[0]['chars'][0], chunk_locations[-1]['chars'][1]]:
        return reject('whole-string boundary does not equal verified adjacent chunks')
    if rendered[result['chars'][0]:result['chars'][1]] != native_text:
        return reject('assembled grounding byte boundary mismatch')
    return {**result, 'native_chunk_locations': chunk_locations,
            'formatter_mapping': {'id': MAPPING, 'source_file': SOURCE_FILE, 'source_sha256': SOURCE_SHA256}}

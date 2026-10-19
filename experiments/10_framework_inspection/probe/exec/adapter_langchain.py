"""Original official rag_bot/indexing snippets and native RunnableParallel.

Adaptations are provider/corpus connections and return/branch observers only.
The official source snippets are executed verbatim; no prompt/body rewrite.
"""
import argparse
import html
import importlib
import json
import os
from pathlib import Path
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from contextlib import contextmanager

from native_bridge import ROOT, REPLICA, rpc, launch
from embedding_provider import CPUEmbeddingProvider, local_server

SOURCE = ROOT/'evidence/official/langchain-ai__docs/sources/src/code-samples/langsmith/evaluate-rag-tutorial.py'
CACHE = ROOT/'evidence/recipe_preparation/embedding_bridge_20261011T175233Z/cache/tiktoken'


def run(ctx, row):
    launch(ctx, row, 'env', 'lc', 'adapter_langchain.py')


def snippet(source, label):
    marker = '# :snippet-start: ' + label
    return source.split(marker, 1)[1].split('# :snippet-end:', 1)[0]


@contextmanager
def corpus_connection(documents):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            try:
                i = int(self.path.rsplit('/', 1)[-1])
                text = documents[i]
            except (ValueError, IndexError):
                self.send_error(404); return
            payload = ('<html><body><div class="post-content">' + html.escape(text) +
                       '</div></body></html>').encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers(); self.wfile.write(payload)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    import requests
    original_get = requests.get
    stable_urls = [f'http://scan.local/corpus/{i}' for i in range(len(documents))]
    def get(url, *args, **kwargs):
        if url not in stable_urls:
            raise RuntimeError('Corpus loader attempted an undeclared external source')
        kwargs['proxies'] = {'http': None, 'https': None}
        return original_get(f'http://127.0.0.1:{server.server_port}/corpus/{stable_urls.index(url)}', *args, **kwargs)
    requests.get = get
    try:
        yield stable_urls
    finally:
        requests.get = original_get
        server.shutdown(); server.server_close(); thread.join(timeout=5)
        if thread.is_alive(): raise RuntimeError('Corpus connection did not terminate')


def observe_http(config):
    # Reused audited dependency SDKs can use httpx or httpx2. Only connection
    # metadata is added; the original request body/model/defaults stay native.
    for name in ['httpx', 'httpx2']:
        try: module = importlib.import_module(name)
        except ImportError: continue
        original = module.Client.send
        def make_send(native_send):
            def send(self, request, *args, **kwargs):
                if str(request.url).startswith(config['base_url']) and request.url.path.endswith('/chat/completions'):
                    identity = REPLICA.get()
                    if identity is None: raise RuntimeError('LLM request outside native branch')
                    request.headers['X-SCAN-Replica'] = str(identity)
                return native_send(self, request, *args, **kwargs)
            return send
        module.Client.send = make_send(original)


def native(config):
    state = Path(config['state_dir'])
    temp = state/'temp'; temp.mkdir(parents=True, exist_ok=True)
    os.environ.update({'TEMP':str(temp), 'TMP':str(temp), 'TIKTOKEN_CACHE_DIR':str(CACHE),
        'HF_HOME':str(ROOT/'cache/huggingface'), 'HF_HUB_OFFLINE':'1', 'TRANSFORMERS_OFFLINE':'1',
        'LANGSMITH_TRACING':'false', 'LANGSMITH_TRACING_V2':'false', 'LANGSMITH_API_KEY':'scan-local',
        'LANGSMITH_ENDPOINT':'http://127.0.0.1:1', 'OPENAI_API_KEY':'scan-local',
        'PYTHONDONTWRITEBYTECODE':'1', 'NO_PROXY':'127.0.0.1,localhost'})
    # Ordered read-only reuse of two existing audited SCAN dependency roots.
    # Primary env supplies native LC/SDK/CPU encoder. Only missing parser
    # packages fall back to the frozen LF environment; neither is mutated.
    sys.path.append(str(ROOT/'env_langflow/Lib/site-packages'))
    from langchain_core.vectorstores import InMemoryVectorStore
    from langchain_core.runnables import RunnableLambda, RunnableParallel
    source = SOURCE.read_text(encoding='utf-8')
    indexing = snippet(source, 'evaluate-rag-indexing-py')
    generation = snippet(source, 'evaluate-rag-generation-py')
    provider = CPUEmbeddingProvider(ROOT/'evidence/embeddings')
    native_search = InMemoryVectorStore.similarity_search
    retrieval_records = []
    def observe_search(self, query, k=4, **kwargs):
        identity = REPLICA.get()
        if identity is None: raise RuntimeError('Native retrieval outside branch')
        key = rpc(config, 'native-start', {'query':query, 'backend':'LangChain native InMemoryVectorStore',
                   'metadata':{'effective_k':k, 'search_kwargs':kwargs}})['call_key']
        docs = native_search(self, query, k=k, **kwargs)
        records = [doc.model_dump() for doc in docs]
        changed = rpc(config, 'native-return', {'call_key':key, 'query':query,
                    'chunks':[doc.page_content for doc in docs],
                    'metadata':{'effective_k':k, 'documents':[doc.metadata for doc in docs]}})['chunks']
        with provider.lock:
            retrieval_records.append({'replica':identity,'call_key':key,'query':query,
                                      'native_documents':records,'returned_chunks':changed,'effective_k':k})
        return [doc.model_copy(update={'page_content':text}) for doc,text in zip(docs,changed,strict=True)]
    InMemoryVectorStore.similarity_search = observe_search
    observe_http(config)
    namespace = {'__name__':'scan_official_langchain_rag_body'}
    try:
        with local_server(provider) as embedding_url, corpus_connection(config['documents']) as urls:
            os.environ['OPENAI_BASE_URL'] = embedding_url
            os.environ['OPENAI_API_BASE'] = embedding_url
            # Execute literal published imports/helper/URL declaration first.
            before, after = indexing.split('# Load documents from the URLs', 1)
            exec(compile(before, str(SOURCE)+'#indexing-prefix','exec'), namespace)
            namespace['urls'] = urls  # declared corpus connection mapping only
            exec(compile('# Load documents from the URLs'+after, str(SOURCE)+'#indexing','exec'), namespace)
            os.environ['OPENAI_BASE_URL'] = config['base_url']
            os.environ['OPENAI_API_BASE'] = config['base_url']
            exec(compile(generation, str(SOURCE)+'#generation','exec'), namespace)
            rag_bot = namespace['rag_bot']
            def branch(identity):
                def observed(question):
                    token = REPLICA.set(identity)
                    try: return rag_bot(question)
                    finally: REPLICA.reset(token)
                return RunnableLambda(observed)
            parallel = RunnableParallel({f'R{i}':branch(i) for i in range(config['N'])})
            question = config['query'] + '\n\n' + config['history']
            results = parallel.invoke(question)
            valid = len(results)==config['N'] and all(isinstance(v,dict) and
                       isinstance(v.get('answer'),str) and v['answer'].strip() and
                       isinstance(v.get('documents'),list) and v['documents'] for v in results.values())
            if not valid: raise RuntimeError('Native RunnableParallel did not return all complete rag_bot bodies')
            receipt = {'native_recipe_completed':True, 'native_primitive':'RunnableParallel.invoke',
                'branch_count':len(results),'question':question,'shared_input':True,
                'retriever_search_kwargs':namespace['retriever'].search_kwargs,
                'embedding_defaults':{'model':namespace['vectorstore'].embedding.model,
                    'check_embedding_ctx_length':namespace['vectorstore'].embedding.check_embedding_ctx_length,
                    'embedding_ctx_length':namespace['vectorstore'].embedding.embedding_ctx_length},
                'original_join':'empty string','rag_outputs':{k:{'answer':v['answer'],
                    'documents':[d.model_dump() for d in v['documents']]} for k,v in results.items()}}
        (state/'native_complete.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8')
    finally:
        (state/'embedding_provider_events.json').write_text(json.dumps(provider.events,ensure_ascii=False,indent=2),encoding='utf-8')
        (state/'native_retrieval_records.json').write_text(json.dumps(retrieval_records,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__ == '__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--native-input',required=True)
    native(json.loads(Path(parser.parse_args().native_input).read_bytes()))

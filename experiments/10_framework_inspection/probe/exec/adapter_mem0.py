"""Fixed rank6 native batch recipe; formal admission remains predecessor-gated."""
import argparse
import asyncio
import copy
from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parent))
from native_bridge import ROOT, rpc, launch


def run(ctx, row):
    from selection_gate import verify_rank6_preparation
    verify_rank6_preparation(row)
    ctx.canned_json_response = '{"memory":[{"text":"SCAN deterministic extracted memory for native persistence verification."}]}'
    launch(ctx, row, 'env', 'mm', 'adapter_mem0.py')


class Observer:
    def __init__(self, config):
        self.config = config
        self.history = config['history']

    def retrieval_start(self, replica, query, backend, metadata=None):
        return rpc(self.config, 'native-start', {'query': query, 'backend': backend,
                   'metadata': metadata}, replica)['call_key']

    def retrieval_return(self, replica, key, query, chunks, metadata=None):
        return rpc(self.config, 'native-return', {'call_key': key, 'query': query,
                   'chunks': chunks, 'metadata': metadata}, replica)['chunks']


@contextmanager
def embedding_endpoint(provider, state):
    """Embedding transport only. LLMs use the existing shared SCAN service."""
    lock = threading.Lock()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            raw = self.rfile.read(int(self.headers['Content-Length'])).decode('utf8')
            record = {'raw_body_text': raw, 'replica': self.headers.get('X-SCAN-Replica'),
                      'path': self.path}
            try:
                if self.path != '/v1/embeddings': raise ValueError('Embedding-only endpoint')
                body = json.loads(raw)
                data = provider.respond(body)
                record.update(request=body, response=data, status=200)
            except Exception as exc:
                data = {'error': {'message': str(exc)}}
                record.update(status=422, error=repr(exc), traceback=traceback.format_exc())
            with lock:
                with (state/'embedding_http.jsonl').open('a', encoding='utf8') as out:
                    out.write(json.dumps(record, ensure_ascii=False)+'\n'); out.flush()
            payload = json.dumps(data).encode()
            self.send_response(record['status']); self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload))); self.end_headers(); self.wfile.write(payload)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try: yield f'http://127.0.0.1:{server.server_port}/v1'
    finally: server.shutdown(); server.server_close(); thread.join(timeout=5)


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding='utf8')


def native(config):
    state = Path(config['state_dir']).resolve()
    sys.path.insert(0, str(ROOT/'exec/mem0_support'))
    import native_memory as a
    attempt = a.prepare_environment(state/'worker')
    memory = None
    try:
        provider = a.MiniLM384Provider()
        with embedding_endpoint(provider, state) as embeddings_url:
            memory = a.new_memory(attempt, config['base_url'], embeddings_url)
            result = asyncio.run(a.preparation_batch(memory, Observer(config)))
            models = []
            expected = json.dumps(a.EXTRACTION, separators=(',', ':'))
            for replica in range(4):
                records = rpc(config, 'model-evidence', {}, replica)['records']
                models.extend(records)
                if not records or not all(r['request'].get('response_format') == {'type': 'json_object'}
                                          and r['response_message']['content'] == expected
                                          and r['status'] == 200 for r in records):
                    raise RuntimeError('Native extraction lacks actual frozen shared JSON completion')
            write(state/'shared_model_evidence.json', models)
            write(state/'native_batch_evidence.json', result)
            write(state/'model_receipts.json', provider.model_receipts)
            write(state/'native_complete.json', {
                'native_recipe_completed': True, 'branch_count': 4,
                'native_primitive': 'original batch_operations / asyncio.gather(return_exceptions=True)',
                'one_shared_memory_per_phase': True, 'phase': config['phase'],
                'actual_add_completions': len(result['persisted']),
                'actual_shared_model_requests': len(models),
                'native_batch_return': None, 'classification': None,
                'state_dir': str(attempt), 'shared_stub': True,
                'input_rule': 'Message i + newline + exact S; no ctx.query or added native history',
                'declared_adaptations': ['range(5) to range(4)', 'local Qdrant recipe exception',
                    'real CPU MiniLM384 with both dimension configs384; no padding',
                    'MEM0_TELEMETRY=False process isolation', 'missing optional spaCy/fastembed native fallback'],
                'source_commit': 'b7ad69afda6b6ed030347c66d48a13e4de9dec08'})
    except BaseException as exc:
        write(state/'integration_failure.json', {'exception_type': type(exc).__name__,
            'error': str(exc), 'traceback': traceback.format_exc(), 'classification': None})
        raise
    finally:
        # Archive real storage/history before closing, including partial failure.
        if memory is not None:
            try:
                rows, cursor = memory.vector_store.list(top_k=10000)
                write(state/'actual_storage_snapshot.json', {'cursor': cursor,
                    'rows': [{'id': row.id, 'payload': row.payload,
                              'sqlite_history': memory.db.get_history(row.id)} for row in rows],
                    'add_receipts': copy.deepcopy(memory.scan_add_receipts)})
            except Exception as exc: write(state/'snapshot_failure.json', {'error': repr(exc)})
            errors = []
            for label, resource in [('llm', memory.llm.client), ('embedding', memory.embedding_model.client),
                                    ('qdrant', memory.vector_store.client), ('sqlite', memory.db)]:
                try: resource.close()
                except Exception as exc: errors.append({'resource': label, 'error': repr(exc)})
            write(state/'resource_cleanup.json', {'errors': errors})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--native-input', required=True)
    native(json.loads(Path(parser.parse_args().native_input).read_bytes()))

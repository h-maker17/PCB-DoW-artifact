"""Isolated preparation candidate; not a SCAN framework adapter.

Only text-embedding-ada-002 -> cl100k_base -> verified CPU MiniLM is mapped.
Length splitting/aggregation are left entirely to the unmodified native client.
No vector/accuracy parity with the named OpenAI model is asserted.
"""
from __future__ import annotations

import base64
import hashlib
import json
import struct
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class ContractError(ValueError):
    pass


class CPUEmbeddingProvider:
    def __init__(self, model_dir: Path):
        import torch
        import tiktoken
        from sentence_transformers import SentenceTransformer

        self.model_receipts = []
        for receipt in sorted(model_dir.rglob('*.receipt.json')):
            meta = json.loads(receipt.read_text(encoding='utf-8'))
            artifact = Path(str(receipt).removesuffix('.receipt.json'))
            digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
            if digest != meta['sha256']:
                raise ContractError(f'Model artifact hash mismatch: {artifact.name}')
            self.model_receipts.append({'path': artifact.relative_to(model_dir).as_posix(),
                                        'sha256': digest, 'bytes': artifact.stat().st_size,
                                        'source_url': meta['url']})
        if len(self.model_receipts) != 11:
            raise ContractError('Expected all 11 frozen model artifact receipts')
        torch.set_num_threads(1)
        self.encoding = tiktoken.encoding_for_model('text-embedding-ada-002')
        self.model = SentenceTransformer(str(model_dir), device='cpu', local_files_only=True)
        self.lock = threading.Lock()
        self.events = []

    def decode_input(self, value):
        def token_array(item):
            return isinstance(item, list) and all(type(t) is int for t in item)

        if isinstance(value, str):
            return [value], [len(self.encoding.encode_ordinary(value))], 'string'
        if not isinstance(value, list):
            raise ContractError('input must be string, string batch, token array or token-array batch')
        if token_array(value):
            arrays, kind = [value], 'token_array'
        elif value and all(isinstance(t, str) for t in value):
            return value, [len(self.encoding.encode_ordinary(t)) for t in value], 'string_batch'
        elif value and all(token_array(t) for t in value):
            arrays, kind = value, 'token_array_batch'
        else:
            raise ContractError('Mixed/invalid input batch')
        try:
            # Native tiktoken decode default errors="replace" is retained.
            texts = [self.encoding.decode(t) for t in arrays]
        except (KeyError, OverflowError, ValueError) as e:
            raise ContractError('Invalid cl100k_base token ID') from e
        return texts, [len(t) for t in arrays], kind

    def respond(self, body):
        if body.get('model') != 'text-embedding-ada-002':
            raise ContractError('Only the frozen ada-002 client alias is mapped')
        if 'dimensions' in body:
            raise ContractError('Native ada-002 contract has no dimensions override')
        encoding_format = body.get('encoding_format', 'float')
        if encoding_format not in ('float', 'base64'):
            raise ContractError('Unsupported encoding_format')
        texts, token_counts, kind = self.decode_input(body.get('input'))
        with self.lock:
            vectors = self.model.encode(texts, normalize_embeddings=False,
                                        convert_to_numpy=True).astype('<f4')
            if vectors.shape != (len(texts), 384):
                raise ContractError('Unexpected real MiniLM dimensions')
            rows = []
            for i, vector in enumerate(vectors):
                if encoding_format == 'base64':
                    representation = base64.b64encode(struct.pack('<384f', *vector)).decode('ascii')
                else:
                    representation = vector.tolist()
                rows.append({'object': 'embedding', 'index': i, 'embedding': representation})
            # Unit-test synthetic inputs only; not attached to the formal pipeline.
            self.events.append({'request': body, 'kind': kind, 'texts': texts,
                                'token_counts': token_counts, 'dimensions': 384,
                                'provider': 'verified local CPU all-MiniLM-L6-v2',
                                'normalization': False})
        return {'object': 'list', 'data': rows, 'model': body['model'],
                'usage': {'prompt_tokens': sum(token_counts), 'total_tokens': sum(token_counts)}}


@contextmanager
def local_server(provider):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            if self.path.rstrip('/') != '/v1/embeddings':
                self.send_error(404)
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 8 * 1024 * 1024:
                    raise ContractError('Invalid body length')
                request = json.loads(self.rfile.read(length))
                if not isinstance(request, dict):
                    raise ContractError('Expected JSON object')
                response, status = provider.respond(request), 200
            except (ContractError, json.JSONDecodeError, UnicodeDecodeError, ValueError) as e:
                response, status = {'error': {'message': str(e), 'type': 'invalid_request_error'}}, 400
            data = json.dumps(response, ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, name='SCAN-unit-embedding', daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/v1'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        if thread.is_alive():
            raise RuntimeError('Unit-test server did not terminate')

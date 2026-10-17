"""EmbeddingGemma 2 as a small OpenAI-compatible embedding endpoint (POST /v1/embeddings), for Luna's search.

The model and the runtime are pinned to the versions the retrieval study evaluated (see README.md): the vectors of
the page index and of the questions come from the same code. Input texts are embedded as given; the caller adds
the model card's prompts ("task: question answering | query: ...", "title: ... | text: ...").
"""
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import torch
from sentence_transformers import SentenceTransformer

MODEL_DIR = os.environ.get('MODEL_DIR', '/model')
MODEL_ID = os.environ.get('MODEL_ID', 'google/embeddinggemma-2')
MAX_INPUTS = 64
MAX_CHARS = 16000

torch.set_num_threads(max(1, os.cpu_count() or 1))
t0 = time.time()
MODEL = SentenceTransformer(MODEL_DIR, device='cpu')
MODEL.max_seq_length = 4096
LOCK = threading.Lock()  # one forward pass at a time: the cpu is the bottleneck, threads would only thrash
print(f'loaded {MODEL_ID} from {MODEL_DIR} in {time.time() - t0:.1f}s', flush=True)


class Handler(BaseHTTPRequestHandler):
  def send(self, code, body):
    data = json.dumps(body).encode()
    self.send_response(code)
    self.send_header('Content-Type', 'application/json')
    self.send_header('Content-Length', str(len(data)))
    self.end_headers()
    self.wfile.write(data)

  def do_GET(self):
    if self.path in ('/health', '/'):
      return self.send(200, {'status': 'ok', 'model': MODEL_ID})
    self.send(404, {'error': 'not found'})

  def do_POST(self):
    if self.path != '/v1/embeddings':
      return self.send(404, {'error': 'not found'})
    try:
      body = json.loads(self.rfile.read(int(self.headers.get('Content-Length') or 0)) or b'{}')
      texts = body.get('input')
      texts = [texts] if isinstance(texts, str) else texts
      if not isinstance(texts, list) or not texts or len(texts) > MAX_INPUTS or not all(isinstance(t, str) for t in texts):
        return self.send(400, {'error': f'input: 1 to {MAX_INPUTS} strings'})
      with LOCK:
        vecs = MODEL.encode([t[:MAX_CHARS] for t in texts], normalize_embeddings=True, batch_size=16)
      self.send(200, {'object': 'list', 'model': MODEL_ID,
                      'data': [{'object': 'embedding', 'index': i, 'embedding': [round(float(x), 6) for x in v]}
                               for i, v in enumerate(vecs)]})
    except Exception as e:
      self.send(500, {'error': type(e).__name__})

  def log_message(self, *args):
    pass


if __name__ == '__main__':
  ThreadingHTTPServer(('0.0.0.0', int(os.environ.get('PORT', '8080'))), Handler).serve_forever()

"""Embed every graph recommendation and concept once -> app/data/kg_vectors.npz, for Guido's semantic linking.

  python pipeline/build_kg_vectors.py

uses the OpenAI compatible /embeddings endpoint from .env (LLM_BASE_URL, LLM_API_KEY), model EMBED_MODEL
(default gemini-embedding-001) at EMBED_DIM dimensions (default 256). vectors are stored L2-normalised as float16.
a question in german, russian or hindi lands next to the english guideline text, no translation step needed.

optional. without the file guido links lexically (labels + bm25 over recommendation text) and ranks with pagerank,
which is what the demo runs on. the free gemini tier allows 100 texts a minute and 1000 a day, so the 13k nodes
need a paid key (or a few days). it paces itself and can be stopped and restarted any time: finished batches sit in
pipeline/.vectors_cache.json. recommendations go first, then concepts by how much evidence hangs on them.
every query then costs one embedding call too, so only ship the file with a key that can take the traffic.
"""
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.request
from collections import Counter

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
import config  # noqa: E402  loads .env

MODEL = os.environ.get('EMBED_MODEL', 'gemini-embedding-001')
DIM = int(os.environ.get('EMBED_DIM', '256'))
BATCH = 50  # the free tier counts texts, 100 a minute. half batches leave room for live query embeddings
PER_MINUTE = int(os.environ.get('EMBED_PER_MINUTE', '90'))
CACHE = ROOT / 'pipeline' / '.vectors_cache.json'
OUT = ROOT / 'app' / 'data' / 'kg_vectors.npz'


def embed(texts):
  body = {'model': MODEL, 'input': texts, 'dimensions': DIM}
  req = urllib.request.Request(config.LLM_BASE_URL.rstrip('/') + '/embeddings', data=json.dumps(body).encode(), method='POST',
                               headers={'Authorization': 'Bearer ' + config.LLM_API_KEY, 'Content-Type': 'application/json'})
  for attempt in range(10):
    try:
      with urllib.request.urlopen(req, timeout=60) as r:
        return [d['embedding'] for d in json.load(r)['data']]  # same order as the input
    except urllib.error.HTTPError as e:
      if e.code not in (429, 500, 503):
        raise
      time.sleep(min(60, 5 * 2 ** attempt))
  raise SystemExit('embeddings api keeps failing')


def save(done):
  ids = list(done)
  m = np.asarray([done[i] for i in ids], dtype=np.float32)
  m /= np.linalg.norm(m, axis=1, keepdims=True) + 1e-9
  np.savez_compressed(OUT, vectors=m.astype(np.float16), ids=np.array(ids), model=MODEL, dim=DIM)


if __name__ == '__main__':
  import graph as kg_graph  # noqa: E402  Guido's working view, from the graph server (GRAPH_ENDPOINT)
  graph = kg_graph.agent_view()
  evidence = Counter(b for a, b, _ in graph['edges'] if a.startswith('r:'))
  jobs = [(r['id'], r['text'][:1500]) for r in graph['recs']]
  jobs += [(c['id'], '; '.join([c['label']] + c['aliases'][:6] + ([c['desc']] if c.get('desc') else [])))
           for c in sorted(graph['concepts'], key=lambda c: -evidence[c['id']])]
  done = json.loads(CACHE.read_text()) if CACHE.exists() else {}
  todo = [j for j in jobs if j[0] not in done]
  print(f'{len(done)} cached, {len(todo)} to go at ~{PER_MINUTE}/min')
  for i in range(0, len(todo), BATCH):
    t0 = time.time()
    chunk = todo[i:i + BATCH]
    for (nid, _), v in zip(chunk, embed([t for _, t in chunk])):
      done[nid] = v
    CACHE.write_text(json.dumps(done))
    if (i // BATCH) % 10 == 9:
      save(done)  # the server can already use what is there
    print(f'{len(done)}/{len(jobs)}', flush=True)
    time.sleep(max(0, len(chunk) * 60 / PER_MINUTE - (time.time() - t0)))
  save(done)
  CACHE.unlink()
  print(f'{len(done)} vectors, {DIM} dims, {MODEL} -> {OUT}')

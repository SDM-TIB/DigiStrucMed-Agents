"""How Guido finds where to start, and what matters around it. HippoRAG style, on our guideline graph.

  1. four rankings of the query:  concept labels (lexical idf)   concept vectors (dense)
                                   recommendation text (bm25)    recommendation vectors (dense)
  2. reciprocal rank fusion, k=60 (Cormack et al., SIGIR 2009) -> seed concepts and seed recommendations
  3. personalized pagerank from the seeds over the whole graph, restart 0.5 (HippoRAG, Gutierrez et al., NeurIPS 2024)
     concept seeds weighted by specificity, so 'patient' can't drown 'ivabradine'
  4. recommendations ranked by pagerank mass: evidence that is close to the question in the graph,
     even when no concept label matched. that is what kills "I found nothing"

searching recommendation text directly is the HippoRAG 2 lesson (Gutierrez et al., ICML 2025): passage nodes
took recall@5 from 81.0 to 87.1 in their ablation. the dense side makes german, russian and hindi questions land
on english guideline text. without kg_vectors.npz or an embeddings api it falls back to the lexical rankings.
"""
import json
import math
import urllib.request
from collections import Counter, defaultdict

import numpy as np

import config
import kg

RRF_K = 60
RESTART = 0.5
ITERATIONS = 25

# ---- dense vectors (pipeline/build_kg_vectors.py)
try:
  _npz = np.load(config.DATA / 'kg_vectors.npz')
  VECTORS, VECTOR_IDS = _npz['vectors'].astype(np.float32), [str(x) for x in _npz['ids']]
  EMBED_MODEL, EMBED_DIM = str(_npz['model']), int(_npz['dim'])
except (FileNotFoundError, KeyError):
  VECTORS, VECTOR_IDS, EMBED_MODEL, EMBED_DIM = None, [], '', 0
IS_REC = np.array([i.startswith('r:') for i in VECTOR_IDS]) if VECTOR_IDS else None


def embed(text):
  """one query vector from the same openai compatible api, or None"""
  if VECTORS is None or not config.LLM_API_KEY:
    return None
  body = {'model': EMBED_MODEL, 'input': [text[:2000]], 'dimensions': EMBED_DIM}
  req = urllib.request.Request(config.LLM_BASE_URL.rstrip('/') + '/embeddings', data=json.dumps(body).encode(), method='POST',
                               headers={'Authorization': 'Bearer ' + config.LLM_API_KEY, 'Content-Type': 'application/json'})
  try:
    with urllib.request.urlopen(req, timeout=10) as r:
      v = np.asarray(json.load(r)['data'][0]['embedding'], dtype=np.float32)
    return v / (np.linalg.norm(v) + 1e-9)
  except Exception:
    return None


def dense(vec, recs, k):
  if vec is None:
    return []
  sims = VECTORS @ vec
  sims[IS_REC != recs] = -1
  top = np.argpartition(-sims, k)[:k]
  return [VECTOR_IDS[i] for i in top[np.argsort(-sims[top])]]


# ---- bm25 over recommendation text
_post, _len = defaultdict(list), {}
for _rid, _r in kg.RECS.items():
  _tf = Counter(kg.toks(_r['text']))
  _len[_rid] = sum(_tf.values())
  for _t, _c in _tf.items():
    _post[_t].append((_rid, _c))
_avg = sum(_len.values()) / max(1, len(_len))


def rec_bm25(query, k, k1=1.2, b=0.75):
  scores = defaultdict(float)
  for t in set(kg.toks(kg.expand(query))):
    post = _post.get(t, ())
    idf = math.log(1 + (len(kg.RECS) - len(post) + .5) / (len(post) + .5))
    for rid, c in post:
      scores[rid] += idf * c * (k1 + 1) / (c + k1 * (1 - b + b * _len[rid] / _avg))
  return [rid for rid, _ in sorted(scores.items(), key=lambda x: -x[1])[:k]]


def rrf(*rankings):
  fused = defaultdict(float)
  for ranking in rankings:
    for rank, node in enumerate(ranking):
      fused[node] += 1 / (RRF_K + rank + 1)
  return fused


# ---- personalized pagerank over the undirected graph
_nodes = list(kg.CONCEPTS) + list(kg.RECS) + list(kg.GUIDES)
_index = {n: i for i, n in enumerate(_nodes)}
_src, _dst = [], []
for _a, _b, _role in ((a, b, r) for a, outs in kg.OUT.items() for b, r in outs):
  if _a in _index and _b in _index and _role != 'source':  # guideline hubs would pull everything together
    _src += [_index[_a], _index[_b]]
    _dst += [_index[_b], _index[_a]]
_src, _dst = np.array(_src), np.array(_dst)
_degree = np.bincount(_src, minlength=len(_nodes)).astype(np.float32)
_weight = 1 / np.maximum(_degree[_src], 1)


def pagerank(seeds):
  """seeds: {node: weight} -> stationary mass per node"""
  s = np.zeros(len(_nodes), dtype=np.float32)
  for n, w in seeds.items():
    if n in _index:
      s[_index[n]] += w
  if not s.sum():
    return s
  s /= s.sum()
  p = s.copy()
  for _ in range(ITERATIONS):
    p = RESTART * s + (1 - RESTART) * np.bincount(_dst, weights=p[_src] * _weight, minlength=len(_nodes)).astype(np.float32)
  return p


def search(query, question='', concepts=4, recs=8):
  """seed concepts, seed recommendations matched directly, and recommendations ranked by pagerank"""
  text = (query + ' ' + question).strip()
  vec = embed(query or question)
  lexical_concepts = kg.find_concepts(text, k=12)
  fused = rrf(lexical_concepts, dense(vec, False, 12), rec_bm25(query or question, 15), dense(vec, True, 15))
  fused = {n: s for n, s in fused.items() if not (n in kg.CONCEPTS and len(kg.toks(kg.CONCEPTS[n]['label'])) > 6)}
  ranked = sorted(fused, key=lambda n: -fused[n])
  seed_concepts = [n for n in ranked if n in kg.CONCEPTS and (kg.n_recs(n) or kg.OUT[n])][:concepts]
  seed_recs = [n for n in ranked if n in kg.RECS][:recs]
  # specificity: a concept seen in hundreds of recommendations says little about this question
  seeds = {n: fused[n] / math.log(2 + kg.n_recs(n)) for n in seed_concepts}
  seeds.update({n: fused[n] for n in seed_recs})
  mass = pagerank(seeds)
  by_mass = sorted((n for n in kg.RECS if mass[_index[n]] > 0), key=lambda n: -mass[_index[n]])
  ranked_recs = list(dict.fromkeys(seed_recs[:3] + by_mass))[:recs]
  return {'concepts': seed_concepts, 'matched': seed_recs[:3], 'recs': ranked_recs,
          'mass': {n: float(mass[_index[n]]) for n in ranked_recs + seed_concepts}, 'dense': vec is not None}

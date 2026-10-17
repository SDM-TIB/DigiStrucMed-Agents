"""Luna's search over the guideline pages: EmbeddingGemma 2 retrieval, then clef-flash reranking.

The design and every setting come from the retrieval study in MASTERTHESIS-HEART
(projects/03_reasoning_aware_guideline_retrieval/experiments/2026-10-17_embeddinggemma2_retrieval, README.md):
on a held-out test half it found a page stating the evidence in the top 5 for 97 % of the questions (BM25: 69 %).

  1. candidates: the question is embedded twice (query prompts "question answering" and "search result") by the
     embedding service (embed-server/, EmbeddingGemma 2). Four vector views of the 2,780 pages are scored: chunks of
     400, 600 and 1,200 characters (a page scores its best chunk) and whole pages. The eight dense rankings and
     BM25 are fused by reciprocal rank (k = 60); the top 40 pages are the candidates (recall@40 0.988 in the study).
  2. reranking: Cloudflare's clef-flash, a System One decision model, judges every candidate on its own: does
     this page contain the specific information the question asks for? (the re-ranking recipe of TypeSafe's Jev
     cookbook). Pages are ordered by that probability, which also goes to Luna as the page's relevance.
Without the index, the embedding service or clef-flash, the search degrades step by step (dense order, then BM25)
and says which mode it ran in.

The index (search_index.npz) is built by pipeline/build_search_index.py and pinned by sha256 in search.lock.json;
it is downloaded once from the page bucket and checked against the page corpus before use.
"""
import concurrent.futures
import hashlib
import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np

import config
import corpus
import pageunits
import storage

LOCK_FILE = Path(__file__).with_name('search.lock.json')
VIEWS = ('chunk_title_s400', 'chunk_title_s600', 'chunk_title', 'page_plain')
PROMPTS = {'qa': 'task: question answering | query: ', 'search': 'task: search result | query: '}
RRF_K = 60
CANDIDATES = 40
CLEF_URL = 'https://api.cloudflare.com/client/v4/accounts/{}/ai/run/@cf/cloudflare/clef-flash'
INSTRUCTIONS = ('Does `candidate` contain the specific information that answers `question`: the guideline statement, '
                'value, class of recommendation or level of evidence it asks for, for the population, situation and '
                'guideline it names?')
CRITERIA = {'true': 'The candidate states the specific fact, value, recommendation or grading the question asks for, '
                    'for the situation it describes.',
            'false': 'The candidate is only on a related topic, or about another population, drug, situation or '
                     'guideline; it does not supply the specific answer.'}
PAGE_CHARS = 6000  # page text a clef-flash judgement reads, as in the study

_state = {'index': None, 'error': None, 'loading': False}
_lock = threading.Lock()
_id_token = {'value': '', 'exp': 0.0}


# ---- the index
def _load():
  """download (once), verify and load the index; on any mismatch the search runs without it"""
  try:
    lock = json.loads(LOCK_FILE.read_text(encoding='utf-8'))
    path = Path('/tmp') / f"search_index_{lock['sha256'][:12]}.npz"
    if not path.exists():
      local = config.DATA / 'search_index.npz'
      data = local.read_bytes() if local.exists() else storage.get_large(lock['object'])
      if data is None:
        raise RuntimeError(f"index object {lock['object']} not found")
      if hashlib.sha256(data).hexdigest() != lock['sha256']:
        raise RuntimeError('index sha256 differs from search.lock.json')
      path.write_bytes(data)
    z = np.load(path)
    page_ids = [str(x) for x in z['page_ids']]
    if page_ids != [p['id'] for p in corpus.PAGES]:
      raise RuntimeError('index pages differ from the page corpus')
    views = {}
    for v in VIEWS:
      owner = z[f'{v}_page'].astype(np.int64)
      if len(owner) != len(pageunits.page_units(corpus.PAGES, v)):
        raise RuntimeError(f'{v}: unit count differs from pageunits')
      starts = np.flatnonzero(np.r_[True, owner[1:] != owner[:-1]])
      views[v] = {'vec': z[f'{v}_vec'].astype(np.float32), 'owner': owner, 'starts': starts, 'pages': owner[starts]}
    chunks = [t.split(' | text: ', 1)[1] for _, t in pageunits.page_units(corpus.PAGES, 'chunk_title_s400')]
    _state['index'] = {'views': views, 'chunks': chunks, 'meta': json.loads(str(z['meta']))}
    print(f"search: index {lock['sha256'][:12]} loaded, {sum(len(x['owner']) for x in views.values())} vectors", flush=True)
  except Exception as e:
    _state['error'] = f'{type(e).__name__}: {e}'
    print('search: no index,', _state['error'], flush=True)
  finally:
    _state['loading'] = False


def load():
  """load the index now (at startup, before the server listens: Cloud Run throttles the CPU of background threads
  between requests, so a background load would take minutes)"""
  with _lock:
    if _state['index'] is not None or _state['loading']:
      return
    _state['loading'] = True
  _load()


def warm():
  """load the index in the background and wake the embedding service; safe to call often"""
  with _lock:
    if _state['index'] is None and not _state['loading'] and _state['error'] is None:
      _state['loading'] = True
      threading.Thread(target=_load, daemon=True).start()
  if config.EMBED_URL:
    threading.Thread(target=lambda: _get(config.EMBED_URL.rstrip('/') + '/health', 30), daemon=True).start()


def status():
  return {'index': _state['index'] is not None, 'loading': _state['loading'], 'error': _state['error'],
          'embed': bool(config.EMBED_URL), 'rerank': bool(config.CF_ACCOUNT_ID and config.CF_API_TOKEN)}


# ---- the embedding service (Cloud Run, invoked with this service account's identity token)
def _identity_token():
  if time.time() < _id_token['exp']:
    return _id_token['value']
  url = ('http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity?audience='
         + config.EMBED_URL.rstrip('/'))
  try:
    with urllib.request.urlopen(urllib.request.Request(url, headers={'Metadata-Flavor': 'Google'}), timeout=5) as r:
      _id_token.update(value=r.read().decode(), exp=time.time() + 45 * 60)
  except OSError:  # local development: the developer's gcloud identity
    import shutil
    import subprocess
    out = subprocess.run([shutil.which('gcloud') or 'gcloud', 'auth', 'print-identity-token', '--audiences=' + config.EMBED_URL.rstrip('/')], capture_output=True, text=True, timeout=30)
    _id_token.update(value=out.stdout.strip(), exp=time.time() + 30 * 60)
  return _id_token['value']


def _get(url, timeout):
  try:
    req = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + _identity_token()})
    with urllib.request.urlopen(req, timeout=timeout) as r:
      return r.read()
  except Exception:
    return None


def embed(texts, timeout=25):
  body = json.dumps({'input': texts}).encode()
  req = urllib.request.Request(config.EMBED_URL.rstrip('/') + '/v1/embeddings', data=body, method='POST',
                               headers={'Authorization': 'Bearer ' + _identity_token(), 'Content-Type': 'application/json'})
  with urllib.request.urlopen(req, timeout=timeout) as r:
    data = sorted(json.load(r)['data'], key=lambda d: d['index'])
  v = np.asarray([d['embedding'] for d in data], dtype=np.float32)
  return v / np.linalg.norm(v, axis=1, keepdims=True)


# ---- clef-flash
def _judge(question, query, i):
  p = corpus.PAGES[i]
  state = {'question': question,
           'candidate': {'source': f"{pageunits.doc_title(p['title'])}, page {p['page']}",
                         'text': pageunits.table_text(p['text'])[:PAGE_CHARS]}}
  if query and query.strip().lower() != question.strip().lower():
    state['search_query'] = query  # what Luna looks for right now, within the question
  body = {'model': 'clef-flash', 'state': state,
          'questions': {'answers': {'type': 'noul', 'instructions': INSTRUCTIONS, 'criteria': CRITERIA}}}
  req = urllib.request.Request(CLEF_URL.format(config.CF_ACCOUNT_ID), data=json.dumps(body, ensure_ascii=False).encode(),
                               method='POST', headers={'Authorization': 'Bearer ' + config.CF_API_TOKEN,
                                                       'Content-Type': 'application/json'})
  for attempt in range(3):
    try:
      with urllib.request.urlopen(req, timeout=20) as r:
        res = json.load(r)
      return float(res.get('result', res).get('answers', {}).get('answers', {}).get('noul', 0.0))
    except urllib.error.HTTPError as e:
      if e.code not in (429, 500, 502, 503, 504) or attempt == 2:
        return None
    except Exception:
      if attempt == 2:
        return None
    time.sleep(0.5 * 2 ** attempt)


_pool = concurrent.futures.ThreadPoolExecutor(CANDIDATES)


# ---- the search
def _rank(scores):
  r = np.empty(len(scores), dtype=np.int64)
  r[np.argsort(-scores, kind='stable')] = np.arange(1, len(scores) + 1)
  return r


def search(query, question=None, k=5):
  """[{'page': page index, 'relevance': clef-flash probability or None, 'passages': [chunk texts]}], mode.
  query: what to look for now; question: the user's question it serves (defaults to the query)"""
  question = (question or query).strip()
  query = query.strip()
  idx = _state['index']
  if idx is None:
    warm()
  vecs = None
  if idx is not None and config.EMBED_URL:
    for attempt in range(2):  # one retry: a cold or busy embedding service can drop a request
      try:
        vecs = dict(zip(PROMPTS, embed([PROMPTS['qa'] + query, PROMPTS['search'] + query])))
        break
      except Exception:
        vecs = None
  lex_order = corpus.bm25(query, k=len(corpus.PAGES), per_doc=len(corpus.PAGES))
  lex_rank = np.full(len(corpus.PAGES), len(corpus.PAGES) + 1, dtype=np.int64)
  lex_rank[lex_order] = np.arange(1, len(lex_order) + 1)
  if vecs is None:
    return [{'page': i, 'relevance': None, 'passages': []} for i in lex_order[:k]], 'bm25'

  fused = 1 / (RRF_K + lex_rank)
  best_chunk = None
  for name, view in idx['views'].items():
    for prompt, q in vecs.items():
      sims = view['vec'] @ q
      page_score = np.full(len(corpus.PAGES), -1.0, dtype=np.float32)
      page_score[view['pages']] = np.maximum.reduceat(sims, view['starts'])
      fused += 1 / (RRF_K + _rank(page_score))
      if name == 'chunk_title_s400' and prompt == 'qa':
        best_chunk = (sims, view)
  cand = [int(i) for i in np.argsort(-fused, kind='stable')[:CANDIDATES]]

  mode = 'embeddinggemma2'
  rel = {}
  if config.CF_ACCOUNT_ID and config.CF_API_TOKEN:
    rel = dict(zip(cand, _pool.map(lambda i: _judge(question, query, i), cand)))
    if sum(v is not None for v in rel.values()) >= CANDIDATES // 2:
      mode = 'embeddinggemma2+clef-flash'
      cand.sort(key=lambda i: -(rel[i] if rel[i] is not None else -1.0))  # stable: ties keep the fused order
    else:
      rel = {}

  sims, view = best_chunk
  out = []
  for i in cand[:k]:
    out.append({'page': i, 'relevance': None if rel.get(i) is None else round(rel[i], 2),
                'passages': _windows(i, sims, view)})
  return out, mode


def _windows(i, sims, view, n=2, width=1):
  """the page text around its best chunks: each best 400-character chunk with `width` neighbours on each side
  (about 1,200 characters), at most n windows that do not overlap, in page order. A value next to the passage
  that matched (a dose, a threshold) stays in view"""
  units = np.flatnonzero(view['owner'] == i)
  if not len(units):
    return []
  text = pageunits._page_text(corpus.PAGES[i])
  spans = pageunits.chunks(text, size=400, overlap=50)  # = the units of chunk_title_s400 on this page
  order = [int(u - units[0]) for u in units[np.argsort(-sims[units])]]
  picked = []
  for c in order:
    lo, hi = max(0, c - width), min(len(spans) - 1, c + width)
    if all(hi < a or lo > b for a, b in picked):
      picked.append((lo, hi))
    if len(picked) == n:
      break
  return [text[spans[lo][0]:spans[hi][1]].strip() for lo, hi in sorted(picked)]

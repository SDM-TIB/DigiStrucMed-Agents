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
import re
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
IMAGE_VIEW = 'page_image'  # optional: each page rendered and embedded as an image (pipeline/build_search_index.py --images)
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
    if f'{IMAGE_VIEW}_vec' in z.files:
      owner = z[f'{IMAGE_VIEW}_page'].astype(np.int64)
      if not np.array_equal(owner, np.arange(len(corpus.PAGES))):
        raise RuntimeError(f'{IMAGE_VIEW}: not one vector per page in corpus order')
      views[IMAGE_VIEW] = {'vec': z[f'{IMAGE_VIEW}_vec'].astype(np.float32), 'owner': owner, 'starts': owner, 'pages': owner}
    chunks = [t.split(' | text: ', 1)[1] for _, t in pageunits.page_units(corpus.PAGES, 'chunk_title_s400')]
    words = [(p['title'] + ' ' + p['plain']).lower() for p in corpus.PAGES]
    kinds = [page_kinds(p['text']) for p in corpus.PAGES]
    kinds = {k: np.array([k in x for x in kinds], dtype=np.float64) for k in PAGE_KINDS}
    _state['index'] = {'views': views, 'chunks': chunks, 'words': words, 'kinds': kinds, 'meta': json.loads(str(z['meta']))}
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
  # the embedding service scales to zero and takes about 50 s to start (Cloud Run instance plus 10 to 14 s model load).
  # the app pings it when someone opens the app or focuses the chat input, so it is up before the first search;
  # at most one ping per two minutes
  if config.EMBED_URL and time.time() - _state.get('pinged', 0) > 120:
    _state['pinged'] = time.time()
    threading.Thread(target=lambda: _get(config.EMBED_URL.rstrip('/') + '/health', 90), daemon=True).start()


def status():
  return {'index': _state['index'] is not None, 'loading': _state['loading'], 'error': _state['error'],
          'embed': bool(config.EMBED_URL), 'rerank': bool(config.CF_ACCOUNT_ID and config.CF_API_TOKEN)}


# ---- the embedding service (Cloud Run, invoked with this service account's identity token)
def _identity_token():
  return storage.identity_token(config.EMBED_URL)


def _get(url, timeout):
  try:
    req = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + _identity_token()})
    with urllib.request.urlopen(req, timeout=timeout) as r:
      return r.read()
  except Exception:
    return None


def embed(texts, timeout=60):
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


_pool = concurrent.futures.ThreadPoolExecutor(4 * CANDIDATES)  # shared by concurrent searches: several agent runs, several searches per step


# ---- the search
def _rank(scores):
  r = np.empty(len(scores), dtype=np.int64)
  r[np.argsort(-scores, kind='stable')] = np.arange(1, len(scores) + 1)
  return r


CANDIDATE_METHODS = ('rrf', 'digup')


def candidates(query, vecs, allowed=None, method=None, n=CANDIDATES):
  """the pages the reranker reads, best first. method 'rrf': reciprocal-rank fusion of BM25 and every text view under
  both prompts (the study's recipe); 'digup': digup() on the search-prompt vector (passages, whole page, page image,
  word share, kind hints). allowed: a mask of the pages that may be returned (a guideline filter), or None"""
  method = method or config.SEARCH_CANDIDATES
  idx = _state['index']
  if method == 'digup' and IMAGE_VIEW in idx['views']:
    score = digup(vecs['search'], query, idx)[0].astype(np.float64)
  else:
    lex_order = [i for i in corpus.bm25(query, k=len(corpus.PAGES), per_doc=len(corpus.PAGES)) if allowed is None or allowed[i]]
    lex_rank = np.full(len(corpus.PAGES), len(corpus.PAGES) + 1, dtype=np.int64)
    lex_rank[lex_order] = np.arange(1, len(lex_order) + 1)
    score = 1 / (RRF_K + lex_rank)
    for name in VIEWS:  # the text views only
      view = idx['views'][name]
      for q in vecs.values():
        page_score = np.full(len(corpus.PAGES), -1.0, dtype=np.float32)
        page_score[view['pages']] = np.maximum.reduceat(view['vec'] @ q, view['starts'])
        score = score + 1 / (RRF_K + _rank(page_score))
  if allowed is not None:
    score = np.where(allowed, score, -np.inf)
  n = min(n, len(corpus.PAGES) if allowed is None else int(allowed.sum()))
  return [int(i) for i in np.argsort(-score, kind='stable')[:n]]


def search(query, question=None, k=5, docs=None, progress=None, method=None):
  """{'hits': [{'page', 'relevance', 'passages'}], 'mode', 'facets'}
  method: how the candidates are found (candidates(); default config.SEARCH_CANDIDATES)
  progress: optional callback, called with each stage as it starts and ends ({'stage': 'embed' | 'compare' |
  'rerank', 'status': 'running' | 'done', 'ms', and what the stage worked on}); the timings are also logged
  query: what to look for now; question: the user's question it serves (defaults to the query); docs: document ids to
  search in (a guideline filter), None for all. facets: for each document, how many of the reranked candidates it
  holds and how many of them are likely relevant (relevance >= 0.5), so the caller sees where evidence clusters
  beyond the top k"""
  question = (question or query).strip()
  query = query.strip()
  report = progress or (lambda ev: None)
  t_start = time.time()
  idx = _state['index']
  if idx is None:
    warm()
  vecs = None
  if idx is not None and config.EMBED_URL:
    report({'stage': 'embed', 'status': 'running', 'model': 'EmbeddingGemma 2'})
    t0 = time.time()
    for attempt in range(3):  # a cold or busy embedding service can drop a request: retry with backoff
      try:
        vecs = dict(zip(PROMPTS, embed([PROMPTS['qa'] + query, PROMPTS['search'] + query])))
        break
      except Exception:
        vecs = None
        time.sleep(2 ** attempt)
    t_embed = round((time.time() - t0) * 1000)
    report({'stage': 'embed', 'status': 'done' if vecs is not None else 'error', 'ms': t_embed, 'model': 'EmbeddingGemma 2',
            'dims': int(next(iter(vecs.values())).shape[0]) if vecs else None, 'texts': len(PROMPTS)})
  t0 = time.time()
  allowed = None if not docs else np.array([p['doc'] in docs for p in corpus.PAGES])
  if vecs is None:
    lex_order = [i for i in corpus.bm25(query, k=len(corpus.PAGES), per_doc=len(corpus.PAGES)) if allowed is None or allowed[i]]
    report({'stage': 'compare', 'status': 'done', 'ms': round((time.time() - t0) * 1000), 'vectors': 0, 'keyword': True, 'candidates': k})
    print(f'search: bm25 only, {round((time.time() - t_start) * 1000)} ms', flush=True)
    return {'hits': [{'page': i, 'relevance': None, 'passages': []} for i in lex_order[:k]], 'mode': 'bm25', 'facets': []}

  method = method if method in CANDIDATE_METHODS else config.SEARCH_CANDIDATES
  cand = candidates(query, vecs, allowed, method)
  view = idx['views']['chunk_title_s400']
  best_chunk = (view['vec'] @ vecs['qa'], view)  # the passages shown with each hit
  t_compare = round((time.time() - t0) * 1000)
  label = lambda i: f"{corpus.PAGES[i].get('ref') or pageunits.doc_title(corpus.PAGES[i]['title'])[:28]}, p. {corpus.PAGES[i]['page']}"
  used = [v for v in VIEWS] if method == 'rrf' else [v for v, _, _ in SEGMENTS if v in idx['views']]
  report({'stage': 'compare', 'status': 'done', 'ms': t_compare, 'vectors': int(sum(len(idx['views'][v]['vec']) for v in used)),
          'views': len(used), 'keyword': True, 'candidates': len(cand), 'method': method,
          'pages': [{'id': corpus.PAGES[i]['id'], 'label': label(i)} for i in cand]})  # in fused order, for the live view

  mode = 'embeddinggemma2'
  rel = {}
  t_rerank = 0
  if config.CF_ACCOUNT_ID and config.CF_API_TOKEN:
    report({'stage': 'rerank', 'status': 'running', 'model': 'clef-flash', 'judged': 0, 'of': len(cand)})
    t0 = time.time()
    futures = {_pool.submit(_judge, question, query, i): i for i in cand}
    for n, f in enumerate(concurrent.futures.as_completed(futures), 1):
      rel[futures[f]] = f.result()
      if n % 4 == 0 and n < len(cand):  # the scores so far, so the live view fills page by page
        report({'stage': 'rerank', 'status': 'running', 'model': 'clef-flash', 'judged': n, 'of': len(cand),
                'scores': {corpus.PAGES[i]['id']: round(v, 2) for i, v in rel.items() if v is not None}})
    t_rerank = round((time.time() - t0) * 1000)
    report({'stage': 'rerank', 'status': 'done', 'ms': t_rerank, 'model': 'clef-flash',
            'judged': sum(v is not None for v in rel.values()), 'of': len(cand),
            'scores': {corpus.PAGES[i]['id']: round(v, 2) for i, v in rel.items() if v is not None}})
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
  facets = {}
  for i in cand:
    f = facets.setdefault(corpus.PAGES[i]['doc'], {'doc': corpus.PAGES[i]['doc'], 'candidates': 0, 'likely_relevant': 0})
    f['candidates'] += 1
    f['likely_relevant'] += int((rel.get(i) or 0) >= 0.5)
  facets = sorted(facets.values(), key=lambda f: (-f['likely_relevant'], -f['candidates']))
  print(f"search: {round((time.time() - t_start) * 1000)} ms (embed {t_embed if vecs else '-'}, compare {t_compare}, "
        f"rerank {t_rerank}) mode {mode}, candidates {method}", flush=True)
  return {'hits': out, 'mode': mode, 'facets': facets}


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


# ---- the search page's semantic mode: DigUp's hybrid ranking (github.com/ARahim3/DigUp, Searcher.swift)
STOP = set('a an the of in on at to for with and or my me that this these those is are be by from as it its what which how'.split())
SEGMENTS = (('chunk_title_s400', 'passage', 'text'), ('page_plain', 'page', 'text'), (IMAGE_VIEW, 'image', 'image'))
Z_GATE, WORD_WEIGHT = 2.0, 2.0
# DigUp's kind hints (Searcher.kindHints): a query that names a kind of item ("...in a video") gives items of that kind
# +1, a soft boost, not a filter. Our kinds are kinds of guideline page, read from the structured page text (tables as
# HTML): (words in the query that ask for the kind, the test a page passes)
GRADED_ROW = re.compile(r'<td[^>]*>\s*(?:I|IIa|IIb|III|1|2a|2b|3)[a-z]?\s*</td>\s*<td[^>]*>\s*(?:A|B|C|B-R|B-NR|C-LD|C-EO)[a-z]?\s*</td>')
# the words in English, German, Spanish, Russian and Hindi, the languages of the app
TABLE = r'(\btables?\b(?! of contents)|\btabellen?|\btablas?\b|таблиц|तालिका)'  # a table of contents is not a table
RECOMMENDATION = r'(\brecommendations?\b|empfehlung|recomendaci|рекомендац|सिफ़ारिश|सिफारिश)'
PAGE_KINDS = {
  # any table
  'table': (TABLE, lambda t: '<table' in t),
  # a table of recommendations: at least three rows with a class of recommendation in one cell and a level of
  # evidence in the next (a table that only defines the classes has no level beside them). "Empfehlungstabelle" is
  # one word in German
  'recommendation table': (rf'{RECOMMENDATION}.*{TABLE}|{TABLE}.*{RECOMMENDATION}|empfehlungstabelle',
                           lambda t: len(GRADED_ROW.findall(t)) >= 3),
  'reference list': (r'\breferences?\b|bibliogra|\bcited (studies|papers|literature)\b|literaturverzeichnis|\breferencias\b'
                     r'|список литературы|संदर्भ सूची', lambda t: len(re.findall(r'et al\.', t)) >= 12),
  'abbreviations': (r'\babbreviations?\b|\bacronyms?\b|abkürzung|\babreviaturas?\b|\bsiglas\b|сокращени|संक्षिप्त',
                    lambda t: re.search(r'abbreviations', t, re.I) is not None and t.count('\n') > 20),
}


def page_kinds(text):
  return {k for k, (_, test) in PAGE_KINDS.items() if test(text)}


def kind_hints(query):
  return {k for k, (words, _) in PAGE_KINDS.items() if re.search(words, query, re.I)}


def word_coverage(query, words):
  """share of the query's words (stopwords out, the first six letters as a stem) found in each page's text"""
  terms = [w[:6] for w in re.findall(r'[a-z0-9]+', query.lower()) if w not in STOP and len(w) > 1]
  if not terms:
    return np.zeros(len(words))
  return np.array([sum(t in tx for t in terms) / len(terms) for tx in words])


USES = ('passage', 'page', 'image', 'words', 'kinds')  # what a semantic search may use; the search page toggles them


def digup(q, query, idx, hints=True, use=None):
  """every segment of a page (its 400-character passages, the whole page, the rendered page as an image) is compared
  with the query; the cosines are centred on the mean of their own modality (text, image) and divided by the spread of
  all centred scores (at least 0.02), so image and text similarities share one scale. A page counts with its best
  segment's z. It qualifies when that stands out (z >= 2) or the page holds at least half of the query's words; score
  = z + 2.0 * coverage^1.5, + 1 for each kind of page the query asks for (PAGE_KINDS), qualifying pages first. Measured against the passage-only ranking on the guideline QA test
  set (478 questions with their evidence page) and on queries for kinds of page (MASTERTHESIS,
  experiments/2026-10-18_semantic_page_search).
  use: the parts to rank with (USES), all by default; at least one kind of segment. With 'image' alone a page is ranked
  by its rendered image only, which shows what the image sees"""
  n = len(corpus.PAGES)
  use = set(USES) if use is None else set(use)
  parts = [(idx['views'][v], name, mod) for v, name, mod in SEGMENTS if v in idx['views'] and name in use]
  if not parts:
    parts = [(idx['views'][v], name, mod) for v, name, mod in SEGMENTS[:1]]
  sims = np.concatenate([v['vec'] @ q for v, _, _ in parts])
  owner = np.concatenate([v['owner'] for v, _, _ in parts])
  modality = np.concatenate([np.full(len(v['owner']), mod) for v, _, mod in parts])
  kind = np.concatenate([np.full(len(v['owner']), name) for v, name, _ in parts])
  centred = sims.astype(np.float64)
  for mod in set(modality):
    m = modality == mod
    centred[m] -= sims[m].mean()
  z = (centred - centred.mean()) / max(centred.std(), 0.02)
  order = np.lexsort((-z, owner))  # by page, its best segment first
  first = order[np.r_[True, owner[order][1:] != owner[order][:-1]]]
  best, seg = np.full(n, -np.inf), np.zeros(n, dtype=np.int64)
  best[owner[first]], seg[owner[first]] = z[first], first
  cov = word_coverage(query, idx['words']) if 'words' in use else np.zeros(n)
  score = np.maximum(best, 0) + WORD_WEIGHT * cov ** 1.5
  asked = kind_hints(query) if hints and 'kinds' in use else set()
  for k in asked:
    score = score + idx['kinds'][k]
  qualifies = (best >= Z_GATE) | (cov >= 0.5)
  return np.where(qualifies, score, score - 1e6), {'z': best, 'segment': seg, 'cosine': sims, 'kind': kind,
                                                   'words': cov, 'qualifies': qualifies, 'segments': len(sims),
                                                   'asked': sorted(asked), 'use': [u for u in USES if u in use]}


def semantic(query, k=10, use=None):
  """embedding search for the search page's semantic mode (no keyword index, no reranker): the query embedded with the
  retrieval prompt and ranked by digup(). Returns the pages with the segment that matched (passage, page or page
  image), its cosine and z, the share of the query's words on the page, the query vector folded into 48 cells, and
  timings"""
  query = query.strip()[:300]
  idx = _state['index']
  if not query or idx is None or not config.EMBED_URL:
    return {'available': False, 'hits': []}
  t0 = time.time()
  q = embed([PROMPTS['search'] + query])[0]
  t1 = time.time()
  score, why = digup(q, query, idx, use=use)
  top = [int(i) for i in np.argsort(-score, kind='stable')[:k]]
  t2 = time.time()
  passages = idx['views']['chunk_title_s400']
  hits = []
  for i in top:
    units = np.flatnonzero(passages['owner'] == i)
    u = int(units[np.argmax(passages['vec'][units] @ q)])
    seg = int(why['segment'][i])
    hits.append({**corpus.card(0, i, query, width=260), 'score': round(float(max(score[i], 0)), 3),
                 'similarity': round(float(why['cosine'][seg]), 3), 'z': round(float(why['z'][i]), 2),
                 'match': str(why['kind'][seg]), 'words': round(float(why['words'][i]), 2),
                 'qualified': bool(why['qualifies'][i]), 'kinds': [k for k in why['asked'] if idx['kinds'][k][i]],
                 'passage': idx['chunks'][u][:420]})
  cells = q.reshape(48, -1).mean(axis=1)
  counts = {name: int(len(idx['views'][v]['owner'])) for v, name, _ in SEGMENTS if v in idx['views'] and name in why['use']}
  return {'available': True, 'hits': hits, 'model': 'EmbeddingGemma 2', 'dims': int(q.shape[0]),
          'vectors': int(why['segments']), 'segments': counts, 'method': 'digup', 'asked': why['asked'], 'use': why['use'],
          'image_view': IMAGE_VIEW in idx['views'],
          'kind_pages': {k: int(idx['kinds'][k].sum()) for k in why['asked']}, 'prompt': PROMPTS['search'].strip(),
          'query_vector': [round(float(x), 4) for x in cells / (np.abs(cells).max() + 1e-9)],
          'ms': {'embed': round(1000 * (t1 - t0)), 'score': round(1000 * (t2 - t1))}}

"""The guideline library: 2,780 OCR'd pages, a BM25 index over them, and helpers to point at a page.

BM25 in 40 lines beats a vector db here. queries are clinical terms, the pages use the same terms.
"""
import gzip
import html
import json
import math
import re
from collections import Counter, defaultdict

import config
import textclean

TOKEN = re.compile(r'[a-zäöüß0-9]+(?:[-/][a-zäöüß0-9]+)*', re.I)
STOP = set(('the a an and or of in on to for with by is are be as at from that this these those it its was were not no can may '
            'should than then there their which who whom what when where how also into per vs via der die das und oder ein eine '
            'einer eines in im auf zu für mit von bei ist sind nicht kein keine den dem des zur zum als wie wenn sich auch').split())


def tokens(text):
  out = []
  for t in TOKEN.findall(text.lower()):
    if t in STOP or len(t) < 2:
      continue
    out.append(t)
    if '-' in t or '/' in t:  # 'beta-blocker' also matches 'blocker'
      out.extend(x for x in re.split(r'[-/]', t) if len(x) > 1 and x not in STOP)
  return out


def plain(text):
  text = textclean.strip_tags(text)
  text = re.sub(r'!\[[^\]]*\]\([^)]*\)', ' ', text)
  return re.sub(r'[ \t]+', ' ', text)


with gzip.open(config.DATA / 'corpus.json.gz', 'rt', encoding='utf-8') as fh:
  PAGES = json.load(fh)['pages']
for p in PAGES:
  p['plain'] = plain(p['text'])
PAGE_BY_ID = {p['id']: p for p in PAGES}
PAGE_INDEX = {p['id']: i for i, p in enumerate(PAGES)}

# ---- bm25 index
_postings, _lengths = defaultdict(list), []
for i, p in enumerate(PAGES):
  tf = Counter(tokens(p['title'] + ' ' + p['plain']))
  _lengths.append(sum(tf.values()))
  for t, c in tf.items():
    _postings[t].append((i, c))
_avg = sum(_lengths) / max(1, len(_lengths))


def bm25(query, k=5, k1=1.4, b=0.75, per_doc=2):
  """page indices, best first, at most per_doc pages from one document"""
  scores = defaultdict(float)
  for t in set(tokens(query)):
    post = _postings.get(t)
    if not post:
      continue
    idf = math.log(1 + (len(PAGES) - len(post) + .5) / (len(post) + .5))
    for i, c in post:
      scores[i] += idf * c * (k1 + 1) / (c + k1 * (1 - b + b * _lengths[i] / _avg))
  out, seen = [], Counter()
  for i, _ in sorted(scores.items(), key=lambda x: -x[1]):
    if seen[PAGES[i]['doc']] >= per_doc:
      continue
    seen[PAGES[i]['doc']] += 1
    out.append(i)
    if len(out) >= k:
      break
  return out


def snippet(text, query, width=900):
  """the densest window of query hits, so the model sees the relevant part of a long page"""
  terms = set(tokens(query))
  words = list(re.finditer(r'\S+', text))
  if not words:
    return ''
  hits = [j for j, m in enumerate(words) if set(tokens(m.group())) & terms]
  start = 0
  if hits:
    best = max(range(len(hits)), key=lambda a: sum(1 for h in hits[a:] if h - hits[a] < 140))
    start = max(0, hits[best] - 25)
  chunk = text[words[start].start():][:width]
  return ('… ' if start else '') + re.sub(r'\s+', ' ', chunk).strip() + ' …'


def card(n, i, query=None, width=900):
  """one numbered source as the ui shows it"""
  p = PAGES[i]
  return {'n': n, 'page_id': p['id'], 'title': p['title'], 'ref': p.get('ref'), 'page': p['page'],
          'snippet': snippet(p['plain'], query, width) if query else None}


# ---- library view
_DE = set('und der die das nicht mit für bei ist sind eine einer wird werden sie ihre können sollte'.split())
_EN = set('and the of with for is are be should patients recommended treatment which this'.split())
_library = None


def library():
  """every document once: title, author-year, language, pages"""
  global _library
  if _library is None:
    docs = {}
    for p in PAGES:
      d = docs.setdefault(p['doc'], {'doc': p['doc'], 'title': p['title'], 'ref': p.get('ref'), 'pages': [], 'de': 0, 'en': 0})
      d['pages'].append([p['id'], p['page']])
      text = p['plain'].lower()
      words = re.findall(r'[a-zäöüß]+', text)
      d['de'] += sum(w in _DE for w in words) + 3 * len(re.findall(r'[äöüß]', text))
      d['en'] += sum(w in _EN for w in words)
    for d in docs.values():
      d['pages'].sort(key=lambda x: x[1])
      first = PAGE_BY_ID[d['pages'][0][0]]['plain']
      d['preview'] = re.sub(r'\s+', ' ', re.sub(r'[#*|_>-]+', ' ', first)).strip()[:180]
      d['lang'] = 'DE' if d.pop('de') > d.pop('en') else 'EN'
    _library = {'documents': sorted(docs.values(), key=lambda d: (-len(d['pages']), d['title'].lower())), 'pages': len(PAGES)}
  return _library



# ---- "look, here": the page and the table a recommendation was extracted from
_squash = lambda t: re.sub(r'[^a-z0-9]+', '', html.unescape(textclean.strip_tags(t)).lower())
_squashed, _toks = {}, {}
PAGES_OF_DOC = {}
for _i, _p in enumerate(PAGES):
  PAGES_OF_DOC.setdefault(_p['doc'], []).append(_i)
MIN_RECALL = 0.6  # share of a text's words a page or table must hold to count as its place when no exact match exists


def _words(text):
  """words of a text, with latex commands dropped: the graph writes \\leq, \\mu where the page has ≤, μ"""
  return set(re.findall(r'[a-z0-9]{2,}', html.unescape(re.sub(r'\\[a-zA-Z]+', ' ', textclean.strip_tags(text))).lower()))


def _page_squash(i):
  if i not in _squashed:
    _squashed[i] = _squash(PAGES[i]['text'])
  return _squashed[i]


def _page_words(i):
  if i not in _toks:
    _toks[i] = _words(PAGES[i]['text'])
  return _toks[i]


def _recall(words, other):
  return len(words & other) / len(words) if words else 0.0


def holds(i, text):
  """does page i hold the text: verbatim (letters and digits), else most of its words"""
  key = _squash(text)[:70]
  return bool(key and key in _page_squash(i)) or _recall(_words(text), _page_words(i)) >= MIN_RECALL


def table_for(page_text, text):
  """the original <table> on a page that holds the text: verbatim first, else the one with most of its words"""
  key, words = _squash(text)[:70], _words(text)
  tables = [m.group(0) for m in re.finditer(r'<table\b.*?</table>', page_text, flags=re.S | re.I)]
  exact = next((t for t in tables if key and key in _squash(t)), None)
  if exact or not tables:
    return exact
  score, best = max((_recall(words, _words(t)), t) for t in tables)
  return best if score >= MIN_RECALL else None


def source(rec, siblings=()):
  """where a recommendation comes from: (page index, original <table> html or None, how it was found).
  graph       the knowledge graph records the physical blocks of its semantic unit ("pb_<doc sha256>_p<page>_g<group>");
              their page ids are the corpus page ids. of those pages the one that holds the text
  text match  the graph records no block, only the document (doc). the page of that document that holds the text;
              when several do (a table title also stands in the table of contents), the one that holds most of the
              other texts of the same semantic unit (siblings), since a semantic unit is one physical structure
  verbatim text alone is not enough to place a recommendation: guidelines repeat them in summaries and contents"""
  text = rec['text']
  pages = [PAGE_INDEX[b[3:].rsplit('_g', 1)[0]] for b in rec.get('src', []) if b[3:].rsplit('_g', 1)[0] in PAGE_INDEX]
  if pages:
    key = _squash(text)[:70]
    best = next((i for i in pages if key and key in _page_squash(i)), None)
    best = best if best is not None else next((i for i in pages if holds(i, text)), pages[0])
    return best, table_for(PAGES[best]['text'], text), 'graph'
  key, doc = _squash(text)[:70], PAGES_OF_DOC.get(rec.get('doc'), [])
  candidates = [i for i in doc if key and key in _page_squash(i)] or [i for i in doc if holds(i, text)]  # verbatim first
  if not candidates:
    return None, None, None
  best = max(candidates, key=lambda i: (sum(holds(i, s) for s in siblings), -i))  # ties: the earlier page
  return best, table_for(PAGES[best]['text'], text), 'text match'

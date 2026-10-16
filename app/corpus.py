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
  text = re.sub(r'<[^>]+>', ' ', text)
  text = re.sub(r'!\[[^\]]*\]\([^)]*\)', ' ', text)
  return re.sub(r'[ \t]+', ' ', text)


with gzip.open(config.DATA / 'corpus.json.gz', 'rt', encoding='utf-8') as fh:
  PAGES = json.load(fh)['pages']
for p in PAGES:
  p['plain'] = plain(p['text'])
PAGE_BY_ID = {p['id']: p for p in PAGES}

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


# ---- "look, here": find the page and the table a sentence was extracted from
_squash = lambda t: re.sub(r'[^a-z0-9]+', '', html.unescape(re.sub(r'<[^>]+>', ' ', t)).lower())
_squashed = {}


def locate(text, ref):
  """page index and original <table> html that contain `text`, searched within guideline `ref`"""
  key = _squash(text)[:70]
  hits = []
  for i, p in enumerate(PAGES):
    if p.get('ref') != ref:
      continue
    if i not in _squashed:
      _squashed[i] = _squash(p['text'])
    if key and key in _squashed[i]:
      hits.append(i)
  # a table title also stands in the table of contents, which comes first. the page where the text sits inside a
  # table is the source; then any page that is not a table of contents; then the first page that has it
  for i in hits:
    table = _table_with(PAGES[i]['text'], key)
    if table:
      return i, table
  best = next((i for i in hits if not _is_contents(i)), hits[0] if hits else None)
  if best is None:  # ocr noise broke the exact match. fall back to retrieval within the same guideline
    best = next((i for i in bm25(text, k=40, per_doc=99) if PAGES[i].get('ref') == ref), None)
  if best is None:
    return None, None
  return best, _table_with(PAGES[best]['text'], key)


def _is_contents(i):
  """a table of contents: most lines end in a page number ("... ejection fraction 13")"""
  lines = [l.strip() for l in PAGES[i]['text'].splitlines() if len(l.strip()) > 12]
  numbered = sum(1 for l in lines if re.search(r'[A-Za-z)\].]\s*\.*\s+\d{1,3}$', l))
  return len(lines) >= 6 and numbered >= 0.4 * len(lines)


def _table_with(page_text, key):
  return next((m.group(0) for m in re.finditer(r'<table\b.*?</table>', page_text, flags=re.S | re.I)
               if key and key in _squash(m.group(0))), None)

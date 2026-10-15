"""Find a text on the original page: which words of the A4 page it is, and where they sit.

the corpus text is an OCR transcription without coordinates. data/page_words.json.gz (pipeline/build_page_words.py)
holds every word of every page with its box, from the PDF text layer or, for scans, from OCR. a quote is matched
to those words token by token, tolerant to line breaks, hyphenation, punctuation and small OCR differences, and
comes back as one rectangle per line, in fractions of the page, ready to draw over the page image.

  exact: a verbatim quote (luna's call evidence, the passages of lino's check). at least 60 % of its words in order
  near:  a sentence that paraphrases the page (luna's answer citing it). the content words only, at least 35 %;
         the ui says "closest passage", never "quote"
"""
import base64
import gzip
import json
import re
import threading
from array import array
from difflib import SequenceMatcher

import config
import corpus

PATH = config.DATA / 'page_words.jsonl.gz'  # one page per line: read line by line, never the whole file at once
TOKEN = re.compile(r'[0-9a-zäöüßàâæçéèêëîïôœùûüÿáíóúñа-яёऀ-ॿ]+')
STOP = set('the a an and or of in on to for with by is are be as at from that this these those it its was were not no can may '
           'should than then there their which who what when where how also into per der die das und oder ein eine einer eines im '
           'auf zu für mit von bei ist sind nicht den dem des zur zum als wie wenn sich auch werden wird'.split())
_pages, _lock = None, threading.Lock()


def _load():
  """page id -> (src, words joined by \x1f, boxes as uint16 in 1/scale of the page). loaded once, on first use"""
  global _pages
  with _lock:
    if _pages is None:
      if not PATH.exists():
        _pages = {}
        return
      out = {}
      with gzip.open(PATH, 'rt', encoding='utf-8') as fh:
        globals()['SCALE'] = json.loads(fh.readline()).get('scale', 10000)
        for line in fh:
          p = json.loads(line)
          words = p['words'] if isinstance(p['words'], str) else '\x1f'.join(p['words'])  # split per lookup, not up front
          boxes = array('H', base64.b64decode(p['boxes'])) if isinstance(p['boxes'], str) else array('H', p['boxes'])
          out[p['id']] = (p['src'], words, boxes)
      _pages = out


def available():
  _load()
  return bool(_pages)


def _tokens(words, content):
  """(token, word index) for a page, a hyphen at a line end joins the two halves"""
  out, i = [], 0
  while i < len(words):
    w = words[i].lower()
    if w.endswith('-') and i + 1 < len(words) and len(w) > 2:
      joined = TOKEN.findall(w[:-1] + words[i + 1].lower())
      out += [(t, i) for t in joined[:1]] + [(t, i + 1) for t in joined[1:]]
      i += 2
      continue
    out += [(t, i) for t in TOKEN.findall(w)]
    i += 1
  return [(t, k) for t, k in out if not content or (len(t) > 3 and t not in STOP)]


def locate(page_id, text, mode='exact', follow=True):
  """where `text` is on page `page_id`. a quote that runs over the page edge is followed onto the neighbouring page:
  the result then carries `next` or `prev` with that page's rectangles"""
  _load()
  page = _pages.get(page_id)
  if not page or not text:
    return None
  src, words, boxes = page
  words = words.split('\x1f')
  content = mode == 'near'
  P = _tokens(words, content)
  Q = [t for t in TOKEN.findall(str(text).lower()) if not content or (len(t) > 3 and t not in STOP)][:200]
  if len(Q) < 3 or not P:
    return None
  if content:
    return _near(src, boxes, P, Q)
  blocks = [b for b in SequenceMatcher(None, [t for t, _ in P], Q, autojunk=False).get_matching_blocks() if b.size]
  if not blocks:
    return None
  # the densest run of matches whose page span is not much longer than the quote
  span, best, lo = len(Q) * 1.4 + 8, (0, 0, 0), 0
  for hi in range(len(blocks)):
    while blocks[hi].a + blocks[hi].size - blocks[lo].a > span:
      lo += 1
    hit = sum(b.size for b in blocks[lo:hi + 1])
    if hit > best[0]:
      best = (hit, lo, hi)
  hit, lo, hi = best
  first, last = P[blocks[lo].a][1], P[blocks[hi].a + blocks[hi].size - 1][1]
  q_first, q_last = blocks[lo].b, blocks[hi].b + blocks[hi].size
  out = {'src': src, **_shape(boxes, first, last)}
  _table_row(page_id, text, words, boxes, out)
  covered = hit
  # the quote goes on past the page edge: look for the rest on the next (or the previous) page
  if follow and len(Q) - q_last >= 4 and last >= len(words) - EDGE:
    nxt = _neighbour(page_id, 1)
    r = nxt and locate(nxt, ' '.join(Q[q_last:]), 'exact', follow=False)
    if r:
      out['next'] = {'page_id': nxt, 'page': int(nxt.rsplit('_p', 1)[1]), **{k: r[k] for k in ('rects', 'kind', 'box')}}
      covered += round(r['cover'] * (len(Q) - q_last))
  if follow and q_first >= 4 and first <= EDGE:
    prv = _neighbour(page_id, -1)
    r = prv and locate(prv, ' '.join(Q[:q_first]), 'exact', follow=False)
    if r:
      out['prev'] = {'page_id': prv, 'page': int(prv.rsplit('_p', 1)[1]), **{k: r[k] for k in ('rects', 'kind', 'box')}}
      covered += round(r['cover'] * q_first)
  out['cover'] = round(covered / len(Q), 2)
  return out if out['cover'] >= 0.6 else None


EDGE = 30  # a match this close to the first or last word of a page touches the page edge


def _neighbour(page_id, step):
  doc, n = page_id.rsplit('_p', 1)
  other = f'{doc}_p{int(n) + step:04d}'
  return other if other in _pages else None


def _near(src, boxes, P, Q):
  """a paraphrase keeps the content words, not their order: the window of the page holding most of them"""
  want = set(Q)
  width = max(25, int(len(want) * 2.2))
  best, lo, counts, have = (0, 0, 0), 0, {}, 0
  for hi, (t, _) in enumerate(P):
    if t in want:
      counts[t] = counts.get(t, 0) + 1
      have += counts[t] == 1
    while hi - lo >= width:
      u = P[lo][0]
      if u in want:
        counts[u] -= 1
        have -= counts[u] == 0
      lo += 1
    if have > best[0]:
      best = (have, lo, hi)
  have, lo, hi = best
  cover = have / len(want)
  if cover < 0.35:
    return None
  inside = [k for k in range(lo, hi + 1) if P[k][0] in want]  # trim the window to its first and last matching word
  return {'src': src, 'cover': round(cover, 2), **_shape(boxes, P[inside[0]][1], P[inside[-1]][1])}


def _shape(boxes, first, last):
  """the words first..last as one rectangle per line, in fractions of the page, and what they are: prose, or a table
  row whose cells sit apart on the same line. a table also gets one box around all of it, which the viewer draws"""
  rects, gaps = [], 0
  for i in range(first, last + 1):
    x0, y0, x1, y1 = boxes[4 * i:4 * i + 4]
    if rects:
      r = rects[-1]
      same_line = abs((y0 + y1) / 2 - (r[1] + r[3]) / 2) < 0.6 * max(1, min(y1 - y0, r[3] - r[1])) and x0 >= r[0] - 50
      if same_line:
        gaps += x0 - r[2] > CELL_GAP
        r[0], r[1], r[2], r[3] = min(r[0], x0), min(r[1], y0), max(r[2], x1), max(r[3], y1)
        continue
    rects.append([x0, y0, x1, y1])
  s = globals().get('SCALE', 10000)
  rects = [[round(v / s, 4) for v in r] for r in rects]
  box = [min(r[0] for r in rects), min(r[1] for r in rects), max(r[2] for r in rects), max(r[3] for r in rects)]
  return {'rects': rects, 'kind': 'table' if gaps >= 2 else 'prose', 'box': box}


_TR = re.compile(r'<tr\b.*?</tr>', re.S | re.I)
_TD = re.compile(r'<t[dh]\b[^>]*>(.*?)</t[dh]>', re.S | re.I)
_squash = lambda t: re.sub(r'[^a-z0-9]+', '', re.sub(r'<[^>]+>', ' ', t).lower())


def _table_row(page_id, text, words, boxes, out):
  """the quote sits in a table row of the transcription: find the row's other cells (class, level of evidence...)
  on the same height of the original page, and mark the row as one band with those cells singled out"""
  page = corpus.PAGE_BY_ID.get(page_id)
  key = _squash(text)[:60]
  if not page or '<t' not in page['text'] or len(key) < 20:
    return
  row = next((r for r in _TR.findall(page['text']) if key in _squash(r)), None)
  if not row:
    return
  cells = [_squash(c) for c in _TD.findall(row)]
  short = {c for c in cells if c in GRADES}  # only the class and level cells are singled out: I, IIa, B-NR, C-LD...
  sc = globals().get('SCALE', 10000)
  x0, y0, x1, y1 = (v * sc for v in out['box'])
  line = max(r[3] - r[1] for r in out['rects']) * sc
  found = []
  for i, w in enumerate(words):
    bx = boxes[4 * i:4 * i + 4]
    mid = (bx[1] + bx[3]) / 2
    if _squash(w) in short and y0 - line <= mid <= y1 + line and (bx[2] < x0 or bx[0] > x1):
      found.append([round(v / sc, 4) for v in bx])
  out['kind'] = 'table'
  if found:
    out['cells'] = found
    out['box'] = [min(out['box'][0], *(f[0] for f in found)), min(out['box'][1], *(f[1] for f in found)),
                  max(out['box'][2], *(f[2] for f in found)), max(out['box'][3], *(f[3] for f in found))]


GRADES = {'i', 'ii', 'iia', 'iib', 'iii', '1', '2a', '2b', '3', 'a', 'b', 'c', 'br', 'bnr', 'cld', 'ceo'}
CELL_GAP = 350  # a gap wider than 3.5 % of the page between two words of one line: they sit in different table cells

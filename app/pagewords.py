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


def locate(page_id, text, mode='exact'):
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
  span, best, lo = len(Q) * (2.2 if content else 1.4) + 8, (0, 0, 0), 0
  for hi in range(len(blocks)):
    while blocks[hi].a + blocks[hi].size - blocks[lo].a > span:
      lo += 1
    hit = sum(b.size for b in blocks[lo:hi + 1])
    if hit > best[0]:
      best = (hit, lo, hi)
  hit, lo, hi = best
  cover = hit / len(Q)
  if cover < (0.35 if content else 0.6):
    return None
  first, last = P[blocks[lo].a][1], P[blocks[hi].a + blocks[hi].size - 1][1]
  return {'src': src, 'cover': round(cover, 2), 'rects': _rects(boxes, first, last)}


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
  return {'src': src, 'cover': round(cover, 2), 'rects': _rects(boxes, P[inside[0]][1], P[inside[-1]][1])}


def _rects(boxes, first, last):
  """one rectangle per line for words first..last, in fractions of the page"""
  rects = []
  for i in range(first, last + 1):
    x0, y0, x1, y1 = boxes[4 * i:4 * i + 4]
    if rects:
      r = rects[-1]
      same_line = abs((y0 + y1) / 2 - (r[1] + r[3]) / 2) < 0.6 * max(1, min(y1 - y0, r[3] - r[1])) and x0 >= r[0] - 50
      if same_line:
        r[0], r[1], r[2], r[3] = min(r[0], x0), min(r[1], y0), max(r[2], x1), max(r[3], y1)
        continue
    rects.append([x0, y0, x1, y1])
  s = globals().get('SCALE', 10000)
  return [[round(v / s, 4) for v in r] for r in rects]

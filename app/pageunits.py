"""Text units of a guideline page: the page with its tables written as rows, and the chunks the search index embeds.

The OCR pages hold tables as HTML; corpus.plain strips the tags, so a recommendation and its class and level end up
as loose tokens. table_text writes each body row as "header: cell; header: cell", so the class and level stay with
their recommendation. page_units must stay identical to the retrieval study that built and evaluated the index
(MASTERTHESIS-HEART, projects/03_reasoning_aware_guideline_retrieval/experiments/2026-10-17_embeddinggemma2_retrieval/units.py):
search.py checks the unit count of every view against the index and refuses an index that does not match.
"""
import html
import re
import textclean
from html.parser import HTMLParser

HEADING = re.compile(r'^\s{0,3}#{1,6}\s+(.+?)\s*$', re.M)


class _Table(HTMLParser):
  def __init__(self):
    super().__init__()
    self.rows, self.row, self.cell, self.in_cell, self.head = [], None, [], False, False

  def handle_starttag(self, tag, attrs):
    if tag == 'tr':
      self.row = []
    elif tag in ('td', 'th'):
      self.in_cell, self.cell, self.head = True, [], self.head or tag == 'th'
    elif tag == 'br' and self.in_cell:
      self.cell.append(' ')
    elif tag == 'sup' and self.in_cell:
      self.cell.append(' ')

  def handle_endtag(self, tag):
    if tag in ('td', 'th') and self.row is not None:
      self.row.append((tag == 'th', ' '.join(''.join(self.cell).split())))
      self.in_cell = False
    elif tag == 'tr' and self.row is not None:
      self.rows.append(self.row)
      self.row = None

  def handle_data(self, data):
    if self.in_cell:
      self.cell.append(data)


def _render_table(markup):
  p = _Table()
  p.feed(markup)
  header, lines = None, []
  for row in p.rows:
    cells = [c for _, c in row]
    if header is None and (all(h for h, _ in row) or not lines and any(h for h, _ in row)):
      header = cells
      continue
    if not any(cells):
      continue
    if header and len(header) == len(cells):
      parts = [(f'{h}: {c}' if h else c) for h, c in zip(header, cells) if c]
    else:
      parts = [c for c in cells if c]
    lines.append('; '.join(parts))
  return '\n'.join(lines)


def table_text(text):
  """the page with every <table> replaced by one line per body row; other tags removed"""
  out = re.sub(r'<table\b.*?</table>', lambda m: '\n' + _render_table(m.group(0)) + '\n', text, flags=re.S | re.I)
  out = re.sub(r'<sup>(.*?)</sup>', r' \1', out, flags=re.S | re.I)
  out = textclean.strip_tags(out)
  out = html.unescape(out)
  out = re.sub(r'[ \t]+', ' ', out)
  return re.sub(r'\n\s*\n+', '\n\n', out).strip()


def chunks(text, size=1200, overlap=150):
  """(start, end) character spans of ~size characters, cut at line breaks where possible (a table row stays whole)"""
  spans, start, n = [], 0, len(text)
  while start < n:
    end = min(n, start + size)
    if end < n:
      cut = text.rfind('\n', start + size // 2, end)
      end = cut if cut > start else (text.rfind(' ', start + size // 2, end) if text.rfind(' ', start + size // 2, end) > start else end)
    spans.append((start, end))
    if end >= n:
      break
    start = max(end - overlap, start + 1)
    nl = text.find('\n', start, end)
    if 0 <= nl < end:  # start on a line where the overlap allows
      start = nl + 1
  return spans


def heading_at(text, pos):
  """the last markdown heading before pos, or None"""
  last = None
  for m in HEADING.finditer(text, 0, pos):
    last = m.group(1).strip('# ').strip()
  return last


def doc_title(title):
  return ' '.join(str(title or '').split())[:200] or 'none'


def doc(title, text):
  return f'title: {title} | text: {text}'


def size_of(method, default=1200):
  """chunk size from a method name suffix: chunk_title_s600 -> 600"""
  m = re.search(r'_s(\d+)$', method)
  return int(m.group(1)) if m else default


_TABLE_TEXT = {}


def _page_text(p):
  """table_text of a page, computed once per page text"""
  key = p.get('id') or id(p)
  if key not in _TABLE_TEXT:
    _TABLE_TEXT[key] = table_text(p['text'])
  return _TABLE_TEXT[key]


def page_units(pages, method):
  """[(page index, text to embed)]"""
  out = []
  size = size_of(method)
  for i, p in enumerate(pages):
    title = doc_title(p['title'])
    if method == 'page_plain':
      out.append((i, doc(title, p['plain'])))
      continue
    t = _page_text(p)
    if method == 'page_table':
      out.append((i, doc(title, t)))
      continue
    for a, b in chunks(t, size=size, overlap=size // 8):
      head = heading_at(t, a + 1) if method.startswith('chunk_ctx') else None
      out.append((i, doc(f'{title} — {head}' if head else title, t[a:b])))
  return out

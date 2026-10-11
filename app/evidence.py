"""Evidence cards for Luna's video calls: what she just said, backed by verbatim quotes from the guideline pages.

  1. the llm decides if the utterance carries guideline content at all. small talk, greetings, questions back: no card.
     if it does, it names the clinical search terms in english, so german, russian or hindi speech finds english pages
  2. retrieve the pages for those terms (bm25)
  3. the llm picks supporting sentences from those pages, copied verbatim
  4. we keep a quote only if it really is in the page text. character for character, ignoring spaces and punctuation
  5. each kept quote gets its guideline, page, the original html table it sits in, and class and level of evidence
     when the knowledge graph knows that recommendation

so the right side of the call only ever shows text that is in the sources. the llm chooses, it doesn't write the evidence.
"""
import json
import re

import config
import corpus
import kg
import llm

PAGES_PER_UTTERANCE = 4
GATE = (
  "You read what a medical assistant just said in a video call. Return JSON only.\n"
  "If it states no medical or guideline content (small talk, a greeting, a question back to the user, an offer to help, "
  "a general remark), return {\"relevant\": false}. Otherwise return {\"relevant\": true, \"query\": \"<the key clinical "
  "terms in English, e.g. aspirin dose secondary prevention coronary artery disease>\"}."
)
SYSTEM = (
  "You check what a medical assistant just said in a video call against guideline pages.\n"
  "Return JSON only.\n"
  "If the speech states no guideline content (small talk, a greeting, a question back to the user, an offer to help, "
  "a general remark), return {\"relevant\": false}.\n"
  "Otherwise return {\"relevant\": true, \"title\": \"<max 6 words>\", \"points\": [{\"claim\": \"<max 14 words>\", "
  "\"source\": <n>, \"quote\": \"<one or two sentences copied exactly from source n>\"}]} with 1 to 3 points.\n"
  "Rules: a quote must be copied character for character from the given source, never paraphrased or translated. "
  "Prefer recommendation sentences, doses, thresholds and conditions. Pick only quotes that support what was said. "
  "Never quote abbreviation lists, figure or table captions, footnotes or reference lists. "
  "If no source supports the speech, return {\"relevant\": false}. "
  "Write title and claims in the language of the speech."
)

_squash = lambda t: re.sub(r'[^a-z0-9]+', '', re.sub(r'<[^>]+>', ' ', t or '').lower())
# recommendation text -> id, to attach class and level of evidence to a quote
_recs = [(_squash(r['text'])[:80], rid) for rid, r in kg.RECS.items() if len(r['text']) > 60]


def _grade(quote_key):
  for key, rid in _recs:
    if key and (key in quote_key or quote_key[:80] in key):
      r = kg.RECS[rid]
      if r['class'] or r['loe']:
        return kg.grade(r)
  return ''


CLASS = re.compile(r'^(I|IIa|IIb|III)$')
LEVEL = re.compile(r'^(A|B|C|B-R|B-NR|C-LD|C-EO)$')
ABBREVIATIONS = re.compile(r'^(?:[A-Za-z0-9-]{1,10}, [^;]{3,80}; ){2,}')


def _row_grade(table, quote_key):
  """class and level from the table row that holds the quote. esc tables put them in their own cells"""
  for row in re.findall(r'<tr\b.*?</tr>', table or '', flags=re.S | re.I):
    if quote_key[:60] in _squash(row):
      cells = [re.sub(r'<[^>]+>', '', c).strip() for c in re.findall(r'<t[dh]\b.*?</t[dh]>', row, flags=re.S | re.I)]
      cls = next((c for c in cells if CLASS.match(c)), None)
      loe = next((c for c in cells if LEVEL.match(c)), None)
      return ' · '.join(x for x in (cls and 'Class ' + cls, loe and 'LoE ' + loe) if x)
  return ''


def _table(page, quote_key):
  for m in re.finditer(r'<table\b.*?</table>', page['text'], flags=re.S | re.I):
    if quote_key[:60] in _squash(m.group(0)):
      return m.group(0)
  return None


def _json(messages, max_tokens):
  raw, _ = llm.complete(messages, max_tokens=max_tokens, response_format={'type': 'json_object'})
  return json.loads(raw[raw.index('{'):raw.rindex('}') + 1])


def board(text):
  text = str(text)[:2500]
  if not config.LLM_API_KEY or len(text) < 60:
    return {'relevant': False}
  try:
    gate = _json([{'role': 'system', 'content': GATE}, {'role': 'user', 'content': text}], 120)
  except Exception:
    return {'relevant': False}
  if not gate.get('relevant'):
    return {'relevant': False}
  query = str(gate.get('query') or '')
  # english terms first: mixing in german or russian words pulls in the wrong pages. the speech itself fills up
  hits = list(dict.fromkeys(corpus.bm25(query, k=PAGES_PER_UTTERANCE - 1) + corpus.bm25(text, k=2)))[:PAGES_PER_UTTERANCE]
  if not hits:
    return {'relevant': False}
  sources = '\n\n'.join(f"[{n}] {corpus.PAGES[i].get('ref') or corpus.PAGES[i]['title']}, page {corpus.PAGES[i]['page']}\n"
                        f"{corpus.snippet(corpus.PAGES[i]['plain'], query or text, 1800)}" for n, i in enumerate(hits, 1))
  messages = [{'role': 'system', 'content': SYSTEM}, {'role': 'user', 'content': f'Speech:\n{text}\n\nSources:\n{sources}'}]
  try:
    out = _json(messages, 900)
  except Exception:
    return {'relevant': False}
  if not out.get('relevant'):
    return {'relevant': False}
  points, seen = [], set()
  for p in out.get('points') or []:
    try:
      page = corpus.PAGES[hits[int(p['source']) - 1]]
    except (KeyError, ValueError, IndexError, TypeError):
      continue
    quote, key = str(p.get('quote', '')).strip(), _squash(p.get('quote', ''))
    if len(key) < 25 or key not in _squash(page['plain']) or key in seen or ABBREVIATIONS.match(quote):
      continue  # not verbatim in the source (or a legend): dropped, never shown
    seen.add(key)
    table = _table(page, key)
    points.append({'claim': str(p.get('claim', ''))[:160], 'quote': quote[:600], 'page_id': page['id'], 'ref': page.get('ref'),
                   'title': page['title'], 'page': page['page'], 'grade': _row_grade(table, key) or _grade(key), 'table_html': table})
  if not points:
    return {'relevant': False}
  return {'relevant': True, 'title': str(out.get('title', ''))[:60], 'points': points}

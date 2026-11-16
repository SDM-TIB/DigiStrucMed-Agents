"""Graph Walker: Walker V7 run live, every step streamed as an event for the /graph-walker page.

The walker (app/vendor/walker, synced from the thesis repo by scripts/sync_walker.py) answers a question by walking the
proposition graph: guidelines -> topics -> statements, each level scored by decision models (clef-flash, then clef).
No language model reads or writes anything until the last step, when DeepSeek V4.1 Flash writes the answer from the
statements the walk selected, each with its verbatim source quote.

Data (app/data/walker, built by 2026-11-05_proposition_graph/export_app_pack.py):
  layout.json          the picture: every guideline, topic and statement with a fixed position, UMLS concept nodes
  work/props_all.jsonl, work/topics.jsonl   what the walker reads (walker.v7.PropStore)
The 28.10 knowledge graph (recommendation nodes, page text for the index) is read over SPARQL from GRAPH_ENDPOINT.

Events (one JSON object each, see static/js/walker.js for how they are shown):
  loading, question, scores, guidelines, taken, evidence, writing, answer, done, error
"""
import gzip
import json
import os
import queue
import sys
import threading
import time

from pathlib import Path

import config
import storage

VENDOR = config.ROOT / 'vendor'
DATA = config.DATA / 'walker'  # local dev; on Cloud Run the pack comes from the page bucket (walker/...) into /tmp/walker
FILES = ['layout.json', 'work/props_all.jsonl', 'work/topics.jsonl', 'work/de_props.jsonl']
ON_RUN = bool(os.environ.get('K_SERVICE'))
sys.path.insert(0, str(VENDOR))
os.environ.setdefault('WRITER_EFFORT', 'none')  # the writer answers without reasoning (Walker V7 configuration)
# model calls of this process at the same time (walker.models, read at import). a level sends up to a dozen scoring
# requests; with the walker's default of 3 they queue, and a slow model costs the walk several times its delay. the
# calls and their results are the same, only side by side; a throttled call (HTTP 429) is retried with a pause
os.environ.setdefault('MODEL_SLOTS', '8')
os.environ.setdefault('SPARQL_CACHE', '/tmp/walker_sparql' if ON_RUN else str(DATA / 'sparql_cache'))
os.environ.setdefault('GRAPH_ENDPOINT', config.GRAPH_ENDPOINT)

_lock = threading.Lock()
_slots = threading.Semaphore(3)  # walks at the same time
_state = {'ready': False, 'error': None, 'started': False}
_gz = {}


def available():
  return (VENDOR / 'walker').is_dir() and ((DATA / 'layout.json').is_file() or bool(config.BUCKET))


def _ensure(names=FILES):
  """the data pack: local when present, else downloaded once from the page bucket into /tmp/walker"""
  global DATA
  if (DATA / 'layout.json').is_file() and all((DATA / n).is_file() for n in names):
    os.environ.setdefault('V7_PROPS', str(DATA))
    return DATA
  tmp = Path('/tmp/walker')
  for n in names:
    dest = tmp / n
    if not dest.is_file():
      raw = storage.get_large('walker/' + n, timeout=600)
      if raw is None:
        raise RuntimeError(f'walker data missing in the bucket: walker/{n}')
      dest.parent.mkdir(parents=True, exist_ok=True)
      dest.write_bytes(raw)
  if all((tmp / n).is_file() for n in FILES):
    DATA = tmp
  os.environ['V7_PROPS'] = str(tmp)
  return tmp


def layout_gz():
  """layout.json gzipped once, served to the page"""
  if 'layout' not in _gz:
    base = _ensure(['layout.json'])
    _gz['layout'] = gzip.compress((base / 'layout.json').read_bytes(), 6)
  return _gz['layout']


def _load():
  _ensure()
  from walker.beam import ConceptIndex
  from walker.graph import Graph
  from walker.search import Index
  from walker.v7 import PropStore, WalkV7
  layout = json.loads((DATA / 'layout.json').read_text(encoding='utf-8'))
  g = Graph()
  index = Index(g.texts(), g.recommendations())
  concepts = ConceptIndex(g.page_concepts())
  store = PropStore(DATA)
  by_key = {p['key']: p for ps in store.by_doc.values() for p in ps}
  de = {}  # German display texts of the statements (translate_de.py); the walker itself reads the English originals
  de_file = DATA / 'work' / 'de_props.jsonl'
  if de_file.is_file():
    for line in open(de_file, encoding='utf-8'):
      r = json.loads(line)
      de[r['key']] = r['de']
  doc_i = {d['id']: i for i, d in enumerate(layout['docs'])}
  topic_i = {(t['doc'], t['name']): i for i, t in enumerate(layout['topics'])}
  _state.update(graph=g, index=index, concepts=concepts, layout=layout, by_key=by_key, de=de, doc_i=doc_i, topic_i=topic_i,
                key_i={k: i for i, k in enumerate(layout['keys'])}, Walk=WalkV7, ready=True)


def warm():
  """load in the background so the first question does not wait for the graph"""
  with _lock:
    if _state['started']:
      return
    _state['started'] = True

  def go():
    try:
      _load()
    except Exception as e:  # the page shows it, the log keeps the trace
      _state['error'] = f'{type(e).__name__}: {e}'
  threading.Thread(target=go, daemon=True).start()


def _concept_docs(s):
  """UMLS concept -> the guidelines whose statements name it, built once from the layout"""
  if 'cdocs' not in s:
    L, out = s['layout'], {}
    for i, cs in enumerate(L['pc']):
      d = L['topics'][L['topic'][i]]['doc']
      for c in cs:
        out.setdefault(c, set()).add(d)
    s['cdocs'] = out
  return s['cdocs']


def _prop_card(i, p, s):
  """what the page shows for one statement of the proposition graph. each concept also names up to twelve other
  guidelines it occurs in: links a graph can show beyond the walk"""
  L = s['layout']
  own, cdocs = L['topics'][L['topic'][i]]['doc'], _concept_docs(s)
  cons = [{'ci': c, 'cui': L['concepts'][c]['cui'], 'name': L['concepts'][c]['name'], 'de': L['concepts'][c].get('de', ''),
           'docs': sorted(d for d in cdocs.get(c, ()) if d != own)[:12]} for c in L['pc'][i]]
  return {'i': i, 'text': p['text'], 'text_de': s.get('de', {}).get(p.get('key', ''), ''), 'kind': p['kind'], 'cls': p['cls'], 'lvl': p['lvl'], 'population': p['population'],
          'quote': p['quote'], 'page': p['number'], 'doc': L['topics'][L['topic'][i]]['doc'], 'topic': L['topic'][i], 'concepts': cons}


def _translate(ev, s):
  """one walker event -> the events of the page (None: nothing to show)"""
  t = ev.get('t')
  L = s['layout']
  if t == 'scores':
    probs, level = ev['probs'], ev['level']
    if level == 'documents':
      return {'t': 'scores', 'level': 'guidelines', 'scorer': ev['scorer'], 'items': [[s['doc_i'][k], round(v, 3)] for k, v in probs.items() if k in s['doc_i']]}
    if level == 'topics':
      items = []
      for k, v in probs.items():
        doc, name = k.split('|', 1)
        ti = s['topic_i'].get((s['doc_i'].get(doc), name))
        if ti is not None:
          items.append([ti, round(v, 3)])
      best = sorted(items, key=lambda x: -x[1])[:40]  # the topics a page may draw, with their names
      return {'t': 'scores', 'level': 'topics', 'scorer': ev['scorer'], 'items': items, 'doc': L['topics'][items[0][0]]['doc'] if items else None,
              'names': {str(i): [L['topics'][i].get('name') or '', L['topics'][i].get('de') or ''] for i, _ in best}}
    if level in ('pieces', 'answers'):
      items = sorted(([s['key_i'][k], round(v, 3)] for k, v in probs.items() if k in s['key_i']), key=lambda x: -x[1])
      if level == 'pieces' and not items:  # the 28.10 graph's recommendation nodes (class and level of evidence), not statements
        return {'t': 'scores', 'level': 'recommendations', 'scorer': ev['scorer'], 'candidates': len(probs), 'items': []}
      top = []
      for i, v in items[:8]:  # the best candidates, with their text, for the statements column
        p = s['by_key'].get(L['keys'][i])
        if p:
          top.append({**_prop_card(i, p, s), 'p': v})
      ghost = []  # the next candidates, read and not taken: what a closer look at the graph shows around the path
      for i, v in (items[8:60] if level == 'pieces' else []):
        p = s['by_key'].get(L['keys'][i])
        if p:
          ghost.append([i, v, L['topic'][i], L['topics'][L['topic'][i]]['doc'], p['text'][:110], s.get('de', {}).get(p.get('key', ''), '')[:110]])
      return {'t': 'scores', 'level': 'statements' if level == 'pieces' else 'rerank', 'scorer': ev['scorer'], 'candidates': len(probs),
              'items': items[:150], 'others': len(probs) - len(items), 'top': top, 'ghost': ghost}
    return None
  if t == 'guidelines':
    kept = [s['doc_i'][d] for d in ev['kept'] if d in s['doc_i']]
    return {'t': 'guidelines', 'kept': kept, 'titles': [L['docs'][i].get('title') or '' for i in kept]}
  if t == 'taken':
    cards = []
    for k in ev['keys']:
      i, p = s['key_i'].get(k), s['by_key'].get(k)
      if i is not None and p:
        cards.append({**_prop_card(i, p, s), 'p': ev['scores'].get(k)})
    return {'t': 'taken', 'candidates': ev['candidates'], 'items': cards}
  if t == 'round':
    items = []
    for e in ev['evidence']:
      if e['kind'] == 'statement':
        continue  # shown as 'taken'
      items.append({'kind': e['kind'], 'doc': s['doc_i'].get(e.get('document')), 'page': e.get('number'), 'score': e.get('score')})
    return {'t': 'round', 'n': ev['n'], 'docs': [s['doc_i'][d] for d in ev['docs'] if d in s['doc_i']], 'other': items}
  if t in ('question', 'writing'):
    return {k: v for k, v in ev.items()}
  return None


def _sources(walk, s):
  """the numbered sources the answer cites: [n] -> where it came from"""
  out = []
  for e in walk.evidence:
    i = s['key_i'].get(e['key'])
    card = {'n': len(out) + 1, 'kind': e.get('kind'), 'title': e.get('documentTitle'), 'page': e.get('number'),
            'page_id': str(e.get('page') or '').rsplit('/', 1)[-1],  # the page's id in the app's corpus (corpus.PAGE_BY_ID)
            'text': e.get('text'), 'quote': e.get('quote') or '', 'cor': e.get('cor'), 'loe': e.get('loe'), 'unfit': bool(e.get('unfit'))}
    if i is not None:
      card.update(i=i, doc=s['doc_i'].get(e.get('document')), topic=s['layout']['topic'][i], text_de=s.get('de', {}).get(e['key'], ''))
    else:
      card['doc'] = s['doc_i'].get(e.get('document'))
    out.append(card)
  return out


def _costs(walk, seconds, graph_seconds):
  by = {}
  for t in walk.trace:
    u = t.get('usage')
    if u and u.get('model'):
      m = by.setdefault(u['model'], {'calls': 0, 'usd': 0.0, 'input_tokens': 0, 'output_tokens': 0})
      m['calls'] += 1
      m['usd'] += u.get('usd') or 0
      m['input_tokens'] += u.get('input_tokens') or 0
      m['output_tokens'] += u.get('output_tokens') or 0
  from walker import models
  generative = {k: v for k, v in by.items() if k == models.WRITER}  # the writer is the only model that produces text
  return {'seconds': round(seconds, 1), 'graph_seconds': round(graph_seconds, 1), 'usd': round(walk.usd, 5),
          'decision_calls': sum(v['calls'] for k, v in by.items() if k not in generative),
          'language_model_calls': sum(v['calls'] for v in generative.values()),
          'models': {k: {**v, 'usd': round(v['usd'], 6)} for k, v in by.items()}}


EVIDENCE_PIECES, EVIDENCE_CHARS = 6, 330  # what a video agent gets instead of a written answer (its tool result holds 3,000 characters)


def _evidence_answer(walk):
  """the walk's outcome without the writer: the best statements it selected, numbered like the sources, as the text a
  video agent answers from. no model reads them here, so nothing checks a premise or says 'not covered' when the
  statements are only near the question: that is left to the agent that speaks"""
  walk.evidence = sorted(walk.evidence, key=lambda e: -e.get('score', 0))[:EVIDENCE_PIECES]
  if not walk.evidence:
    return {'finish': 'not covered', 'answer': 'No guideline statement about this question was found in the knowledge graph.', 'answer_items': [], 'count': None}
  lines = []
  for n, e in enumerate(walk.evidence):
    grade = ', '.join(x for x in ('class ' + str(e['cor']) if e.get('cor') else '', 'level of evidence ' + str(e['loe']) if e.get('loe') else '') if x)
    text = ' '.join(str(e.get('text') or '').split())
    text = text if len(text) <= EVIDENCE_CHARS else text[:EVIDENCE_CHARS - 1].rsplit(' ', 1)[0] + '…'
    title = str(e.get('documentTitle') or 'Guideline').split(':')[0][:110]  # without the subtitle: the agent names it aloud
    lines.append(f"[{n + 1}] {title}, page {e.get('number')}{' (' + grade + ')' if grade else ''}: {text}")
  head = ('Guideline statements selected from the knowledge graph for this question, best first. Answer only from them, name the '
          'guideline, and say so if they do not answer the question.')
  return {'finish': 'evidence', 'answer': head + '\n' + '\n'.join(lines), 'answer_items': [], 'count': None}


def run(messages, write=True):
  """generator of events for one question (the last user message; every question is walked on its own).
  write False: the walk ends with the selected evidence instead of a written answer (status 'evidence'), for a caller
  that phrases the answer itself. one round only: without the writer nothing judges the evidence incomplete"""
  question = next((m.get('text', '') for m in reversed(messages or []) if m.get('role') == 'user'), '').strip()
  if not question:
    yield {'t': 'error', 'message': 'Empty question'}
    return
  if not available():
    yield {'t': 'error', 'message': 'The Graph Walker data is not installed on this server'}
    return
  warm()
  waited = time.time()
  while not _state['ready']:
    if _state['error']:
      yield {'t': 'error', 'message': 'The walker could not start: ' + _state['error']}
      return
    yield {'t': 'loading', 'seconds': round(time.time() - waited)}
    time.sleep(1.5)
  if not _slots.acquire(timeout=60):
    yield {'t': 'error', 'message': 'The walker is busy, try again in a moment'}
    return
  q = queue.Queue()
  s = _state
  walk = s['Walk'](s['graph'], s['index'], question, concepts=s['concepts'])
  if not write:
    walk.write_answer = lambda final: _evidence_answer(walk)
  marks = {'writing': None}

  def on_event(ev):
    if ev.get('t') == 'writing' and marks['writing'] is None:
      marks['writing'] = time.time()
    try:
      out = _translate(ev, s)
    except Exception as e:  # a display problem must not stop the walk
      out = {'t': 'note', 'message': f'{type(e).__name__}: {e}'}
    if out:
      q.put(out)
  walk.on_event = on_event
  started = time.time()
  result = {}

  def work():
    try:
      result['outcome'] = walk.run()
    except Exception as e:
      result['error'] = f'{type(e).__name__}: {e}'
    q.put(None)
  threading.Thread(target=work, daemon=True).start()
  yield {'t': 'start', 'question': question}
  try:
    while True:
      try:
        ev = q.get(timeout=240)
      except queue.Empty:
        yield {'t': 'error', 'message': 'The walk took too long'}
        return
      if ev is None:
        break
      ev['at'] = round(time.time() - started, 2)  # seconds since the walk began, shown on each step
      yield ev
    if 'error' in result:
      yield {'t': 'error', 'message': result['error']}
      return
    o = result['outcome']
    text = (o.get('answer') or '').split('\n\nEvidence:')[0].strip()
    end = time.time()
    yield {'t': 'answer', 'status': o.get('result'), 'text': text, 'sources': _sources(walk, s), 'at': round(end - started, 2)}
    yield {'t': 'done', **_costs(walk, end - started, (marks['writing'] or end) - started)}
  finally:
    _slots.release()

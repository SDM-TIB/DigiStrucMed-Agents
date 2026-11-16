"""Checks Guido's walker engine (app/guidowalker.py) without the graph server and without any model: routing rules,
source cards, the chat events of one answer, and the fallback to the other engines.

  python tests/guido_walker_check.py
"""
import pathlib
import sys
import types

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'app'))
# guido.py loads its graph view from the graph server at import (kg.py). this check needs none of it: empty stand-ins
sys.modules['kg'] = types.SimpleNamespace(CONCEPTS={}, IN={}, OUT={}, REC_ROLES=(), RECS={})
sys.modules['retrieve'] = types.SimpleNamespace()
import corpus  # noqa: E402
import guido  # noqa: E402
import guidowalker as gw  # noqa: E402

failed = []


def check(name, cond):
  print(('ok   ' if cond else 'FAIL ') + name)
  if not cond:
    failed.append(name)


def boom(_):
  raise RuntimeError('model down')


def collect(gen):
  """events until the generator ends or raises Unavailable -> (events, reason or None)"""
  out = []
  try:
    for e in gen:
      out.append(e)
  except gw.Unavailable as e:
    return out, str(e)
  return out, None


def fixed(question):
  return lambda m: {'kind': 'guideline', 'question': question}


first = [{'role': 'user', 'text': 'Welche Klasse haben SGLT2-Hemmer bei HFrEF?'}]
follow = first + [{'role': 'assistant', 'text': 'Klasse I [1].'}, {'role': 'user', 'text': 'Und bei HFmrEF?'}]
own = {'kind': 'guideline', 'question': 'Und bei HFmrEF?'}

# ---- routing
check('first message: walked in the user\'s own words, whatever the model rewrote',
      gw.route(first, ask=lambda m: {'kind': 'guideline', 'question': 'something else'}) == {'kind': 'guideline', 'question': first[0]['text']})
check('follow-up: the self-contained form is walked',
      gw.route(follow, ask=lambda m: {'kind': 'guideline', 'question': 'Welche Klasse haben SGLT2-Hemmer bei HFmrEF?'})['question'].endswith('bei HFmrEF?'))
check('follow-up, empty rewrite: the user\'s words', gw.route(follow, ask=lambda m: {'kind': 'guideline', 'question': ''}) == own)
check('small talk: Guido answers himself',
      gw.route([{'role': 'user', 'text': 'Hallo'}], ask=lambda m: {'kind': 'smalltalk', 'reply': 'Guten Tag.'}) == {'kind': 'smalltalk', 'reply': 'Guten Tag.'})
check('small talk without a reply is walked', gw.route([{'role': 'user', 'text': 'Hallo'}], ask=lambda m: {'kind': 'smalltalk', 'reply': ''})['kind'] == 'guideline')
check('routing model fails: the user\'s words', gw.route(follow, ask=boom) == own)
check('routing model answers nonsense: the user\'s words', gw.route(follow, ask=lambda m: ['x']) == own)
check('no user message: nothing to walk', gw.route([], ask=boom) == {'kind': 'guideline', 'question': ''})

# ---- source cards
page = corpus.PAGES[0]
cards = gw.source_cards([
  {'n': 1, 'page_id': page['id'], 'title': 'T', 'page': 99, 'text': 'statement', 'quote': 'verbatim quote', 'cor': 'I', 'loe': 'A', 'kind': 'statement'},
  {'n': 2, 'page_id': 'unknown', 'title': 'Other', 'page': 4, 'text': 'row', 'quote': '', 'cor': None, 'loe': None, 'kind': 'table row'}])
check('source on a corpus page: its id and page number, the guideline title of the graph, the quote to mark',
      cards[0]['page_id'] == page['id'] and cards[0]['page'] == page['page'] and cards[0]['title'] == 'T'
      and cards[0]['highlight'] == 'verbatim quote' and cards[0]['snippet'] == 'statement' and cards[0]['grade'] == 'Class I · LoE A')
check('source outside the corpus: no page id, the walker\'s own title and page',
      cards[1]['page_id'] is None and cards[1]['title'] == 'Other' and cards[1]['page'] == 4 and cards[1]['highlight'] == 'row' and cards[1]['grade'] == '')

# ---- one answer
gw.walkerchat.available = lambda: True
seen = {}


def walk(messages):
  seen['q'] = messages[0]['text']
  yield {'t': 'start', 'question': messages[0]['text']}
  yield {'t': 'scores', 'level': 'guidelines', 'items': [[0, .9]]}
  yield {'t': 'answer', 'status': 'answered', 'text': 'Class I [1].', 'sources': [{'n': 1, 'page_id': page['id'], 'text': 's', 'quote': 'q'}]}
  yield {'t': 'done', 'seconds': 8.1, 'usd': 0.01}


def broken(messages):
  yield {'t': 'start', 'question': 'x'}
  yield {'t': 'error', 'message': 'graph down'}


evs, err = collect(gw.run(follow, walk=walk, router=fixed('standalone?')))
kinds = [e['type'] if e['type'] != 'walker' else 'walker:' + e['ev']['t'] for e in evs]
check('chat: the rewritten question is walked', seen['q'] == 'standalone?' and err is None)
check('chat: steps, the answer step, sources, text, cost, done',
      kinds == ['walker:start', 'walker:scores', 'walker:answer', 'sources', 'text', 'walker:done', 'done'])
check('chat: the text is the answer and the source card opens the page',
      next(e for e in evs if e['type'] == 'text')['delta'] == 'Class I [1].' and next(e for e in evs if e['type'] == 'sources')['items'][0]['page_id'] == page['id'])
evs, err = collect(gw.run(first, via='call', walk=walk, router=boom))
check('call: no routing, the tool\'s question is walked', seen['q'] == first[0]['text'] and evs[-1] == {'type': 'done'})
modes = []


def walk_modes(messages, write=True):
  modes.append(write)
  yield {'t': 'answer', 'status': 'answered' if write else 'evidence', 'text': 'x [1]', 'sources': []}


evs, err = collect(gw.run(first, via='call', walk=walk_modes, writer=False))
check('call without the writer: the walk is asked for evidence only and the text says so',
      modes == [False] and next(e for e in evs if e['type'] == 'text').get('evidence_only') is True)
modes.clear()
evs, err = collect(gw.run(first, walk=walk_modes, router=fixed('q'), writer=False))
check('chat: always the written answer, whatever the call setting', modes == [True] and 'evidence_only' not in next(e for e in evs if e['type'] == 'text'))
modes.clear()
evs, err = collect(gw.run(first, via='call', walk=walk_modes))
check('call, default setting: the writer answers (the evaluated path)', modes == [True])


class FakeWalk:
  def __init__(self, evidence):
    self.evidence = evidence


piece = lambda n, score, **k: {'documentTitle': f'Guideline {n}', 'number': n, 'text': 'statement ' + str(n) + ' ' + 'word ' * 120, 'score': score, 'cor': None, 'loe': None, **k}
out = gw.walkerchat._evidence_answer(FakeWalk([piece(n, n / 10) for n in range(1, 10)] + [piece(10, .99, cor='I', loe='A')]))
rows = out['answer'].split('\n')[1:]
check('evidence answer: the best six statements, best first, numbered, with grade, within the tool result\'s size',
      out['finish'] == 'evidence' and len(rows) == 6 and rows[0].startswith('[1] Guideline 10, page 10 (class I, level of evidence A): statement 10')
      and rows[1].startswith('[2] Guideline 9') and len(out['answer']) <= 3000)
check('evidence answer: nothing selected is "not covered"', gw.walkerchat._evidence_answer(FakeWalk([]))['finish'] == 'not covered')
evs, err = collect(gw.run([{'role': 'user', 'text': 'Danke'}], walk=boom, router=lambda m: {'kind': 'smalltalk', 'reply': 'Gern.'}))
check('small talk: text and done, no walk', evs == [{'type': 'text', 'delta': 'Gern.'}, {'type': 'done'}])
evs, err = collect(gw.run(first, walk=broken, router=fixed('q')))
check('walk fails before an answer: Unavailable', err == 'graph down' and [e['ev']['t'] for e in evs] == ['start'])
evs, err = collect(gw.run(first, walk=lambda m: iter([{'t': 'start'}]), router=fixed('q')))
check('walk ends without an answer: Unavailable', err == 'walk ended without an answer')
evs, err = collect(gw.run(first, walk=walk, router=fixed('')))
check('empty question: Unavailable before anything is shown', err == 'empty question' and evs == [])
gw.walkerchat.available = lambda: False
evs, err = collect(gw.run(first, walk=walk, router=fixed('q')))
check('no data pack: Unavailable before anything is shown', err == 'walker data not installed' and evs == [])

# ---- the start check logs what works, and never raises
lines = []
gw.walkerchat.available = lambda: True
gw.walkerchat.run = walk
gw._ask_route = lambda m: {'kind': 'guideline', 'question': 'Welche Empfehlungsklasse haben SGLT2-Hemmer bei HFmrEF?', 'reply': ''}
gw.warm(log=lambda text, flush=False: lines.append(text))
import time  # noqa: E402
for _ in range(50):
  if len(lines) == 2:
    break
  time.sleep(.05)
check('start check: routing ok and the walk with its sources', len(lines) == 2 and 'routing ok' in lines[0] and 'walk answered, 1 sources on corpus pages' in lines[1])
lines.clear()
gw._ask_route = boom
gw.walkerchat.run = broken
gw.warm(log=lambda text, flush=False: lines.append(text))
for _ in range(50):
  if len(lines) == 2:
    break
  time.sleep(.05)
check('start check: failures are logged, not raised', len(lines) == 2 and 'routing failed (RuntimeError' in lines[0] and 'walk FAILED (Unavailable: graph down)' in lines[1])

# ---- guido.run: the walker first, another engine after it
gw.walkerchat.available = lambda: True
guido.config.AGENT_URL = ''
guido._local = lambda messages, rounds=None: iter([{'type': 'text', 'delta': 'local answer'}, {'type': 'done'}])
gw.walkerchat.run = broken
evs = list(guido.run(first, harness='walker', via='call'))
check('guido: walker fails mid-walk -> fallback signal, then the other engine answers',
      [e.get('ev', {}).get('t') or e['type'] for e in evs] == ['start', 'fallback', 'text', 'done'])
gw.walkerchat.run = walk
evs = list(guido.run(first, harness='walker', via='call'))
check('guido: walker answers -> no other engine', [e['type'] for e in evs].count('text') == 1 and evs[-1] == {'type': 'done'}
      and next(e for e in evs if e['type'] == 'text')['delta'] == 'Class I [1].')
gw.walkerchat.available = lambda: False
evs = list(guido.run(first, harness='walker'))
check('guido: no data pack -> the other engine, no fallback signal', [e['type'] for e in evs] == ['text', 'done'])
evs = list(guido.run(first, harness='local'))
check('guido: harness local never touches the walker', [e['type'] for e in evs] == ['text', 'done'])
sys.exit(1 if failed else 0)

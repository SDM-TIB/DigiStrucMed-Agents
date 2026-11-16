"""Runs the app locally with Guido on the Graph Walker and a replayed walk, to check the pages without any model cost.

  python tests/guido_walker_dev.py            then open http://localhost:8099, password: local-walker-check

The server is the real one (app/server.py) with Guido's engine set to 'walker'. Only the walk itself is replaced: instead
of Walker V7 (decision models and a writer on Cloudflare, about one cent per question) a replay sends the same events,
built from the real proposition graph in app/data/walker: every guideline scored, the guidelines and topics that hold
the question's words, their statements, the best of them as evidence with page and verbatim quote, and an answer that
cites them. The scores are word overlap, not model judgements, and the "answer" only lists the evidence: this checks
the event flow, the step lines, the embedded path, the source viewer and the video call's screen, not answer quality.
A question containing "walker down" makes the replay fail mid-walk, to check the fallback to the other engines.

With --real the walk is the real Walker V7 instead of the replay (decision models and writer as configured in the
environment, about one to two cents per question; the walker reads the graph server on localhost:7001 for what its
SPARQL cache in app/data/walker/sparql_cache does not hold), and Cloudflare stays on, so a video call with Guido can be
tested end to end: python tests/guido_walker_dev.py --real

Without --real nothing outside this computer is needed or billed. Guido's concept view, normally loaded from the graph server at
start, is a three-node stand-in here (so the other engines and the Sources graph tab have nothing real to show), and
the language model, the decision models on Cloudflare, the embedding service and the agent server are switched off:
Guido's routing call fails and the question is walked as typed (the documented behaviour). Page images come from the page bucket when the local credentials reach it.
"""
import json
import mimetypes
import os
import pathlib
import re
import sys
import time
from http.server import ThreadingHTTPServer

APP = pathlib.Path(__file__).resolve().parents[1] / 'app'
REAL = '--real' in sys.argv
for name, value in {'GUIDO_HARNESS': 'walker', 'LLM_API_KEY': '', 'GEMINI_API_KEY': '', 'EMBED_URL': '', 'AGENT_URL': '', 'AGENT_AISDK_URL': '',
                    **({} if REAL else {'CF_ACCOUNT_ID': '', 'CF_API_TOKEN': ''}),  # replay: no clef-flash either, triage banners and the screen director stay off
                    'PORT': '8099', 'DEMO_PASSWORD': 'local-walker-check', 'GRAPH_ENDPOINT': 'http://localhost:7001'}.items():
  os.environ[name] = value
sys.path.insert(0, str(APP))
import walkerchat  # noqa: E402

WORD = re.compile(r'[a-zäöüß0-9]{4,}')
STOP = set('what which does with from that this have were will welche wird werden nach sind eine einer oder nicht auch beim guideline guidelines leitlinie leitlinien patients patienten recommended recommendation'.split())


def load():
  data = APP / 'data' / 'walker'
  L = json.loads((data / 'layout.json').read_text(encoding='utf-8'))
  by_key = {}
  for line in open(data / 'work' / 'props_all.jsonl', encoding='utf-8'):
    r = json.loads(line)
    for n, x in enumerate(r['propositions']):
      by_key[f"{r['page']}#prop{n}"] = {'key': f"{r['page']}#prop{n}", 'page': r['page'], 'number': r['number'], 'text': str(x.get('text') or ''),
                                         'kind': str(x.get('kind') or 'other').lower(), 'cls': str(x.get('class') or ''), 'lvl': str(x.get('level') or ''),
                                         'population': str(x.get('population') or ''), 'quote': str(x.get('quote') or '')}
  de = {}
  for line in open(data / 'work' / 'de_props.jsonl', encoding='utf-8'):
    r = json.loads(line)
    de[r['key']] = r['de']
  return {'layout': L, 'by_key': by_key, 'de': de, 'key_i': {k: i for i, k in enumerate(L['keys'])}}


S = load()


def replay(messages):
  """the events walkerchat.run yields for one question, from word overlap over the real graph"""
  question = messages[-1]['text']
  L, words = S['layout'], {w for w in WORD.findall(question.lower()) if w not in STOP}
  fit = lambda text: len(words & set(WORD.findall(text.lower()))) / max(1, len(words))
  t0 = time.time()
  at = lambda ev: {**ev, 'at': round(time.time() - t0, 2)}
  yield {'t': 'start', 'question': question}
  time.sleep(.5)
  yield at({'t': 'question', 'aggregate': False})
  # statements first: the guidelines and topics the replay "walks" are the ones holding the best statements
  scored = sorted(((fit(p['text'] + ' ' + p['population']), i) for i, k in enumerate(L['keys']) for p in [S['by_key'].get(k)] if p), reverse=True)[:400]
  best = [(s, i) for s, i in scored if s > 0][:6]
  doc_of = lambda i: L['topics'][L['topic'][i]]['doc']
  kept = list(dict.fromkeys(doc_of(i) for _, i in best))[:3] or [0]
  docs = [[di, round(min(.97, .08 + .5 * fit(d['title']) + (.45 if di in kept else 0)), 3)] for di, d in enumerate(L['docs'])]
  time.sleep(.9)
  yield at({'t': 'scores', 'level': 'guidelines', 'scorer': '@cf/cloudflare/clef-flash', 'items': docs})
  time.sleep(.5)
  yield at({'t': 'guidelines', 'kept': kept, 'titles': [L['docs'][d]['title'] for d in kept]})
  if 'walker down' in question.lower():
    time.sleep(.6)
    yield {'t': 'error', 'message': 'replayed failure'}
    return
  for d in kept:
    topics = [ti for ti, t in enumerate(L['topics']) if t['doc'] == d]
    hit = {L['topic'][i] for _, i in best if doc_of(i) == d}
    time.sleep(.6)
    yield at({'t': 'scores', 'level': 'topics', 'scorer': '@cf/cloudflare/clef-flash', 'doc': d,
              'items': [[ti, round(.85 if ti in hit else min(.5, .05 + fit(L['topics'][ti]['name'])), 3)] for ti in topics],
              'names': {str(ti): [L['topics'][ti].get('name') or '', L['topics'][ti].get('de') or ''] for ti in topics}})
  card = lambda s, i: {**walkerchat._prop_card(i, S['by_key'][L['keys'][i]], S), 'p': round(min(.98, .35 + .6 * s), 3)}
  candidates = sum(1 for i in range(len(L['keys'])) if doc_of(i) in kept)
  time.sleep(1.2)
  yield at({'t': 'scores', 'level': 'statements', 'scorer': '@cf/cloudflare/clef-flash', 'candidates': candidates,
            'items': [[i, round(.3 + .6 * s, 3)] for s, i in scored[:150]], 'others': 0, 'top': [card(s, i) for s, i in scored[:8]]})
  time.sleep(1.2)
  yield at({'t': 'scores', 'level': 'rerank', 'scorer': '@cf/cloudflare/clef', 'candidates': 150,
            'items': [[i, round(.35 + .6 * s, 3)] for s, i in scored[:150]], 'others': 0, 'top': [card(s, i) for s, i in best[:8]]})
  time.sleep(.8)
  taken = [card(s, i) for s, i in best]
  yield at({'t': 'taken', 'candidates': candidates, 'items': taken})
  time.sleep(.5)
  yield at({'t': 'writing', 'final': True, 'pieces': len(taken)})
  time.sleep(1.6)
  sources = [{'n': n + 1, 'kind': 'statement', 'title': L['docs'][c['doc']]['title'], 'page': c['page'], 'page_id': L['keys'][c['i']].split('#')[0].rsplit('/', 1)[-1],
              'text': c['text'], 'quote': c['quote'], 'cor': c['cls'] or None, 'loe': c['lvl'] or None, 'unfit': False,
              'i': c['i'], 'doc': c['doc'], 'topic': c['topic'], 'text_de': c['text_de']} for n, c in enumerate(taken)]
  text = ('Replay, not a model answer. The statements the walk selected:\n\n' + '\n'.join(f"- {s['text']} [{s['n']}]" for s in sources[:4])) if sources \
    else 'No guideline in the library addresses this question: no statement about it was found in the guidelines searched.'
  end = time.time()
  yield {'t': 'answer', 'status': 'answered' if sources else 'not covered', 'text': text, 'sources': sources, 'at': round(end - t0, 2)}
  yield {'t': 'done', 'seconds': round(end - t0, 1), 'graph_seconds': round(end - t0 - 1.6, 1), 'usd': 0.0, 'decision_calls': 0, 'language_model_calls': 0, 'models': {}}


if not REAL:
  walkerchat.run = replay
  walkerchat.warm = lambda: None  # the real walker (graph index, models) is not loaded in this check

import graph  # noqa: E402

_concept = lambda i, label: {'id': 'c:' + i, 'iri': 'urn:dev:' + i, 'label': label, 'aliases': [], 'cui': None, 'wikidata': None, 'desc': None, 'codes': {}}
graph.load_agent_view = lambda *a, **k: {  # the smallest view kg.py and retrieve.py build their indexes from
  'concepts': [_concept('a', 'stand-in concept a'), _concept('b', 'stand-in concept b')],
  'recs': [{'id': 'r:dev', 'iri': 'urn:dev:r', 'text': 'Stand-in recommendation of the local check; the real view comes from the graph server.',
            'class': [], 'loe': [], 'direction': [], 'ref': None, 'guide': 'g:dev', 'src': [], 'doc': None, 'page_id': None}],
  'guides': [{'id': 'g:dev', 'label': 'stand-in guideline'}],
  'edges': [('c:a', 'c:b', 'related to'), ('r:dev', 'c:a', 'mention'), ('r:dev', 'g:dev', 'source')], 'pages': {}}
import server  # noqa: E402

if __name__ == '__main__':
  mimetypes.add_type('image/svg+xml', '.svg')
  for ext in ('woff2', 'woff', 'ttf'):
    mimetypes.add_type('font/' + ext, '.' + ext)
  server.page_search.load()
  print(f"Guido on {'the real walker' if REAL else 'a replayed walk'}: http://localhost:{server.config.PORT}  (password: {os.environ['DEMO_PASSWORD']})", flush=True)
  ThreadingHTTPServer(('127.0.0.1', server.config.PORT), server.Handler).serve_forever()

"""Guido on the Graph Walker: Walker V7 (walkerchat.py) as the engine behind Guido's chat and video call.

The walker answers one self-contained question by walking the proposition graph (guidelines -> topics -> statements);
a language model writes only at the end, from the statements the walk selected. This module is what a conversation
needs around that:

  route      what the user means: a guideline question (made self-contained when it is a follow-up), or small talk
             that Guido answers himself without walking the graph. One call of the app's language model, bounded in
             time; without it the user's own words are walked as they are. The video call skips it: there the Tavus
             persona already talks and passes a complete question.
  run        the walk as chat events: `walker` (every step, for the trace and the path picture), `sources` (numbered,
             each with its guideline page and the verbatim quote), `text`, `done`.

It raises Unavailable before anything was shown when the walker cannot answer (data pack missing, walk failed), so
guido.run falls back to the agent it ran before.
"""
import json
import threading
import time

import config
import corpus
import llm
import walkerchat

ROUTE_SECONDS = 4.0  # the walk must not wait longer for the routing call
ROUTE = (
  "You prepare one user message for Guido, an assistant that answers questions about clinical guidelines on the heart "
  "and circulation by looking them up in a knowledge graph. The lookup sees one question only, without the conversation.\n"
  "Return JSON: {\"kind\": \"guideline\" | \"smalltalk\", \"question\": string, \"reply\": string}.\n"
  "- guideline: anything medical or about the guidelines, also a single word such as 'aspirin', a personal question "
  "or a follow-up. `question` is the message as one self-contained question in the user's language: resolve "
  "references to earlier turns ('and for women?', 'what about the dose?') with the drug, condition, guideline and "
  "population they refer to. If the message is already self-contained, copy it unchanged. Add nothing the user did "
  "not ask. `reply` is empty.\n"
  "- smalltalk: a greeting, thanks, a question about Guido himself or about how he works, with no medical content. "
  "`reply` answers it in the user's language in one or two sentences, formally addressed (German 'Sie'): Guido is an "
  "AI assistant from TIB who answers by walking a knowledge graph of cardiology guidelines and shows the source of "
  "every statement; then invite a question. `question` is empty.")


WARM_QUESTION = 'What is the recommended target dose of sacubitril/valsartan in heart failure with reduced ejection fraction?'
WARM_FOLLOW_UP = [{'role': 'user', 'text': 'Welche Empfehlungsklasse haben SGLT2-Hemmer bei HFrEF?'},
                  {'role': 'assistant', 'text': 'Klasse 1, Evidenzgrad A [1].'}, {'role': 'user', 'text': 'Und bei HFmrEF?'}]


def warm(log=print):
  """when an instance starts with Guido on the walker: one routing call and one complete walk in the background, each
  with its outcome in the service log. the first visitor then meets loaded data, open connections and a filled
  query cache, and the log says whether the engine works here and how long a walk takes (about two cents per start)"""
  def go():
    try:
      t0 = time.time()
      out = _ask_route(WARM_FOLLOW_UP)
      ok = out.get('kind') == 'guideline' and 'hfmref' in str(out.get('question') or '').lower() and len(str(out.get('question'))) > len('Und bei HFmrEF?')
      log(f"guido walker start check: routing {'ok' if ok else 'unexpected: ' + json.dumps(out, ensure_ascii=False)[:200]} in {time.time() - t0:.1f} s", flush=True)
    except Exception as e:  # the walk then takes a follow-up as typed
      log(f'guido walker start check: routing failed ({type(e).__name__}: {str(e)[:160]})', flush=True)
    try:
      t0, status, sources = time.time(), None, 0
      for ev in run([{'role': 'user', 'text': WARM_QUESTION}], via='call', writer=True):
        if ev['type'] == 'walker' and ev['ev'].get('t') == 'answer':
          status = ev['ev'].get('status')
        if ev['type'] == 'sources':
          sources = sum(1 for s in ev['items'] if s.get('page_id'))
      log(f'guido walker start check: walk {status}, {sources} sources on corpus pages, {time.time() - t0:.1f} s including loading', flush=True)
    except Exception as e:
      log(f'guido walker start check: walk FAILED ({type(e).__name__}: {str(e)[:300]}); Guido answers with the fallback engine', flush=True)
  threading.Thread(target=go, daemon=True).start()


class Unavailable(Exception):
  """the walker cannot answer this one; nothing was shown yet"""


def last_question(messages):
  return next((str(m.get('text') or '') for m in reversed(messages or []) if m.get('role') == 'user'), '').strip()


def _ask_route(messages):
  convo = [{'role': m.get('role'), 'text': str(m.get('text') or '')[:1200]} for m in (messages or [])[-6:]]
  raw, _ = llm.complete([{'role': 'system', 'content': ROUTE},
                         {'role': 'user', 'content': json.dumps({'conversation': convo[:-1], 'message': convo[-1]['text']}, ensure_ascii=False)}],
                        max_tokens=300, timeout=ROUTE_SECONDS + 1, response_format={'type': 'json_object'}, temperature=0)
  return json.loads(raw)


def route(messages, ask=_ask_route):
  """{'kind': 'guideline', 'question': ...} or {'kind': 'smalltalk', 'reply': ...}. Any doubt, failure or delay of the
  routing call: the user's own words are the question"""
  own = last_question(messages)
  fallback = {'kind': 'guideline', 'question': own}
  if not own:
    return fallback
  box = {}

  def go():
    try:
      box['out'] = ask(messages)
    except Exception as e:
      box['error'] = type(e).__name__
  t = threading.Thread(target=go, daemon=True)
  t.start()
  t.join(ROUTE_SECONDS)
  out = box.get('out')
  if not isinstance(out, dict):
    return fallback
  if out.get('kind') == 'smalltalk' and str(out.get('reply') or '').strip():
    return {'kind': 'smalltalk', 'reply': str(out['reply']).strip()}
  question = str(out.get('question') or '').strip()
  earlier = any(m.get('role') == 'user' for m in (messages or [])[:-1])
  # a first message is walked in the user's own words; only a follow-up needs the rewritten form
  return {'kind': 'guideline', 'question': question if earlier and question else own}


def source_cards(items):
  """the walker's numbered evidence as the chat's source cards: the guideline page it is on (so the page viewer opens
  the original page) and the verbatim quote to mark there"""
  out = []
  for s in items or []:
    page = corpus.PAGE_BY_ID.get(str(s.get('page_id') or ''))
    grade = ' · '.join(x for x in ('Class ' + str(s['cor']) if s.get('cor') else '', 'LoE ' + str(s['loe']) if s.get('loe') else '') if x)
    # the guideline's title from the graph's verified document metadata; the corpus title is the page's own heading
    out.append({'n': s['n'], 'page_id': page['id'] if page else None, 'title': s.get('title') or (page or {}).get('title') or 'Guideline',
                'ref': (page or {}).get('ref'), 'page': (page or {}).get('page') or s.get('page'), 'snippet': s.get('text') or '',
                'highlight': s.get('quote') or s.get('text') or '', 'grade': grade, 'cor': s.get('cor'), 'loe': s.get('loe'),
                'kind': s.get('kind'), 'walker': True})
  return out


def run(messages, via=None, walk=None, router=None, writer=None):
  """chat events of one answer. `via` 'call': the video call's tool, the question arrives complete; there `writer`
  False (default: GUIDO_CALL_WRITER) ends the walk with the selected statements, which the video agent phrases itself.
  the chat always gets the written answer"""
  walk, router = walk or walkerchat.run, router or route
  writer = True if via != 'call' else config.GUIDO_CALL_WRITER if writer is None else bool(writer)
  if not walkerchat.available():
    raise Unavailable('walker data not installed')
  r = {'kind': 'guideline', 'question': last_question(messages)} if via == 'call' else router(messages)
  if r['kind'] == 'smalltalk':
    yield {'type': 'text', 'delta': r['reply']}
    yield {'type': 'done'}
    return
  if not r['question']:
    raise Unavailable('empty question')
  answered = False
  for ev in (walk([{'role': 'user', 'text': r['question']}]) if writer else walk([{'role': 'user', 'text': r['question']}], write=False)):
    kind = ev.get('t')
    if kind == 'error':
      if not answered:
        raise Unavailable(str(ev.get('message') or 'walk failed'))
      return
    if kind == 'answer':
      cards = source_cards(ev.get('sources'))
      yield {'type': 'walker', 'ev': ev}
      yield {'type': 'sources', 'query': 'walker', 'items': cards}
      yield {'type': 'text', 'delta': ev.get('text') or '', **({'evidence_only': True} if ev.get('status') == 'evidence' else {})}
      answered = True
      continue
    if kind == 'done':  # one line per walk in the service log: how long, how much, which path. never the question
      print(f"guido walker: {ev.get('seconds')} s (graph walk {ev.get('graph_seconds')} s), {ev.get('usd')} USD, {ev.get('decision_calls')} decisions, "
            f"{'call' if via == 'call' else 'chat'}, {'writer' if writer else 'evidence only'}", flush=True)
    yield {'type': 'walker', 'ev': ev}
  if not answered:
    raise Unavailable('walk ended without an answer')
  yield {'type': 'done'}

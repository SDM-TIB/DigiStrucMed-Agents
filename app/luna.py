"""Luna: an agentic RAG assistant over the guideline library.

The model plans its own retrieval with three tools and answers with [n] citations:
  search_guidelines  EmbeddingGemma 2 search with clef-flash reranking (search.py), optionally within one guideline.
                     Each result carries its relevance and page windows; the response also shows in which
                     guidelines the likely relevant pages cluster ("facets"), so the agent sees beyond its top k.
  read_page          up to three whole pages at once, as the original OCR source: Markdown with the HTML tables
  list_guidelines    the catalogue, to find a guideline to search in
The loop follows common agent-harness practice (Vercel AI SDK loop control, Anthropic's guidance on tools for
agents): a step budget with parallel tool calls per step, a notice when the budget is used up, a final answer turn
without tools, and older tool results shortened so the context stays focused.
yields ui events: tool_call, sources, read, text, done, error.
"""
import base64
import html
import json
import re
import threading
import urllib.request
import uuid

import config
import corpus
import live
import llm
import search
import storage

MAX_TOOL_ROUNDS = 6  # default step budget; a request may ask for 1..ROUNDS_LIMIT (evaluation ablations)
ROUNDS_LIMIT = 10
READ_MAX = 3  # pages per read_page call
PAGE_CHARS = 32000  # an original page; the longest of the 2,780 has 30,768 characters
KEEP_FULL = 2  # tool turns kept in full; older tool results are shortened
SHORT = 1500
LAST_ROUND = ('That was your last tool step: no further searches or page reads are possible. '
              'Write your answer now from the sources you found, with their citation numbers. '
              'If they do not answer the question, say so.')

SYSTEM = """You are Luna, an AI research assistant developed by the team at TIB (Leibniz Information Centre for Science and Technology). You answer questions about clinical guideline evidence on the heart and circulation, using a library of 204 guideline and patient-information documents (2,780 pages, English and German).

How to work (you have up to {{rounds}} tool steps; several tool calls in one step run together):
1. Plan. Identify the parts of the question: each population, situation, drug or guideline it asks about. For a question with several parts (two situations, two guidelines, then and now) search each part on its own; you may issue several search_guidelines calls in one step.
2. Search. Phrase each query as a full description with the clinical terms, the population and the guideline if known. When the question names a guideline, set `guideline` (its title words or its number from list_guidelines) so the search stays inside it.
3. Check. Each result has a relevance between 0 and 1 (how likely the page holds the specific answer) and page windows (tables written as rows, "Class: I; Level: A"). `facets` shows in which guidelines the likely relevant pages cluster. If the best relevance is low or the facets point elsewhere, search again with other words or another guideline.
4. Read the original. Windows are cleaned excerpts made for searching. Before you state an exact value (a dose, threshold, rate, percentage or duration, with its sign: <, ≤, >, ≥), a class of recommendation or a level of evidence, read the page that holds it with read_page: it returns the original page with its HTML tables, so you can see which value, class and level belong to which row.
5. Answer.

How to answer:
- Base medical statements only on retrieved sources. Cite them inline with their numbers in square brackets, one number per bracket, e.g. [1] or [2][3]. Never invent sources, page numbers, recommendation classes or evidence levels; report them only when the source text states them.
- If the sources do not answer the question, say so plainly. If the question rests on a wrong premise, say what the guideline states instead.
- {languages}
- Be clear and well structured: a direct answer first, then the details in short bullet points or a small table. Use **bold** sparingly for key terms. Give every relevant detail the sources contain; as long as needed, usually 150 to 350 words.
- Distinguish what a guideline recommends from background description. Mention who a recommendation applies to and important conditions or exceptions.
- {research}
- Formatting: Markdown only. Write symbols as plain Unicode (≤, ≥, ±, µg), never LaTeX or $...$.
- If the user says they currently have severe chest pain, severe breathlessness or fainted, first tell them to call 112 immediately.""".format(languages=config.LANGUAGE_RULE, research=config.RESEARCH_RULE)

# view_page (agent server only): the rendered PDF page as an image, for what the OCR text may have lost (figures,
# footnotes, shifted table cells). Offered per request while it is evaluated, or always with LUNA_IMAGE_TOOL=1.
VIEW_MAX = 2
IMAGE_RULE = ('\n- If the original text of a page is unclear or a value may sit in a figure, a footnote or a shifted table '
              'cell, look at the page itself with view_page (the rendered PDF page) before you answer.')

# the agent server's loop has no fixed tool budget: the model searches until it can answer
SYSTEM_AGENT = SYSTEM.replace('How to work (you have up to {rounds} tool steps; several tool calls in one step run together):',
                              'How to work (several tool calls in one step run together):')

TOOLS = [
  llm.tool('search_guidelines',
           'Semantic search over the guideline pages (EmbeddingGemma 2, each candidate page judged by a relevance model). '
           'Returns numbered sources (guideline, author/year, page, relevance 0-1, page windows) and facets: the guidelines '
           'where the likely relevant pages cluster.',
           {'query': {'type': 'string', 'description': 'What to find, as a full phrase with the clinical terms and the population, '
                                                       'e.g. "class of recommendation for beta-blockers in patients with HFrEF"'},
            'guideline': {'type': 'string', 'description': 'Optional: search only in this guideline, given by its words '
                                                           '(e.g. "2021 ESC heart failure") or its number from list_guidelines'},
            'top_k': {'type': 'integer', 'description': 'Number of sources, 3-8 (default 5)'}}, ['query']),
  llm.tool('read_page', 'Read up to three whole pages returned as numbered sources, as the original source: the OCR text in '
                         'Markdown with the HTML tables (rows and columns as in the guideline, signs like < and > kept).',
           {'sources': {'type': 'array', 'items': {'type': 'integer'}, 'description': 'Source numbers from earlier search results'}},
           ['sources']),
  llm.tool('list_guidelines', 'The guideline catalogue: number, title, author/year, language and pages of each document, '
                              'optionally only those matching a topic. Use it to find the guideline to search in.',
           {'topic': {'type': 'string', 'description': 'Optional words to match in titles, e.g. "ventricular arrhythmias"'}}, []),
]


HARNESSES = {'mastra': lambda: config.AGENT_URL, 'aisdk': lambda: config.AGENT_AISDK_URL}


def run(messages, rounds=None, harness=None, image_tool=None, candidates=None):
  """the agent server runs the loop when AGENT_URL is set (agent-server/, Mastra; harness 'aisdk' picks the AI SDK 7
  server, agent-server-aisdk/); otherwise, or when it cannot be reached before it answers, the loop below runs here
  with the same prompt and tools"""
  url = HARNESSES.get(harness or 'mastra', HARNESSES['mastra'])()
  if url:
    started = False
    try:
      for ev in _via_agent(messages, rounds, url, config.LUNA_IMAGE_TOOL if image_tool is None else bool(image_tool),
                           candidates=candidates):
        started = True
        yield ev
      return
    except Exception as e:
      if started:
        yield llm.error_event(e, 'Agent')
        return
      print('luna: agent server unavailable, local loop:', type(e).__name__, flush=True)
  yield from _local(messages, rounds)


def _via_agent(messages, rounds, url, image_tool=False, agent='luna', system=None, run=None, candidates=None):
  """an agent of the agent server (luna, or guido from guido.py), its event stream already in the chat's format.
  candidates: how this run's searches find their candidate pages (search.candidates), for an evaluation's variant"""
  body = {'messages': [{'role': m.get('role'), 'text': m.get('text')} for m in messages if m.get('text')][-12:],
          'system': system or SYSTEM_AGENT + (IMAGE_RULE if image_tool else ''), 'run': run or uuid.uuid4().hex,
          'image_tool': image_tool}
  if rounds:  # an evaluation's explicit step bound
    body['max_steps'] = int(rounds)
  req = urllib.request.Request(url.rstrip('/') + f'/{agent}/chat', data=json.dumps(body).encode(), method='POST',
                               headers={'Content-Type': 'application/json', 'X-Agent-Token': config.AGENT_TOKEN,
                                        'Authorization': 'Bearer ' + storage.identity_token(url)}
                               if config.AGENT_TOKEN else
                               {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + storage.identity_token(url)})
  # the agent's stream and the run's live channel (search progress from the tool endpoints) in one stream: a reader
  # thread puts the agent's events into the channel, this generator yields everything in the order it arrives
  channel = live.open_run(body['run'])
  if candidates:
    live.set_options(body['run'], {'candidates': candidates})
  end = object()

  def read():
    try:
      with urllib.request.urlopen(req, timeout=600) as r:
        for raw in r:
          line = raw.decode('utf-8', 'replace').strip()
          if line.startswith('data:'):
            channel.put(json.loads(line[5:]))
    except Exception as e:  # handed to the consumer, which raises it there
      channel.put(e)
    channel.put(end)

  threading.Thread(target=read, daemon=True).start()
  try:
    while True:
      ev = channel.get()
      if ev is end:
        return
      if isinstance(ev, Exception):
        raise ev
      yield ev
  finally:
    live.close(body['run'])


def _local(messages, rounds=None):
  if not config.LLM_API_KEY:
    yield {'type': 'error', 'message': 'Chat is not configured (missing LLM_API_KEY).'}
    return
  rounds = llm.rounds(rounds, MAX_TOOL_ROUNDS, ROUNDS_LIMIT)
  chat = llm.history(messages, keep=12, max_chars=8000, system=SYSTEM.replace('{rounds}', str(rounds)))
  question = next((m['text'] for m in reversed(messages) if m.get('role') == 'user' and m.get('text')), '')
  sources = []  # page indices, numbered 1..n across this answer
  answer = ''
  try:
    for rnd in range(rounds + 1):
      final = rnd == rounds  # last step: no tools sent at all (Gemini on Vertex AI ignores tool_choice none), the model has to answer
      text, calls = '', []
      for kind, val in llm.stream(_shorten(chat), None if final else TOOLS, max_tokens=2500):
        if kind == 'text':
          text += val
          yield {'type': 'text', 'delta': val}
        else:
          calls = val
      answer += text
      if not calls:
        if answer.strip() or final:
          break
        chat += [{'role': 'assistant', 'content': text or '...'}, {'role': 'user', 'content': 'Answer now from the sources you found.'}]
        continue
      results = []
      for call in calls:
        name, args = call['function']['name'], llm.args(call)
        yield {'type': 'tool_call', 'name': name, 'args': args}
        if name == 'search_guidelines':
          results.append((yield from _search(args, sources, question)))
        elif name == 'read_page':
          results.append((yield from _read(args, sources)))
        elif name == 'list_guidelines':
          results.append(_catalogue(args))
        else:
          results.append({'error': 'Unknown tool'})
      chat += llm.tool_turn(text, calls, results)
      if rnd == rounds - 1:  # the budget is used up: say so, so the model plans its answer instead of searching on
        chat.append({'role': 'user', 'content': LAST_ROUND})
    if not answer.strip():  # every step ended in tool calls: answer from what was found
      for val in llm.answer_now(_shorten(chat), 'Answer the question now from the sources you found. Do not search again.'):
        answer += val
        yield {'type': 'text', 'delta': val}
    yield {'type': 'done'}
  except Exception as e:
    yield llm.error_event(e)


def _shorten(chat):
  """the conversation as the model sees it: tool results of all but the last KEEP_FULL tool turns shortened"""
  turns = [i for i, m in enumerate(chat) if m.get('role') == 'assistant' and m.get('tool_calls')]
  old = set(turns[:-KEEP_FULL]) if len(turns) > KEEP_FULL else set()
  out, in_old = [], False
  for i, m in enumerate(chat):
    if m.get('role') == 'assistant':
      in_old = i in old
    if in_old and m.get('role') == 'tool' and len(m.get('content') or '') > SHORT:
      m = {**m, 'content': m['content'][:SHORT] + ' ... [earlier result shortened; read the page again if you need it]'}
    out.append(m)
  return out


# ---- the catalogue
def _docs():
  return corpus.library()['documents']


def _hay(d):
  return f"{d['title']} {d.get('ref') or ''}".lower()


def _catalogue(args):
  topic = [w for w in re.findall(r'[a-zäöüß0-9]+', str(args.get('topic') or '').lower()) if len(w) > 2]
  out = []
  for n, d in enumerate(_docs(), 1):
    if not topic or any(w in _hay(d) for w in topic):
      out.append({'number': n, 'title': d['title'], 'ref': d.get('ref'), 'language': d['lang'], 'pages': len(d['pages'])})
  return {'guidelines': out[:40], 'shown': min(40, len(out)), 'matching': len(out)}


def _guideline_filter(value):
  """(document ids, their titles) for a guideline given by its catalogue number or its words; (None, None) when
  nothing matches clearly"""
  value = str(value or '').strip()
  if not value:
    return None, None
  docs = _docs()
  if value.isdigit() and 1 <= int(value) <= len(docs):
    d = docs[int(value) - 1]
    return {d['doc']}, d['title']
  words = [w for w in re.findall(r'[a-zäöüß0-9]+', value.lower()) if len(w) > 2 or w.isdigit()]
  if not words:
    return None, None
  score = {d['doc']: sum(w in _hay(d) for w in words) for d in docs}
  best = max(score.values())
  hits = [d for d in docs if score[d['doc']] == best]
  if best < max(1, (len(words) + 1) // 2) or len(hits) > 6:  # at least half the words, and a small set
    return None, None
  return {d['doc'] for d in hits}, '; '.join(d['title'] for d in hits)


# ---- the tools
def _search(args, sources, question='', progress=None, method=None):
  q = str(args.get('query', ''))[:300]
  k = max(3, min(8, int(args.get('top_k') or 5)))
  docs, scope = _guideline_filter(args.get('guideline'))
  res = search.search(q, question=question[:1000] or None, k=k, docs=docs, progress=progress, method=method)
  cards, found = [], []
  for h in res['hits']:
    i = h['page']
    if i not in sources:
      sources.append(i)
    n = sources.index(i) + 1
    cards.append({**corpus.card(n, i, q), 'relevance': h['relevance']})
    p = corpus.PAGES[i]
    hit = {'n': n, 'title': p['title'], 'ref': p.get('ref'), 'page': p['page'], 'relevance': h['relevance'],
           'passages': h['passages'] or [cards[-1].get('snippet')]}
    found.append({k2: v for k2, v in hit.items() if v not in (None, '', [])})
  yield {'type': 'sources', 'query': q, 'items': cards, 'mode': res['mode']}
  if not found:
    return 'No matching pages. Try other terms' + (' or no guideline filter.' if docs else '.')
  title = {d['doc']: d['title'] for d in _docs()}
  out = {'sources': found,
         'facets': [{'guideline': title.get(f['doc'], f['doc']), 'likely_relevant_pages': f['likely_relevant'],
                     'candidate_pages': f['candidates']} for f in res['facets'][:5]]}
  if args.get('guideline'):
    out['searched_in'] = scope or f"all guidelines (no guideline matched '{args.get('guideline')}')"
  return out


def _read(args, sources):
  wanted = args.get('sources')
  if wanted is None and args.get('source') is not None:  # the older single-page form
    wanted = [args.get('source')]
  if not isinstance(wanted, list):
    wanted = [wanted]
  pages, errors = [], []
  for n in list(dict.fromkeys(str(x) for x in wanted))[:READ_MAX]:
    if not n.isdigit() or not 1 <= int(n) <= len(sources):
      errors.append(f'Unknown source {n}')
      continue
    n = int(n)
    p = corpus.PAGES[sources[n - 1]]
    yield {'type': 'read', 'item': corpus.card(n, sources[n - 1])}
    pages.append({'source': n, 'title': p['title'], 'page': p['page'], 'original': html.unescape(p['text'])[:PAGE_CHARS]})
  out = {'pages': pages}
  if errors:
    out['errors'] = errors
  if len(wanted) > READ_MAX:
    out['note'] = f'At most {READ_MAX} pages per call; read the others in another call.'
  return out


def _view(args, sources):
  """the rendered PDF pages of up to VIEW_MAX sources, as WebP images (base64) for the model"""
  wanted = args.get('sources')
  if not isinstance(wanted, list):
    wanted = [wanted]
  images, errors, ui = [], [], []
  for n in list(dict.fromkeys(str(x) for x in wanted))[:VIEW_MAX]:
    if not n.isdigit() or not 1 <= int(n) <= len(sources):
      errors.append(f'Unknown source {n}')
      continue
    n = int(n)
    p = corpus.PAGES[sources[n - 1]]
    data = storage.get(p['id'] + '.webp')
    if not data:
      errors.append(f'No image for source {n}')
      continue
    ui.append({'type': 'read', 'item': corpus.card(n, sources[n - 1])})
    images.append({'source': n, 'title': p['title'], 'page': p['page'], 'mediaType': 'image/webp',
                   'data': base64.b64encode(data).decode()})
  model = {'pages': [{k: v for k, v in im.items() if k != 'data'} for im in images]}
  if errors:
    model['errors'] = errors
  if len(wanted) > VIEW_MAX:
    model['note'] = f'At most {VIEW_MAX} page images per call.'
  return {'model': model, 'images': images, 'ui': ui}

"""Luna: retrieval augmented chat over the guideline library.

the model gets two tools, search and read. it searches, reads what it needs, answers with [n] citations.
yields ui events: tool_call, sources, read, text, done, error.
"""
import config
import corpus
import llm
import pageunits
import search

MAX_TOOL_ROUNDS = 3  # default search budget; a request may ask for 1..ROUNDS_LIMIT (evaluation ablations)
ROUNDS_LIMIT = 8
LAST_ROUND = ('That was your last search round: no further searches or page reads are possible. '
              'Write your answer now from the sources you found, with their citation numbers. '
              'If they do not answer the question, say so.')

SYSTEM = """You are Luna, an AI research assistant developed by the team at TIB (Leibniz Information Centre for Science and Technology). You answer questions about clinical guideline evidence on the heart and circulation, using a library of 204 guideline and patient-information documents (2,780 pages, English and German).

How to answer:
- For every medical question, call search_guidelines first with one precise query (search again only if the results clearly miss the topic; at most {{rounds}} searches). Use read_page when a snippet is cut off or you need the surrounding table or conditions.
- Each search result has passages (tables written as rows, "Class: I; Level: A") and, when available, a relevance between 0 and 1: how likely the page holds the specific answer. Prefer high-relevance pages; if the best relevance is low, search again with other words before you answer.
- Base medical statements only on retrieved sources. Cite them inline with their numbers in square brackets, one number per bracket, e.g. [1] or [2][3]. Never invent sources, page numbers, recommendation classes or evidence levels; report them only when the source text states them.
- If the sources do not answer the question, say so plainly.
- {languages}
- Be clear and well structured: a direct answer first, then the details in short bullet points or a small table. Use **bold** sparingly for key terms. Give every relevant detail the sources contain; as long as needed, usually 150 to 350 words.
- Distinguish what a guideline recommends from background description. Mention who a recommendation applies to and important conditions or exceptions.
- {research}
- Formatting: Markdown only. Write symbols as plain Unicode (≤, ≥, ±, µg), never LaTeX or $...$.
- If the user says they currently have severe chest pain, severe breathlessness or fainted, first tell them to call 112 immediately.""".format(languages=config.LANGUAGE_RULE, research=config.RESEARCH_RULE)

TOOLS = [
  llm.tool('search_guidelines',
           'Semantic search over all guideline pages (EmbeddingGemma 2, reranked by a relevance model). Returns numbered sources: '
           'document title, author/year if known, page number, relevance (0-1) and the best passages. '
           'Describe what you look for in a full phrase with the clinical terms, population and guideline if known.',
           {'query': {'type': 'string', 'description': 'Search query, e.g. "beta-blocker HFrEF recommendation class"'},
            'top_k': {'type': 'integer', 'description': 'Number of sources, 3-8 (default 5)'}}, ['query']),
  llm.tool('read_page', 'Read the full text of one page that was returned as a numbered source.',
           {'source': {'type': 'integer', 'description': 'Source number from an earlier search result'}}, ['source']),
]


def run(messages, rounds=None):
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
      final = rnd == rounds  # last round: no tools sent at all (Gemini on Vertex AI ignores tool_choice none), the model has to answer
      text, calls = '', []
      for kind, val in llm.stream(chat, None if final else TOOLS):
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
        else:
          results.append({'error': 'Unknown tool'})
      chat += llm.tool_turn(text, calls, results)
      if rnd == rounds - 1:  # the search budget is used up: say so, so the model plans its answer instead of searching on
        chat.append({'role': 'user', 'content': LAST_ROUND})
    if not answer.strip():  # every round ended in tool calls: answer from what was found
      for val in llm.answer_now(chat, 'Answer the question now from the sources you found. Do not search again.'):
        answer += val
        yield {'type': 'text', 'delta': val}
    yield {'type': 'done'}
  except Exception as e:
    yield llm.error_event(e)


def _search(args, sources, question=''):
  q = str(args.get('query', ''))[:300]
  k = max(3, min(8, int(args.get('top_k') or 5)))
  hits, mode = search.search(q, question=question[:1000] or None, k=k)
  cards, found = [], []
  for h in hits:
    i = h['page']
    if i not in sources:
      sources.append(i)
    n = sources.index(i) + 1
    cards.append({**corpus.card(n, i, q), 'relevance': h['relevance']})
    p = corpus.PAGES[i]
    hit = {'n': n, 'title': p['title'], 'ref': p.get('ref'), 'page': p['page'], 'relevance': h['relevance'],
           'passages': h['passages'] or [cards[-1].get('snippet')]}
    found.append({k2: v for k2, v in hit.items() if v not in (None, '', [])})
  yield {'type': 'sources', 'query': q, 'items': cards, 'mode': mode}
  return {'sources': found} if found else 'No matching pages. Try other terms.'


def _read(args, sources):
  n = int(args.get('source') or 0)
  if not 1 <= n <= len(sources):
    return {'error': f'Unknown source {n}'}
  p = corpus.PAGES[sources[n - 1]]
  yield {'type': 'read', 'item': corpus.card(n, sources[n - 1])}
  return {'source': n, 'title': p['title'], 'page': p['page'], 'text': pageunits.table_text(p['text'])[:12000]}

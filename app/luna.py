"""Luna: retrieval augmented chat over the guideline library.

the model gets two tools, search and read. it searches, reads what it needs, answers with [n] citations.
yields ui events: tool_call, sources, read, text, done, error.
"""
import config
import corpus
import llm

MAX_TOOL_ROUNDS = 3

SYSTEM = """You are Luna, an AI research assistant developed by the team at TIB (Leibniz Information Centre for Science and Technology). You answer questions about clinical guideline evidence, mainly on heart failure and cardiovascular care, using a library of 204 guideline and patient-information documents (2,780 pages, English and German).

How to answer:
- For every medical question, call search_guidelines first with one precise query (search again only if the results clearly miss the topic; at most 3 searches). Use read_page when a snippet is cut off or you need the surrounding table or conditions.
- Base medical statements only on retrieved sources. Cite them inline with their numbers in square brackets, one number per bracket, e.g. [1] or [2][3]. Never invent sources, page numbers, recommendation classes or evidence levels; report them only when the source text states them.
- If the sources do not answer the question, say so plainly.
- {languages}
- Be clear and well structured: a direct answer first, then short bullet points if helpful. Use **bold** sparingly for key terms. Keep it concise (usually under 180 words).
- Distinguish what a guideline recommends from background description. Mention who a recommendation applies to and important conditions or exceptions.
- You do not diagnose, prescribe, or give personal dosing. For personal decisions, encourage discussing with the care team.
- Formatting: Markdown only. Write symbols as plain Unicode (≤, ≥, ±, µg), never LaTeX or $...$.
- Never add disclaimers, prototype caveats, safety notes or emergency advice at the end of an answer. Exception: if the user says they currently have severe chest pain, severe breathlessness or fainted, tell them to call 112 immediately.""".format(languages=config.LANGUAGE_RULE)

TOOLS = [
  llm.tool('search_guidelines',
           'Full-text search over all guideline pages. Returns numbered sources (document title, author/year if known, page number, snippet). '
           'Use precise clinical terms; try synonyms or German terms if results are weak.',
           {'query': {'type': 'string', 'description': 'Search query, e.g. "beta-blocker HFrEF recommendation class"'},
            'top_k': {'type': 'integer', 'description': 'Number of sources, 3-8 (default 5)'}}, ['query']),
  llm.tool('read_page', 'Read the full text of one page that was returned as a numbered source.',
           {'source': {'type': 'integer', 'description': 'Source number from an earlier search result'}}, ['source']),
]


def run(messages):
  if not config.LLM_API_KEY:
    yield {'type': 'error', 'message': 'Chat is not configured (missing LLM_API_KEY).'}
    return
  chat = llm.history(messages, keep=12, max_chars=8000, system=SYSTEM)
  sources = []  # page indices, numbered 1..n across this answer
  try:
    for _ in range(MAX_TOOL_ROUNDS + 1):
      text, calls = '', []
      for kind, val in llm.stream(chat, TOOLS):
        if kind == 'text':
          text += val
          yield {'type': 'text', 'delta': val}
        else:
          calls = val
      if not calls:
        break
      results = []
      for call in calls:
        name, args = call['function']['name'], llm.args(call)
        yield {'type': 'tool_call', 'name': name, 'args': args}
        if name == 'search_guidelines':
          results.append((yield from _search(args, sources)))
        elif name == 'read_page':
          results.append((yield from _read(args, sources)))
        else:
          results.append({'error': 'Unknown tool'})
      chat += llm.tool_turn(text, calls, results)
    yield {'type': 'done'}
  except Exception as e:
    yield llm.error_event(e)


def _search(args, sources):
  q = str(args.get('query', ''))[:300]
  k = max(3, min(8, int(args.get('top_k') or 5)))
  cards = []
  for i in corpus.bm25(q, k):
    if i not in sources:
      sources.append(i)
    cards.append(corpus.card(sources.index(i) + 1, i, q))
  yield {'type': 'sources', 'query': q, 'items': cards}
  return {'sources': [{k2: c[k2] for k2 in ('n', 'title', 'ref', 'page', 'snippet') if c.get(k2)} for c in cards]
          or 'No matching pages. Try other terms.'}


def _read(args, sources):
  n = int(args.get('source') or 0)
  if not 1 <= n <= len(sources):
    return {'error': f'Unknown source {n}'}
  p = corpus.PAGES[sources[n - 1]]
  yield {'type': 'read', 'item': corpus.card(n, sources[n - 1])}
  return {'source': n, 'title': p['title'], 'page': p['page'], 'text': p['plain'][:7000]}

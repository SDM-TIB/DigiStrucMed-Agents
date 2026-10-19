"""Guido: the llm walks the guideline knowledge graph. Think-on-Graph for the walk, HippoRAG for where to start.

Think-on-Graph (Sun et al., ICLR 2024): start at the topic entities, explore relations, prune, repeat, answer from
the explored subgraph. its weak spot is the start: when no concept label matches, the walk finds nothing even though
the answer sits in a recommendation's text. so `explore` starts from retrieve.search: hybrid linking over concept
labels and recommendation text (lexical + dense, fused), then personalized pagerank from those seeds ranks the
recommendations closest to the question in the graph. `expand`, or another `explore` with other words, goes further.

every hop is also a `graph` event, the ui draws the walk live.
yields ui events: tool_call, graph, sources, text, done, error.
"""
import html
import re

import config
import corpus
import llm
import kg
import retrieve
from kg import CONCEPTS, IN, OUT, REC_ROLES, RECS

MAX_ROUNDS = 3  # default exploration budget; a request may ask for 1..ROUNDS_LIMIT (evaluation ablations)
ROUNDS_LIMIT = 8
LAST_ROUND = ('That was your last exploration round: no further graph calls are possible. '
              'Write your answer now from the explored graph, with the citation numbers of the recommendations. '
              'If the graph does not answer the question, say so.')
CITE = re.compile(r'\[(\d{1,2}(?:\s*,\s*\d{1,2})*)\]')

SYSTEM = (
  "You are Guido, an AI assistant developed by the team at TIB. You answer questions about clinical guideline evidence on the "
  "heart and circulation by traversing TIB's medical guideline knowledge graph (recommendations from cardiology guidelines linked to populations, "
  "conditions, drugs, procedures, side effects, with class of recommendation and level of evidence).\n"
  "Method: call explore with the key medical terms of the question in English, plus useful synonyms and the drug class "
  "(e.g. 'sacubitril valsartan ARNI side effects hypotension hyperkalaemia'). It links the terms to concepts and to "
  "recommendation texts and ranks the recommendations closest to them in the graph, with relations and connected concepts. "
  "If a relevant path continues beyond what you see, call expand on that node (concept id or recommendation number). "
  "If the results miss the point, call explore again with other words, a broader term or the drug class before you answer.\n"
  "Never leave the user empty-handed: if no recommendation answers the exact question, say what the guidelines do say "
  "about the closest related topic and cite it.\n"
  "Answer only from the explored graph. Cite recommendations inline as [n], one number per bracket. Report class of "
  "recommendation and level of evidence exactly as given; never invent them. Mention the graph path when it explains the "
  "answer (e.g. Aspirin → recommendation [3] → bleeding).\n"
  "Concepts list their terminology codes (UMLS, SNOMED CT, MeSH, RxNorm, LOINC, Wikidata) and how each was chosen: "
  "'selected' is a model's contextual choice over retrieval candidates, 'unconfirmed' only the top retrieval candidate. "
  "Name a code when the user asks for one or when it pins down which concept is meant; say how it was chosen, never "
  "present it as clinically verified.\n"
  + config.LANGUAGE_RULE + "\n"
  + config.RESEARCH_RULE.replace('search right away', 'explore the graph right away').replace('search again', 'explore again') + "\n"
  "Give every relevant detail the graph returns, well structured, usually 150 to 300 words. Markdown, symbols as plain Unicode. "
  "If the user describes acute emergency symptoms, first tell them to call 112."
)
# guido v2 (agent server): the same walk, the guideline pages part of the graph, and the pages readable
SYSTEM_V2 = SYSTEM.replace(
  "If the results miss the point, call explore again with other words, a broader term or the drug class before you answer.\n",
  "If the results miss the point, call explore again with other words, a broader term or the drug class.\n"
  "The guideline pages are nodes of the graph too (page nodes p:...): explore lists the pages where the concepts meet, a "
  "recommendation names its page, and expand on a page node shows the concepts and recommendations on it, so you can walk "
  "concept -> page -> recommendation and on. For exact values (doses, thresholds, durations, percentages, intervals) and "
  "for anything the recommendation texts do not state, call search_pages (semantic search over all guideline pages, the "
  "pages join the graph) and read_page to read a page as it was printed, tables included. Check every number you give "
  "on a page you read.\n").replace(
  "Answer only from the explored graph. Cite recommendations inline as [n], one number per bracket.",
  "Answer only from the explored graph and the pages you read. Cite recommendations and pages inline as [n], one number "
  "per bracket. Every sentence or bullet that states a medical fact, value or recommendation carries the number of the "
  "recommendation or page it comes from, also a short answer, a summary or a point that draws on several of them; "
  "sentences without a claim (a greeting, a transition, the advice to see a doctor) need no number.")
assert SYSTEM_V2 != SYSTEM and 'carries the number' in SYSTEM_V2

TOOLS = [
  llm.tool('explore', 'Link medical terms to the guideline knowledge graph (concepts and recommendation texts) and return the '
           'recommendations closest to them, ranked by personalized pagerank, with class/level of evidence, relations and connected concepts.',
           {'query': {'type': 'string', 'description': 'Key medical terms in English with synonyms, e.g. "aspirin dose secondary prevention antiplatelet"'}}, ['query']),
  llm.tool('expand', 'Go one hop further from a node already shown: a concept id (c:...) or a recommendation number.',
           {'node_id': {'type': 'string'}}, ['node_id']),
]


class Walk:
  """the explored subgraph of one answer: citation numbers and what the ui already has"""

  def __init__(self, question, pages=False):
    """pages: the guideline pages are part of the walk (Guido v2 on the agent server); without, the walk is the
    evaluated concept/recommendation walk of the local loop"""
    self.question, self.pages = question, pages
    self.qt = set(kg.toks(kg.expand(question)))
    self.numbered, self.shown = [], set()

  def num(self, rid):
    if rid not in self.numbered:
      self.numbered.append(rid)
    return self.numbered.index(rid) + 1

  def event(self, nodes, links, focus, action, hop):
    new = [kg.node_view(n, hop) for n in dict.fromkeys(nodes) if n not in self.shown]
    self.shown.update(nodes)
    return {'type': 'graph', 'nodes': new, 'links': [{'source': a, 'target': b, 'role': r} for a, b, r in links],
            'focus': focus, 'action': action, 'hop': hop}

  def rec_line(self, rid):
    r = RECS[rid]
    g = '; '.join(x for x in (kg.grade(r, '; '), ('direction ' + '/'.join(r['direction'])) if r['direction'] else '', r['ref'] or '') if x)
    where = f", on page node p:{r['page_id']}" if self.pages and r.get('page_id') else ''
    return f"[{self.num(rid)}] {r['text'][:320]} ({g}{where})"

  def page_line(self, pid, query=''):
    """a page node for the model: number, guideline and page, the concepts it is about, its recommendations, and
    the passage that matches the query"""
    p = kg.page(pid)
    about = ', '.join(kg.label(c) for c, _ in kg.page_concepts(pid, 6))
    recs = [f"[{self.num(r)}]" for r in kg.PAGE_RECS.get(pid, [])[:6]]
    text = corpus.snippet(p['plain'], query or self.question, 300) if p else ''
    return (f"[{self.num(pid)}] page {pid}: {kg.label(pid)} ({p['title'] if p else ''})" + (f"\n    about: {about}" if about else '')
            + (f"\n    recommendations on this page: {' '.join(recs)}" if recs else '') + (f"\n    text: {text}" if text else ''))

  def page_links(self, pid, extra=()):
    """graph edges from a page to the concepts already in view that it names, and from its recommendations"""
    names = {c for c, _ in kg.PAGE_CONCEPTS.get(pid, [])}
    links = [(pid, c, 'on page') for c in names if c in self.shown or c in extra][:6]
    links += [(r, pid, 'source page') for r in kg.PAGE_RECS.get(pid, []) if r in self.shown][:4]
    if not links:
      links = [(pid, c, 'on page') for c, _ in kg.page_concepts(pid, 2)]
    return links

  def neighbourhood(self, cid, events):
    """1-hop and 2-hop context of one concept as compact text for the model. ui events go into `events`"""
    name = CONCEPTS[cid]['label']
    desc = CONCEPTS[cid].get('desc')
    lines = [f"## {name} ({cid})" + (f": {desc}" if desc else '') + (f"\nCodes: {kg.codes(cid)}" if kg.codes(cid) else '')]
    rel_out = [(b, r) for b, r in OUT[cid] if r not in REC_ROLES][:8]
    rel_in = [(a, r) for a, r in IN[cid] if r not in REC_ROLES and not a.startswith('r:')][:8]
    recs = sorted({a for a, _ in IN[cid] if a.startswith('r:')}, key=lambda r: -kg.rec_score(r, self.qt))[:6]

    # hop 1: relations and the recommendations that mention it
    links = [(cid, b, r) for b, r in rel_out] + [(a, cid, r) for a, r in rel_in] + [(rid, cid, dict(OUT[rid]).get(cid, 'mention')) for rid in recs]
    events.append(self.event([b for b, _ in rel_out] + [a for a, _ in rel_in] + recs, links, cid, 'hop1', 1))
    if rel_out or rel_in:
      lines.append('Relations: ' + '; '.join([f"{name} --{r}--> {kg.label(b)}" for b, r in rel_out] +
                                             [f"{kg.label(a)} --{r}--> {name}" for a, r in rel_in]))
    if recs:
      lines.append('Recommendations mentioning it:')
      lines += ['  ' + self.rec_line(r) for r in recs]

    # hop 2: the other concepts of those recommendations, and relations of the related concepts
    nodes, links, text = [], [], []
    for rid in recs[:4]:
      others = [(b, role) for b, role in OUT[rid] if b in CONCEPTS and b != cid][:5]
      if others:
        text.append(f"  via [{self.num(rid)}]: " + ', '.join(f"{kg.label(b)} ({role})" for b, role in others))
        nodes += [b for b, _ in others]
        links += [(rid, b, role) for b, role in others]
    for b, _ in rel_out[:4] + rel_in[:4]:
      for c2, r2 in [(x, y) for x, y in OUT[b] if y not in REC_ROLES and x != cid][:2]:
        text.append(f"  {kg.label(b)} --{r2}--> {kg.label(c2)}")
        nodes.append(c2)
        links.append((b, c2, r2))
    if text:
      events.append(self.event(nodes, links, None, 'hop2', 2))
      lines += ['Two hops away:'] + text
    return '\n'.join(lines)

  def explore(self, query, question, events):
    """start where hybrid linking and pagerank say. ui events go into `events`, compact text goes to the model"""
    found = retrieve.search(query, question)
    seeds, matched, ranked = found['concepts'], found['matched'], found['recs']
    events.append(self.event(seeds, [], seeds[0] if seeds else None, 'seed', 0))
    if matched:  # recommendations whose own text matched, no concept label needed
      events.append(self.event(matched, [(r, c, role) for r in matched for c, role in OUT[r] if c in seeds], None, 'match', 1))
    lines = []
    for cid in seeds:
      c = CONCEPTS[cid]
      rel_out = [(cid, b, r) for b, r in OUT[cid] if r not in REC_ROLES][:6]
      rel_in = [(a, cid, r) for a, r in IN[cid] if r not in REC_ROLES and not a.startswith('r:')][:4]
      if rel_out or rel_in:
        events.append(self.event([b for _, b, _ in rel_out] + [a for a, _, _ in rel_in], rel_out + rel_in, cid, 'hop1', 1))
      relations = '; '.join(f"{kg.label(a)} --{r}--> {kg.label(b)}" for a, b, r in rel_out + rel_in)
      lines.append(f"## {c['label']} ({cid})" + (f": {c['desc']}" if c.get('desc') else '') + (f"\nCodes: {kg.codes(cid)}" if kg.codes(cid) else '')
                   + (f"\nRelations: {relations}" if relations else ''))
    if ranked:
      events.append(self.event(ranked, [(r, c, role) for r in ranked for c, role in OUT[r] if c in self.shown], None, 'rank', 1))
      lines.append('Recommendations closest to the question in the graph, best first:')
      nodes, links = [], []
      for rid in ranked:
        others = [(b, role) for b, role in OUT[rid] if b in CONCEPTS and b not in seeds][:4]
        linked = ', '.join(f"{kg.label(b)} ({role}, id {b})" for b, role in others)
        lines.append('  ' + self.rec_line(rid) + (f"\n    linked: {linked}" if linked else ''))
        if rid in ranked[:4]:
          nodes += [b for b, _ in others]
          links += [(rid, b, role) for b, role in others]
      if nodes:
        events.append(self.event(nodes, links, None, 'hop2', 2))
    if self.pages and (seeds or ranked):
      # candidates from the graph (pages where the start concepts meet, pages of the top recommendations), ordered
      # by how many of the question's words the page text holds; the graph proposes, the text decides
      rec_pages = ['p:' + RECS[r]['page_id'] for r in ranked if RECS[r].get('page_id')]
      cands = list(dict.fromkeys((kg.pages_for(seeds, k=20, prefer=rec_pages) if seeds else []) + rec_pages))
      words = set(kg.toks(kg.expand(query + ' ' + self.question)))
      fit = {x: len(words & set(kg.toks(kg.page(x)['plain']))) for x in cands if kg.page(x)}
      pids = sorted(fit, key=lambda x: (-fit[x], cands.index(x)))[:5]
      if pids:
        links = [lk for x in pids for lk in self.page_links(x, seeds)]
        events.append(self.event(pids + [b for _, b, _ in links], links, None, 'pages', 2))
        lines.append('Guideline pages where these concepts appear together (expand a page node to walk on from it, '
                     'read_page to read it):')
        lines += ['  ' + self.page_line(x, query) for x in pids]
    return '\n'.join(lines) or 'Nothing linked. Try other words, a synonym, the drug class or a broader term.'

  def expand(self, nid, events):
    nid = nid.strip().strip('[]')
    if nid.isdigit() and 1 <= int(nid) <= len(self.numbered):
      nid = self.numbered[int(nid) - 1]
    if self.pages and not nid.startswith(('p:', 'c:', 'r:')) and kg.page('p:' + nid):
      nid = 'p:' + nid
    if self.pages and nid.startswith('p:') and kg.page(nid):
      about = kg.page_concepts(nid, 10)
      recs = kg.PAGE_RECS.get(nid, [])[:8]
      links = [(nid, c, 'on page') for c, _ in about] + [(r, nid, 'source page') for r in recs]
      events.append(self.event([nid] + [c for c, _ in about] + recs, links, nid, 'expanded', 2))
      lines = [self.page_line(nid)]
      if about:
        lines.append('Concepts on this page (expand one to walk on): ' + '; '.join(f"{kg.label(c)} (id {c})" for c, _ in about))
      if recs:
        lines.append('Recommendations on this page:')
        lines += ['  ' + self.rec_line(r) for r in recs]
      return '\n'.join(lines)
    if nid in CONCEPTS:
      return self.neighbourhood(nid, events) + (self.concept_pages(nid, events) if self.pages else '')
    if nid in RECS:
      nbrs = OUT[nid][:12]
      events.append(self.event([b for b, _ in nbrs], [(nid, b, r) for b, r in nbrs], nid, 'expanded', 2))
      more = []
      for b, r in nbrs:
        if b in CONCEPTS:
          rel = [f"{kg.label(b)} --{r2}--> {kg.label(x)}" for x, r2 in OUT[b] if r2 not in REC_ROLES][:3]
          more.append(f"  {kg.label(b)} ({r}, id {b})" + ('; ' + '; '.join(rel) if rel else ''))
      return f"{self.rec_line(nid)}\nLinked concepts:\n" + '\n'.join(more)
    return f'Unknown node {nid}. Use concept ids (c:...) or recommendation numbers shown before.'

  def concept_pages(self, cid, events):
    """the pages most about one concept, as a further hop of expand"""
    pids = [x for x, _ in kg.CONCEPT_PAGES.get(cid, [])[:3] if kg.page(x)]
    if not pids:
      return ''
    events.append(self.event(pids, [(x, cid, 'on page') for x in pids], None, 'pages', 2))
    return '\nPages most about it:\n' + '\n'.join('  ' + self.page_line(x) for x in pids)

  def search_pages(self, query, events, progress=None):
    """Luna's two-stage page search (EmbeddingGemma 2 + clef-flash), its pages joined into the walk"""
    import search
    res = search.search(query, question=self.question[:1000] or None, k=5, progress=progress)
    pids = ['p:' + corpus.PAGES[h['page']]['id'] for h in res['hits']]
    if not pids:
      return 'No matching pages. Try other words.'
    links = [lk for x in pids for lk in self.page_links(x)]
    events.append(self.event(pids + [b for _, b, _ in links], links, None, 'pages', 1))
    return 'Pages found by the page search, best first:\n' + '\n'.join('  ' + self.page_line(x, query) for x in pids)

  def read_pages(self, refs, events):
    """the original OCR text of up to three pages (numbers or page node ids; a recommendation's number reads its
    page): Markdown with the HTML tables, signs like < and > kept"""
    out = []
    for ref in list(dict.fromkeys(str(x).strip().strip('[]') for x in refs))[:3]:
      nid = self.numbered[int(ref) - 1] if ref.isdigit() and 1 <= int(ref) <= len(self.numbered) else ref
      if nid in RECS and RECS[nid].get('page_id'):
        nid = 'p:' + RECS[nid]['page_id']
      if not nid.startswith('p:'):
        nid = 'p:' + nid
      p, i = kg.page(nid), kg.page_index(nid)
      if not p:
        out.append({'source': ref, 'error': 'unknown page'})
        continue
      n = self.num(nid)
      events.append({'type': 'read', 'item': corpus.card(n, i)})
      out.append({'source': n, 'page_node': nid, 'title': p['title'], 'page': p['page'], 'original': html.unescape(p['text'])[:32000]})
    return {'pages': out}

  def sources(self):
    out = []
    for i, nid in enumerate(self.numbered):
      if nid.startswith('p:'):
        idx = kg.page_index(nid)
        if idx is not None:
          out.append(corpus.card(i + 1, idx, self.question))
        continue
      r = RECS[nid]
      p = kg.page('p:' + r['page_id']) if self.pages and r.get('page_id') else None
      out.append({'n': i + 1, 'page_id': p['id'] if p else None, 'rec_id': nid, 'title': r['ref'] or 'Guideline knowledge graph',
                  'ref': r['ref'], 'page': p['page'] if p else None, 'snippet': r['text']})
    return out


def run(messages, rounds=None, harness=None):
  """guido v2 on the agent server when the harness is 'mastra' (request, or GUIDO_HARNESS); otherwise, or when the
  agent server cannot be reached before it answers, the evaluated local loop below"""
  if (harness or config.GUIDO_HARNESS) == 'mastra' and config.AGENT_URL:
    started = False
    try:
      for ev in _via_agent(messages, rounds):
        started = True
        yield ev
      return
    except Exception as e:
      if started:
        yield llm.error_event(e, 'Agent')
        return
      print('guido: agent server unavailable, local loop:', type(e).__name__, flush=True)
  yield from _local(messages, rounds)


def _via_agent(messages, rounds):
  """the agent's stream; at its end the cited nodes for the graph view, from the walk the tool endpoints kept"""
  import uuid

  import agent_tools
  import luna
  run, answer = uuid.uuid4().hex, ''
  for ev in luna._via_agent(messages, rounds, config.AGENT_URL, agent='guido', system=SYSTEM_V2, run=run):
    if ev.get('type') == 'text':
      answer += ev.get('delta', '')
    if ev.get('type') == 'done':
      walk = agent_tools.walk(run)
      if walk:
        cited = sorted({int(n) for grp in CITE.findall(answer) for n in re.findall(r'\d+', grp) if 0 < int(n) <= len(walk.numbered)})
        yield {'type': 'graph', 'nodes': [], 'links': [], 'cited': [walk.numbered[n - 1] for n in cited], 'action': 'done'}
    yield ev


def _local(messages, rounds=None):
  if not config.LLM_API_KEY:
    yield {'type': 'error', 'message': 'Chat is not configured (missing LLM_API_KEY).'}
    return
  question = next((m['text'] for m in reversed(messages) if m.get('role') == 'user' and m.get('text')), '')
  walk = Walk(question)
  rounds = llm.rounds(rounds, MAX_ROUNDS, ROUNDS_LIMIT)
  chat = llm.history(messages, keep=10, max_chars=6000, system=SYSTEM)
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
        chat += [{'role': 'assistant', 'content': text or '...'}, {'role': 'user', 'content': 'Answer now from the explored graph.'}]
        continue
      results = []
      for call in calls:
        results.append((yield from _tool(call, walk, question)))
      chat += llm.tool_turn(text, calls, results)
      if rnd == rounds - 1:  # the exploration budget is used up: say so, so the model plans its answer instead of exploring on
        chat.append({'role': 'user', 'content': LAST_ROUND})
    if not answer.strip():  # every round ended in tool calls: answer from the explored graph
      for val in llm.answer_now(chat, 'Answer the question now from the explored graph. Do not call tools again.'):
        answer += val
        yield {'type': 'text', 'delta': val}
    cited = sorted({int(n) for grp in CITE.findall(answer) for n in re.findall(r'\d+', grp) if 0 < int(n) <= len(walk.numbered)})
    yield {'type': 'graph', 'nodes': [], 'links': [], 'cited': [walk.numbered[n - 1] for n in cited], 'action': 'done'}
    yield {'type': 'done'}
  except Exception as e:
    if answer.strip() or not walk.numbered:
      yield llm.error_event(e)
      return
    # the model is out of quota but the walk already found evidence: hand that over instead of an error
    top = walk.numbered[:3]
    yield {'type': 'text', 'delta': 'The language model is busy right now, so here is what the knowledge graph found:\n\n' +
           '\n'.join(f"* {RECS[r]['text'][:300].strip()} [{i + 1}]" for i, r in enumerate(top))}
    yield {'type': 'graph', 'nodes': [], 'links': [], 'cited': top, 'action': 'done'}
    yield {'type': 'done'}


def _tool(call, walk, question):
  name, args, events = call['function']['name'], llm.args(call), []
  if name == 'explore':
    q = str(args.get('query', ''))[:200]
    yield {'type': 'tool_call', 'name': 'explore', 'args': {'query': q}}
    result = walk.explore(q, question, events)
  elif name == 'expand':
    nid = str(args.get('node_id', '')).strip()
    yield {'type': 'tool_call', 'name': 'expand', 'args': {'label': kg.label(nid) if nid in CONCEPTS else nid}}
    result = walk.expand(nid, events)
  else:
    result = 'Unknown tool'
  yield from events
  yield {'type': 'sources', 'query': name, 'items': walk.sources()}
  return result


UNITS = {}  # semantic unit -> its recommendations, the siblings that place a text unit the graph gives no page for
for _r in RECS.values():
  UNITS.setdefault(_r['id'].rsplit('/tu_', 1)[0], []).append(_r)


def evidence(rid):
  """a recommendation with the guideline page and the original table it came from"""
  r = RECS.get(rid)
  if not r:
    return None
  page, table, found = corpus.source(r, [x['text'] for x in UNITS.get(rid.rsplit('/tu_', 1)[0], []) if x is not r])
  if page is None:
    return {'rec': rid, 'text': r['text'], 'ref': r['ref'], 'page_id': None}
  p = corpus.PAGES[page]
  return {'rec': rid, 'text': r['text'], 'class': r['class'], 'loe': r['loe'], 'ref': r['ref'], 'title': p['title'],
          'page': p['page'], 'page_id': p['id'], 'table_html': table, 'match': r['text'][:60], 'found': found}

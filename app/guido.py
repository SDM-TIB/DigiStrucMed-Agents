"""Guido: the llm walks the guideline knowledge graph. Think-on-Graph for the walk, HippoRAG for where to start.

Think-on-Graph (Sun et al., ICLR 2024): start at the topic entities, explore relations, prune, repeat, answer from
the explored subgraph. its weak spot is the start: when no concept label matches, the walk finds nothing even though
the answer sits in a recommendation's text. so `explore` starts from retrieve.search: hybrid linking over concept
labels and recommendation text (lexical + dense, fused), then personalized pagerank from those seeds ranks the
recommendations closest to the question in the graph. `expand`, or another `explore` with other words, goes further.

every hop is also a `graph` event, the ui draws the walk live.
yields ui events: tool_call, graph, sources, text, done, error.
"""
import re

import config
import corpus
import llm
import kg
import retrieve
import wikidata
from kg import CONCEPTS, IN, OUT, REC_ROLES, RECS

MAX_ROUNDS = 3
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
  + config.LANGUAGE_RULE + "\n"
  + config.RESEARCH_RULE.replace('search right away', 'explore the graph right away').replace('search again', 'explore again') + "\n"
  "Give every relevant detail the graph returns, well structured, usually 150 to 300 words. Markdown, symbols as plain Unicode. "
  "If the user describes acute emergency symptoms, first tell them to call 112."
)
TOOLS = [
  llm.tool('explore', 'Link medical terms to the guideline knowledge graph (concepts and recommendation texts) and return the '
           'recommendations closest to them, ranked by personalized pagerank, with class/level of evidence, relations and connected concepts.',
           {'query': {'type': 'string', 'description': 'Key medical terms in English with synonyms, e.g. "aspirin dose secondary prevention antiplatelet"'}}, ['query']),
  llm.tool('expand', 'Go one hop further from a node already shown: a concept id (c:...) or a recommendation number.',
           {'node_id': {'type': 'string'}}, ['node_id']),
]


class Walk:
  """the explored subgraph of one answer: citation numbers and what the ui already has"""

  def __init__(self, question):
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
    return f"[{self.num(rid)}] {r['text'][:320]} ({g})"

  def neighbourhood(self, cid, events):
    """1-hop and 2-hop context of one concept as compact text for the model. ui events go into `events`"""
    name = CONCEPTS[cid]['label']
    desc = CONCEPTS[cid].get('desc')
    lines = [f"## {name} ({cid})" + (f": {desc}" if desc else '')]
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
    wikidata.prefetch(seeds[:4])  # the call's inspector opens one of these a few seconds from now
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
      lines.append(f"## {c['label']} ({cid})" + (f": {c['desc']}" if c.get('desc') else '') + (f"\nRelations: {relations}" if relations else ''))
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
    return '\n'.join(lines) or 'Nothing linked. Try other words, a synonym, the drug class or a broader term.'

  def expand(self, nid, events):
    if nid.isdigit() and 1 <= int(nid) <= len(self.numbered):
      nid = self.numbered[int(nid) - 1]
    if nid in CONCEPTS:
      return self.neighbourhood(nid, events)
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

  def sources(self):
    return [{'n': i + 1, 'page_id': None, 'rec_id': r, 'title': RECS[r]['ref'] or 'Guideline knowledge graph',
             'ref': RECS[r]['ref'], 'page': None, 'snippet': RECS[r]['text']} for i, r in enumerate(self.numbered)]


def run(messages):
  if not config.LLM_API_KEY:
    yield {'type': 'error', 'message': 'Chat is not configured (missing LLM_API_KEY).'}
    return
  question = next((m['text'] for m in reversed(messages) if m.get('role') == 'user' and m.get('text')), '')
  walk = Walk(question)
  chat = llm.history(messages, keep=10, max_chars=6000, system=SYSTEM)
  answer = ''
  try:
    for rnd in range(MAX_ROUNDS + 1):
      final = rnd == MAX_ROUNDS  # last round: tools off, the model has to answer
      text, calls = '', []
      for kind, val in llm.stream(chat, TOOLS, 'none' if final else None):
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


def evidence(rid):
  """a recommendation with the guideline page and the original table it came from"""
  r = RECS.get(rid)
  if not r:
    return None
  page, table = corpus.locate(r['text'], r['ref'])
  if page is None:
    return {'rec': rid, 'text': r['text'], 'ref': r['ref'], 'page_id': None}
  p = corpus.PAGES[page]
  return {'rec': rid, 'text': r['text'], 'class': r['class'], 'loe': r['loe'], 'ref': r['ref'], 'title': p['title'],
          'page': p['page'], 'page_id': p['id'], 'table_html': table, 'match': r['text'][:60]}

"""Guido: Think-on-Graph over the guideline knowledge graph, with the llm as the walker.

Think-on-Graph (Sun et al., ICLR 2024): start at the topic entities, explore relations, prune, repeat, answer from
the explored subgraph. we cut the latency by handing the model the whole 1-hop and 2-hop neighbourhood in one
`explore` call. `expand` goes further when it needs to. most questions are done after one tool call.

every hop is also a `graph` event, the ui draws the walk live.
yields ui events: tool_call, graph, sources, text, done, error.
"""
import re

import config
import corpus
import llm
import kg
from kg import CONCEPTS, IN, OUT, REC_ROLES, RECS

MAX_ROUNDS = 3
CITE = re.compile(r'\[(\d{1,2}(?:\s*,\s*\d{1,2})*)\]')

SYSTEM = (
  "You are Guido, an AI assistant developed by the team at TIB. You answer questions about clinical guideline evidence "
  "by traversing TIB's medical guideline knowledge graph (recommendations from cardiology guidelines linked to populations, "
  "conditions, drugs, procedures, side effects, with class of recommendation and level of evidence).\n"
  "Method: call explore once with the key medical terms of the question. It returns the matching concepts with their "
  "1-hop and 2-hop neighbourhood. Read it; if a relevant path continues beyond what you see, call expand on that node "
  "(concept id or recommendation number), possibly several in parallel. Then answer. Usually one explore is enough.\n"
  "Answer only from the explored graph. Cite recommendations inline as [n], one number per bracket. Report class of "
  "recommendation and level of evidence exactly as given; never invent them. Mention the graph path when it explains the "
  "answer (e.g. Aspirin → recommendation [3] → bleeding). If the graph does not contain the answer, say so.\n"
  + config.LANGUAGE_RULE + "\n"
  + config.HELP_RULE.replace('search right away', 'explore the graph right away') + "\n"
  "Be concise (usually under 150 words), Markdown, symbols as plain Unicode. Never add disclaimers. "
  "Only if the user describes acute emergency symptoms, tell them to call 112."
)
TOOLS = [
  llm.tool('explore', 'Find the concepts for medical terms in the guideline knowledge graph and return their 1-hop and 2-hop neighbourhood '
           '(relations, recommendations with class/level of evidence, connected concepts).',
           {'query': {'type': 'string', 'description': 'Key medical terms of the question, e.g. "aspirin complications bleeding"'}}, ['query']),
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
    yield llm.error_event(e)


def _tool(call, walk, question):
  name, args, events = call['function']['name'], llm.args(call), []
  if name == 'explore':
    q = str(args.get('query', ''))[:200]
    seeds = kg.find_concepts(q + ' ' + question)
    yield {'type': 'tool_call', 'name': 'explore', 'args': {'query': q}}
    yield walk.event(seeds, [], seeds[0] if seeds else None, 'seed', 0)
    result = '\n\n'.join(walk.neighbourhood(c, events) for c in seeds) or 'No matching concepts in the graph.'
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

"""Guido's working view of the final MHH guideline graph, in memory. 9k concepts, 4k recommendations, 23k edges.

node ids:  c:<entity>  concept     r:<text unit>  recommendation     g:<guideline>  source guideline
edges:     rec -> concept  (population | target | condition | parameter | mention)
           rec -> guide    (source)
           concept -> concept  (subclass of, treated with, has symptom, ...)
loaded once at startup from the graph server (graph.agent_view), the same graph the explorer shows. every concept
carries its link set per terminology (UMLS, SNOMED CT, MeSH, RxNorm, LOINC, Wikidata) from the graph's entity-links.
"""
import math
import re
from collections import Counter, defaultdict

import graph

_g = graph.load_agent_view()
CONCEPTS = {c['id']: c for c in _g['concepts']}
RECS = {r['id']: r for r in _g['recs']}
GUIDES = {g['id']: g for g in _g['guides']}
OUT, IN = defaultdict(list), defaultdict(list)
for a, b, role in _g['edges']:
  OUT[a].append((b, role))
  IN[b].append((a, role))
del _g
REC_ROLES = {'population', 'target', 'condition', 'parameter', 'mention'}
CODE_NAME = {'umls': 'UMLS', 'snomed': 'SNOMED CT', 'mesh': 'MeSH', 'rxnorm': 'RxNorm', 'loinc': 'LOINC', 'wikidata': 'Wikidata'}
SELECTORS = (('clef', 'CLEF'), ('gemini', 'Gemini 2.5 Flash Lite'), ('luna', 'Luna'))


def selector(method):
  """who chose, from a method id of the graph: 'entity_vote:page_surface' records how the concept's mentions voted;
  the mention-level methods name the model (clef_native_selector..., gemini_batch..., luna_top10...)"""
  return next((n for key, n in SELECTORS if key in method.lower()), method.replace('_', ' '))


def codes(cid):
  """one line for the agent: the concept's selected terminology codes with their support and runners-up, then the
  unconfirmed Top3 candidates, marked, so they stay reachable but read as unchecked"""
  links = (CONCEPTS.get(cid) or {}).get('codes') or {}
  chosen, unconfirmed = [], []
  for source, name in CODE_NAME.items():
    ls = links.get(source)
    if not ls or not ls['concepts']:
      continue
    rec = ls['record']
    if ls['status'] == 'selected_top1':
      top = ls['concepts'][0]
      runners = [c for c in rec.get('selected_votes', {}) if c != top['id']][:3]
      chosen.append(f"{name} {top['id']}" + (f" {top['name']}" if top.get('name') else '')
                    + f" (selected for {rec.get('support')} of {rec.get('votes')} matching mentions"
                    + (f"; alternatives {', '.join(runners)}" if runners else '') + ')')
    elif ls['status'] == 'unconfirmed_top3':
      unconfirmed.append(f"{name} " + '/'.join(c['id'] for c in ls['concepts']))
  if unconfirmed:
    chosen.append('unconfirmed retrieval candidates, unchecked and possibly wrong: ' + ', '.join(unconfirmed))
  return '; '.join(chosen)


TOK = re.compile(r'[a-z0-9]+')
STOP = set(('the a an and or of in on to for with by is are be as at from that this these it its not no can may should what which '
            'who how does do i my me about patients patient without there any you your tell know give explain please').split())
# the graph speaks in abbreviations, people don't
SYNONYMS = [(r'reduced ejection fraction', ' hfref'), (r'preserved ejection fraction', ' hfpef'),
            (r'dapagliflozin|empagliflozin', ' sglt2 inhibitors'), (r'sacubitril|valsartan|neprilysin', ' arni'),
            (r'spironolactone|eplerenone|mineralocorticoid', ' mra'), (r'beta.?blocker|bisoprolol|metoprolol|carvedilol', ' beta-blockers'),
            (r'atrial fibrillation', ' af'), (r'defibrillator', ' icd'), (r'exercise training|physical activity', ' exercise rehabilitation'),
            (r'complication|side effect|adverse|risk', ' bleeding adverse events side effects')]


def toks(text):
  return [t for t in TOK.findall(text.lower()) if t not in STOP and len(t) > 1]


def expand(query):
  q = query.lower()
  for pattern, extra in SYNONYMS:
    if re.search(pattern, q):
      q += extra
  return q


def n_recs(cid):
  return sum(1 for a, _ in IN[cid] if a.startswith('r:'))


def grade(r, sep=' · ', loe='LoE '):
  return sep.join(x for x in (('Class ' + '/'.join(r['class'])) if r['class'] else '', (loe + '/'.join(r['loe'])) if r['loe'] else '') if x)


def label(nid):
  if nid in CONCEPTS:
    return CONCEPTS[nid]['label']
  if nid in GUIDES:
    return GUIDES[nid]['label']
  return nid


def rec_score(rid, query_tokens):
  r = RECS[rid]
  return len(set(toks(r['text'])) & query_tokens) + (1.5 if r['class'] else 0) + (1.0 if r['loe'] else 0)


# ---- finding start concepts
_index = defaultdict(set)
for _cid, _c in CONCEPTS.items():
  for _t in toks(' '.join([_c['label']] + _c['aliases'])):
    _index[_t].add(_cid)
_df = {t: len(v) for t, v in _index.items()}


def find_concepts(query, k=4):
  """concepts a question is about. idf over label tokens, a big bonus for a full name match"""
  q = expand(query)
  scores = Counter()
  for t in set(toks(q)):
    for cid in _index.get(t, ()):
      scores[cid] += math.log(1 + len(CONCEPTS) / (1 + _df[t]))
  for cid in list(scores):
    c = CONCEPTS[cid]
    if len(toks(c['label'])) > 6:  # sentence-like labels are extraction artefacts, not concepts
      del scores[cid]
      continue
    if any(re.search(r'\b' + re.escape(n) + r'\b', q) for n in [c['label'].lower()] + [a.lower() for a in c['aliases']] if len(n) > 2):
      scores[cid] += 6
    scores[cid] += 0.6 * math.log(1 + n_recs(cid)) - 0.3 * len(toks(c['label']))
  top = scores.most_common(1)[0][1] if scores else 0
  out = []
  for cid, sc in scores.most_common(20):
    if sc < 0.45 * top:
      break  # weak partial matches are not start concepts
    if n_recs(cid) or OUT[cid] or IN[cid]:
      out.append(cid)
    if len(out) >= k:
      break
  return out


# ---- views for the ui
def node_view(nid, hop):
  if nid in RECS:
    r = RECS[nid]
    g = grade(r)
    return {'id': nid, 'kind': 'rec', 'label': (r['ref'] or 'Recommendation') + (' · ' + g if g else ''), 'text': r['text'][:220], 'hop': hop}
  if nid in GUIDES:
    return {'id': nid, 'kind': 'guide', 'label': GUIDES[nid]['label'], 'hop': hop}
  return {'id': nid, 'kind': 'concept', 'label': CONCEPTS[nid]['label'], 'hop': hop}


def detail(nid):
  """one node with everything the explorer side panel shows"""
  v = node_view(nid, 0)
  if nid in RECS:
    r = RECS[nid]
    v.update(text=r['text'], cls=r['class'], loe=r['loe'], direction=r.get('direction') or [], ref=r['ref'], iri=r.get('iri'))
  elif nid in CONCEPTS:
    c = CONCEPTS[nid]
    v.update(desc=c.get('desc') or '', n_recs=n_recs(nid), aliases=c['aliases'][:6], codes=codes(nid), iri=c.get('iri'))
  return v


def neighbours(nid, limit=36):
  """direct neighbours in both directions. concepts first, then recommendations by grade"""
  if nid not in CONCEPTS and nid not in RECS and nid not in GUIDES:
    return None
  other = lambda e: e[1] if e[0] == nid else e[0]
  edges = [(nid, b, r) for b, r in OUT[nid]] + [(a, nid, r) for a, r in IN[nid]]
  edges = list({other(e): e for e in edges}.values())
  edges.sort(key=lambda e: (other(e).startswith('r:'), -(rec_score(other(e), set()) if other(e).startswith('r:') else 0)))
  return {'node': detail(nid), 'total': len(edges),
          'nodes': [node_view(other(e), 1) for e in edges[:limit]],
          'links': [{'source': a, 'target': b, 'role': r} for a, b, r in edges[:limit]]}


def search(q, k=12):
  """concepts and recommendations for a search box"""
  qt = toks(q)
  if not qt:
    return []
  hits = find_concepts(q, k=k)
  by_label = [cid for cid, c in CONCEPTS.items() if all(t in c['label'].lower() for t in qt) and cid not in hits]
  hits += sorted(by_label, key=lambda c: (-n_recs(c), len(CONCEPTS[c]['label'])))[:k - len(hits)]
  recs = [rid for rid, r in RECS.items() if all(t in r['text'].lower() for t in qt)]
  # the same recommendation is often extracted from several tables. keep one per text prefix
  recs = list({re.sub(r'[^a-z0-9]', '', RECS[r]['text'].lower())[:70]: r for r in sorted(recs, key=lambda r: rec_score(r, set(qt)))}.values())
  recs = sorted(recs, key=lambda r: -rec_score(r, set(qt)))[:5]
  return [detail(x) for x in hits[:k] + recs]


# ---- matching outside labels (wikidata) back into the graph
_norm = lambda t: re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9]+', ' ', t.lower())).strip()
_by_label = {}
for _cid, _c in CONCEPTS.items():
  for _t in [_c['label']] + _c['aliases']:
    _k = _norm(_t)
    if len(_k) > 2 and (_k not in _by_label or n_recs(_cid) > n_recs(_by_label[_k])):
      _by_label[_k] = _cid


def concept_for_label(text):
  """'(RS)-warfarin' -> c:warfarin, or None"""
  return _by_label.get(_norm(re.sub(r'^\([^)]*\)-', '', text or '')))

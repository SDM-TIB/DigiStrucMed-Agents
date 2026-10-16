"""The guideline knowledge graph, in memory. 9k concepts, 4k recommendations, 23k edges. fits in a dict.

node ids:  c:<entity>  concept     r:<text unit>  recommendation     g:<guideline>  source guideline
edges:     rec -> concept  (population | target | condition | parameter | mention)
           rec -> guide    (source)
           concept -> concept  (subclass of, treated with, has symptom, ...)
built by pipeline/build_kg_graph.py from guidelineKG.nt
"""
import gzip
import json
import math
import re
from collections import Counter, defaultdict

import config

with gzip.open(config.DATA / 'kg_graph.json.gz', 'rt', encoding='utf-8') as fh:
  _g = json.load(fh)
CONCEPTS = {c['id']: c for c in _g['concepts']}
RECS = {r['id']: r for r in _g['recs']}
GUIDES = {g['id']: g for g in _g['guides']}
OUT, IN = defaultdict(list), defaultdict(list)
for a, b, role in _g['edges']:
  OUT[a].append((b, role))
  IN[b].append((a, role))
del _g
REC_ROLES = {'population', 'target', 'condition', 'parameter', 'mention'}

# terminology codes per concept from the final MHH graph (pipeline/build_kg_links.py): our selections first
_links_path = config.DATA / 'kg_links.json.gz'
LINKS = json.load(gzip.open(_links_path, 'rt', encoding='utf-8')) if _links_path.exists() else {}
CODE_NAME = {'umls': 'UMLS', 'snomed': 'SNOMED CT', 'mesh': 'MeSH', 'rxnorm': 'RxNorm', 'loinc': 'LOINC', 'wikidata': 'Wikidata'}


def codes(cid):
  """one line for the agent: the selected terminology codes of a concept with how they were chosen and their
  runners-up, then the unconfirmed retrieval candidates, bare and marked, so they stay reachable but read as unchecked"""
  e = LINKS.get(cid, {})
  chosen, unconfirmed = [], []
  for source, name in CODE_NAME.items():
    link = (e.get('mhh') or {}).get(source)
    if not link:
      continue
    code = link.get('cui') or link.get('qid') or link.get('id')
    if link['status'] == 'selected':
      how = f"selected by {link.get('selector') or link.get('method', '')}, {link['support']}/{link['mentions']} mentions"
      alt = f"; alternatives {', '.join(link['alternatives'])}" if link.get('alternatives') else ''
      chosen.append(f"{name} {code}" + (f" {link['name']}" if link.get('name') else '') + f" ({how}{alt})")
    else:
      unconfirmed.append(f"{name} {code}")
  graph = e.get('kg') or {}
  if graph.get('cui') and graph['cui'] != ((e.get('mhh') or {}).get('umls') or {}).get('cui'):
    chosen.append(f"colleague graph UMLS {graph['cui']}")
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
    v.update(text=r['text'], cls=r['class'], loe=r['loe'], direction=r.get('direction') or [], ref=r['ref'])
  elif nid in CONCEPTS:
    c = CONCEPTS[nid]
    v.update(desc=c.get('desc') or '', n_recs=n_recs(nid), aliases=c['aliases'][:6], codes=codes(nid))
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

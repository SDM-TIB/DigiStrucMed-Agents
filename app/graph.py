"""The final MHH guideline graph, read over SPARQL from the graph server (QLever, graph-server/). The app keeps no copy of
the graph: every concept, recommendation, terminology code and source page the agents and the ui show comes from here.

  sparql(q)           one SELECT, rows as dicts of plain values
  agent_view()        the working view Guido walks (kg.py): recommendations with their grades and roles, the concepts they
                      name, the relations between concepts, and every concept's link set per terminology
  search / node / neighbours / kinds   the graph explorer (Graph tab)

node kinds come from the graph's IRI scheme (KINDS), so every node has exactly one kind, typed or not.
"""
import collections
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import config

DS, M, F = 'http://digistrucmed.org/', 'urn:mhh:integration:20261011:', 'urn:mhh:final:20261016:'
RDF_TYPE, RDFS_LABEL = 'http://www.w3.org/1999/02/22-rdf-syntax-ns#type', 'http://www.w3.org/2000/01/rdf-schema#label'
PREFIXES = (f'PREFIX ds: <{DS}>\nPREFIX m: <{M}>\nPREFIX f: <{F}>\nPREFIX ql: <http://qlever.cs.uni-freiburg.de/builtin-functions/>\n'
            'PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>\nPREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>\n')
SOURCES = ('umls', 'snomed', 'mesh', 'rxnorm', 'loinc', 'wikidata')

# kind: (IRI prefix, display name, colour, search predicate, how its nodes are counted). the count is a graph pattern
# over ?n, cheap for QLever: the kind's own rdf:type where every node carries it, else the predicate that names them
KINDS = {
  'guideline': (DS + 'guideline/', 'Guideline', '#7c5cff', None, '?n a m:SourceDocument'),
  'page': (DS + 'page/', 'Page', '#4f7cff', None, '?n a ds:Page'),
  'unit': (DS + 'semantic-unit/', 'Semantic unit (table, section)', '#2f9e9e', 'ds:titleOrContext', '?n a ds:SemanticUnit'),
  'recommendation': (DS + 'text-unit/', 'Recommendation / statement', '#1f8a5b', 'ds:verbatimText', '?n a ds:TextUnit'),
  'assertion': (DS + 'normative-assertion/', 'Normative assertion', '#5aa469', None, '?n a ds:NormativeAssertion'),
  'grade': (DS + 'grade-record/', 'Grade record', '#9aa64a', None, '?n a ds:SourceGradeRecord'),
  'concept': (DS + 'entity/', 'Concept', '#e08a00', 'rdfs:label', '?n rdfs:label ?l . FILTER STRSTARTS(STR(?n), "' + DS + 'entity/")'),
  'mention': (DS + 'entity-mention/', 'Entity mention', '#f2b544', 'rdfs:label', '?n a ds:ExtractedEntity'),
  'query': (M + 'query:', 'Page mention', '#e0567a', 'f:surfaceForm', '?n a m:RetrievalQuery'),
  'variant': (M + 'query-variant:', 'Page mention source variant', '#c98aa0', None, '?q m:hasSourceVariant ?n'),
  'occurrence': (M + 'occurrence:', 'Occurrence on a page', '#d48da5', None, '?n a ds:TextOccurrence'),
  'unitmention': (F + 'unit-mention:', 'Level-4 mention', '#c2410c', 'f:surfaceForm', '?n a f:Level4UnitMention'),
  'l4entity': (M + 'entity:', 'Level-4 entity', '#b45309', None, None),
  'annotation': (M + 'annotation:', 'Level-4 annotation', '#a16207', None, '?x m:hasAnnotation ?n'),
  'relation': (M + 'factual-relation:', 'Factual relation', '#8b5e34', None, '?n a ds:FactualRelation'),
  'linkset': (F + 'link-set:', 'Link set (concept linking)', '#64748b', None, '?n a f:EffectiveConceptLinkSet'),
  'code': (M + 'catalogue-entry:', 'Terminology code', '#0ea5e9', 'rdfs:label', '?n a f:CatalogueEntry'),
}
_ORDER = sorted(KINDS.items(), key=lambda kv: -len(kv[1][0]))


class GraphUnavailable(RuntimeError):
  pass


def sparql(query, timeout=60):
  data = urllib.parse.urlencode({'query': PREFIXES + query}).encode()
  req = urllib.request.Request(config.GRAPH_ENDPOINT, data=data, method='POST',
                               headers={'Accept': 'application/sparql-results+json', 'Content-Type': 'application/x-www-form-urlencoded'})
  try:
    with urllib.request.urlopen(req, timeout=timeout) as r:
      res = json.load(r)
  except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
    raise GraphUnavailable(f'graph server: {e}') from None
  names = res['head']['vars']
  return [{v: b[v]['value'] for v in names if v in b} for b in res['results']['bindings']]


def iri(x):
  """an IRI for a query: only what an IRI may contain, so nothing a client sends can change the query"""
  if not re.fullmatch(r'[^\s<>"{}|\\^`]+', x or ''):
    raise ValueError('not an IRI')
  return f'<{x}>'


def lit(text):
  return json.dumps(str(text), ensure_ascii=False)


def kind_of(node):
  for k, (prefix, *_rest) in _ORDER:
    if node.startswith(prefix):
      return k
  return 'other'


def local(node):
  return urllib.parse.unquote(re.split(r'[/#:]', node)[-1])


# ---------------------------------------------------------------- the working view of the agents

VALID_CLASS = re.compile(r'^(I|IIa|IIb|III|1|2a|2b|3)$', re.I)
VALID_LOE = re.compile(r'^(A|B|B-R|B-NR|C|C-LD|C-EO)$', re.I)
ROLES = {DS + 'hasPopulationEntity': 'population', DS + 'hasTargetEntity': 'target', DS + 'hasConditionEntity': 'condition',
         DS + 'hasParameterEntity': 'parameter'}
RELATIONS = {DS + 'subclass_of': 'subclass of', DS + 'instance_of': 'instance of', DS + 'drug_or_therapy_used_for_treatment': 'treated with',
             DS + 'symptoms': 'has symptom', DS + 'health_specialty': 'medical specialty', DS + 'main_subject': 'main subject'}
BLOCK = re.compile(r'^pb_[0-9a-f]{64}_p\d{4}_g\d{3}$')


def _grades(iris, prefix, valid):
  vals = [i.rsplit('/', 1)[-1].replace(prefix, '').replace('_', ' ').strip() for i in iris]
  return sorted({v for v in vals if valid.match(v.replace(' ', ''))})


def _reference(slug):
  a = re.match(r'^([a-z]+(?:-[a-z]+)?)-(\d{4})-', slug)
  return (a.group(1).replace('-', ' ').title().replace(' ', '-') + ' ' + a.group(2)) if a else None


def agent_view():
  """recommendations (text units with a normative assertion and a verbatim text of a full sentence), the concepts they
  name through their entity mentions and in which role, the concept relations of the graph, and per concept and
  terminology its link set. ids as the ui knows them: c:<entity>, r:<text unit>, g:<guideline slug>"""
  q = {
    'text': 'SELECT ?tu ?text WHERE { ?tu ds:hasNormativeAssertion ?na ; ds:verbatimText ?text . '
            'FILTER STRSTARTS(STR(?tu), "http://digistrucmed.org/text-unit/") }',
    'dir': 'SELECT ?tu ?dir WHERE { ?tu ds:hasNormativeAssertion ?na . ?na ds:direction ?dir }',
    'cls': 'SELECT ?tu ?g WHERE { ?tu ds:hasNormativeAssertion ?na ; ds:hasClassOfRecommendation ?g }',
    'loe': 'SELECT ?tu ?g WHERE { ?tu ds:hasNormativeAssertion ?na ; ds:hasLevelOfEvidence ?g }',
    'role': 'SELECT ?tu ?role ?e WHERE { ?tu ds:hasNormativeAssertion ?na . ?na ?role ?m . ?m ds:surfaceForm ?e . '
            'VALUES ?role { ds:hasPopulationEntity ds:hasTargetEntity ds:hasConditionEntity ds:hasParameterEntity } }',
    'mention': 'SELECT ?tu ?e WHERE { ?tu ds:hasNormativeAssertion ?na ; ds:hasExtractedEntity ?m . ?m ds:surfaceForm ?e }',
    'src': 'SELECT ?tu ?pb ?doc WHERE { ?su ds:hasTextUnit ?tu . ?tu ds:hasNormativeAssertion ?na . '
           'OPTIONAL { ?su ds:physicalBlockId ?pb } OPTIONAL { ?su ds:documentSha256 ?doc } }',
    'label': 'SELECT ?e ?l WHERE { ?e rdfs:label ?l . FILTER STRSTARTS(STR(?e), "http://digistrucmed.org/entity/") }',
    'alias': 'SELECT ?e ?a WHERE { ?e ds:alias ?a }',
    'desc': 'SELECT ?e ?d WHERE { ?e ds:hasdescription ?d }',
    'cui': 'SELECT ?e ?c WHERE { ?e ds:umls_cui ?c }',
    'qid': 'SELECT ?e ?c WHERE { ?e ds:wikidataId ?c }',
    'rel': 'SELECT ?e ?p ?o WHERE { ?e ?p ?o . VALUES ?p { ds:subclass_of ds:instance_of ds:drug_or_therapy_used_for_treatment '
           'ds:symptoms ds:health_specialty ds:main_subject } FILTER STRSTARTS(STR(?o), "http://digistrucmed.org/entity/") }',
    'links': 'SELECT ?e ?src ?status ?method ?rec ?eff ?name WHERE { ?ls f:anchor ?e ; f:terminologySource ?src ; f:linkStatus ?status ; '
             'f:method ?method ; f:decisionRecord ?rec . OPTIONAL { ?ls f:effectiveConcept ?eff . OPTIONAL { ?eff rdfs:label ?name } } '
             'FILTER STRSTARTS(STR(?e), "http://digistrucmed.org/entity/") }',
    # the guideline pages as part of the walk: a page names a concept when one of its detected mentions is linked to
    # the same UMLS concept as the concept itself (both as the selected Top1 of their link sets); ?n counts the
    # mentions, so a walk can prefer the pages that are about a concept over those that only name it once
    'pagecon': 'SELECT ?page ?e (COUNT(?occ) AS ?n) WHERE { ?els f:anchor ?e ; f:terminologySource "umls" ; '
               'f:linkStatus "selected_top1" ; f:effectiveConcept ?code . FILTER STRSTARTS(STR(?e), "http://digistrucmed.org/entity/") '
               '?ls f:effectiveConcept ?code ; f:terminologySource "umls" ; f:linkStatus "selected_top1" ; f:anchor ?q . '
               '?q a m:RetrievalQuery . ?occ m:ofQuery ?q . ?page m:hasOccurrence ?occ } GROUP BY ?page ?e',
  }
  with ThreadPoolExecutor(6) as pool:
    rows = dict(zip(q, pool.map(lambda s: sparql(s, timeout=300), q.values())))

  text = {r['tu']: r['text'] for r in rows['text']}
  per = collections.defaultdict(lambda: collections.defaultdict(set))
  for key, field in (('dir', 'dir'), ('cls', 'g'), ('loe', 'g')):
    for r in rows[key]:
      per[r['tu']][key].add(r[field])
  roles = collections.defaultdict(dict)
  for r in rows['role']:
    roles[r['tu']].setdefault(r['e'], ROLES[r['role']])
  for r in rows['mention']:
    roles[r['tu']].setdefault(r['e'], 'mention')
  src = collections.defaultdict(lambda: {'pb': set(), 'doc': None})
  for r in rows['src']:
    if r.get('pb') and BLOCK.match(r['pb']):
      src[r['tu']]['pb'].add(r['pb'])
    src[r['tu']]['doc'] = src[r['tu']]['doc'] or r.get('doc')

  ent = collections.defaultdict(lambda: {'labels': [], 'aliases': set(), 'desc': None, 'cui': None, 'qid': None})
  for r in rows['label']:
    ent[r['e']]['labels'].append(r['l'])
  for r in rows['alias']:
    ent[r['e']]['aliases'].add(local(r['a']).replace('_', ' '))
  for r in rows['desc']:
    ent[r['e']]['desc'] = ent[r['e']]['desc'] or r['d']
  for key in ('cui', 'qid'):
    for r in sorted(rows[key], key=lambda r: r['c']):
      ent[r['e']][key] = ent[r['e']][key] or local(r['c'])
  codes = collections.defaultdict(dict)  # entity -> terminology -> link set
  for r in rows['links']:
    ls = codes[r['e']].setdefault(r['src'], {'status': r['status'], 'method': r['method'], 'record': json.loads(r['rec']), 'concepts': []})
    if r.get('eff'):
      code = local(r['eff'])
      if code not in [c['id'] for c in ls['concepts']]:
        ls['concepts'].append({'id': code, 'name': r.get('name')})

  cid = lambda e: 'c:' + e[len(DS + 'entity/'):]
  concepts, recs, guides, edges = {}, {}, {}, set()

  def concept(e):
    if e not in concepts:
      c = ent.get(e) or {'labels': [], 'aliases': set(), 'desc': None, 'cui': None, 'qid': None}
      concepts[e] = {'id': cid(e), 'label': (sorted(c['labels'], key=len) or [local(e).replace('_', ' ')])[0],
                     'aliases': sorted(c['aliases'])[:12], 'cui': c['cui'], 'wikidata': c['qid'], 'desc': c['desc'],
                     'codes': codes.get(e, {})}
    return concepts[e]['id']

  for tu, t in text.items():
    if len(t) < 40 or t.count(';') > 6:
      continue  # fragments and abbreviation lists are no recommendations
    unit = tu.split('/text-unit/', 1)[1]
    slug, rid = unit.split('_n0')[0], 'r:' + unit
    p = per[tu]
    recs[rid] = {'id': rid, 'iri': tu, 'text': t, 'class': _grades(p['cls'], 'ClassOfRecommendation_', VALID_CLASS),
                 'loe': _grades(p['loe'], 'LevelOfEvidence_', VALID_LOE), 'direction': sorted(p['dir']), 'ref': _reference(slug),
                 'guide': 'g:' + slug, 'src': sorted(src[tu]['pb']), 'doc': src[tu]['doc']}
    guides.setdefault('g:' + slug, {'id': 'g:' + slug, 'label': _reference(slug) or slug[:40]})
    edges.update((rid, concept(e), role) for e, role in roles[tu].items())
    edges.add((rid, 'g:' + slug, 'source'))
  for r in rows['rel']:
    if r['o'] != r['e']:
      edges.add((concept(r['e']), concept(r['o']), RELATIONS[r['p']]))
  used = {a for a, _, _ in edges} | {b for _, b, _ in edges}
  # pages: page id -> [(concept id, mentions)] for the concepts of this view; a recommendation's page from its block id
  pages = collections.defaultdict(list)
  for r in rows['pagecon']:
    if r['e'] in concepts and concepts[r['e']]['id'] in used:
      pages[local(r['page'])].append((concepts[r['e']]['id'], int(r['n'])))
  for rec in recs.values():
    rec['page_id'] = next((pb[3:].rsplit('_g', 1)[0] for pb in rec['src']), None)
  return {'concepts': [c | {'iri': e} for e, c in concepts.items() if c['id'] in used], 'recs': list(recs.values()),
          'guides': [g for g in guides.values() if g['id'] in used], 'edges': sorted(edges), 'pages': dict(pages)}


def load_agent_view(attempts=12, wait=10):
  """the view at startup. the graph server may still be starting: retry for about two minutes, then fail loudly"""
  for i in range(attempts):
    try:
      t0 = time.time()
      view = agent_view()
      print(f"graph: agent view {len(view['concepts'])} concepts, {len(view['recs'])} recommendations, "
            f"{len(view['edges'])} edges, {len(view['pages'])} pages with "
            f"{sum(len(v) for v in view['pages'].values())} page-concept links in {time.time() - t0:.1f}s from {config.GRAPH_ENDPOINT}", flush=True)
      return view
    except GraphUnavailable as e:
      print(f'graph: {e} (attempt {i + 1}/{attempts})', flush=True)
      time.sleep(wait)
  raise GraphUnavailable(f'graph server {config.GRAPH_ENDPOINT} not reachable')


# ---------------------------------------------------------------- the explorer

LABEL_PREDICATES = ('rdfs:label', 'f:surfaceForm', 'ds:verbatimText', 'ds:name', 'f:code', 'f:terminologySource', 'f:linkStatus',
                    'ds:pageId', 'ds:semanticUnitId', 'ds:titleOrContext', 'm:technicalPageNumber', 'm:queryId', 'ds:sourceDocument')
_short = {RDFS_LABEL: 'label', RDF_TYPE: 'type'}


def short(pred):
  """a readable name for a predicate IRI"""
  if pred in _short:
    return _short[pred]
  return re.split(r'[/#:]', pred)[-1]


def labels(nodes):
  """display label per node, from its own label-like literals (one query for a whole batch)"""
  nodes = [n for n in dict.fromkeys(nodes) if n]
  if not nodes:
    return {}
  values = ' '.join(iri(n) for n in nodes)
  rows = sparql(f'SELECT ?n ?p ?v WHERE {{ VALUES ?n {{ {values} }} VALUES ?p {{ {" ".join(LABEL_PREDICATES)} }} ?n ?p ?v }}', timeout=30)
  got = collections.defaultdict(dict)
  for r in rows:
    got[r['n']].setdefault(short(r['p']), r['v'])
  out = {}
  for n in nodes:
    g, k = got.get(n, {}), kind_of(n)
    if k == 'linkset':
      lab = f"{g.get('terminologySource', '').upper()} · {g.get('linkStatus', '').replace('_', ' ')}"
    elif k == 'code':
      lab = f"{g.get('terminologySource', '').upper()} {g.get('code', local(n))}" + (f" · {g['label']}" if g.get('label') else '')
    elif k == 'page':
      lab = f"Page {local(n).rsplit('_p', 1)[-1].lstrip('0')}"
    else:
      lab = next((g[x] for x in ('label', 'surfaceForm', 'name', 'verbatimText', 'titleOrContext', 'semanticUnitId', 'queryId') if g.get(x)), None)
      lab = lab or local(n).replace('_', ' ')
    out[n] = re.sub(r'\s+', ' ', lab).strip()[:160]
  return out


def view(nodes):
  lab = labels(nodes)
  return [{'id': n, 'kind': kind_of(n), 'label': lab.get(n, local(n))} for n in dict.fromkeys(nodes)]


def search(q, kinds=None, per_kind=8):
  """the words of q as word prefixes in the label of every searchable kind, through QLever's full-text index over the
  graph's literals (graph-server/Qleverfile, TEXT_INDEX from_literals), one query per kind, in parallel, shortest
  labels first. a terminology code (C0018801, D006333, Q181754, 84114007) also matches its catalogue entry exactly"""
  words = [w for w in re.findall(r'[a-z0-9]+', (q or '').lower()) if len(w) > 1][:6]
  if not words:
    return []
  kinds = [k for k in (kinds or KINDS) if k in KINDS and KINDS[k][3]]
  text = ' '.join(w + '*' for w in words)

  def one(k):
    prefix, _, _, pred, _ = KINDS[k]
    rows = sparql(f'SELECT DISTINCT ?n ?l WHERE {{ ?t ql:contains-word {lit(text)} . ?t ql:contains-entity ?l . ?n {pred} ?l . '
                  f'FILTER STRSTARTS(STR(?n), {lit(prefix)}) }} ORDER BY STRLEN(?l) LIMIT {per_kind}', timeout=30)
    return [{'id': r['n'], 'kind': k, 'label': re.sub(r'\s+', ' ', r['l'])[:160]} for r in rows]

  out = []
  code = q.strip()
  if re.fullmatch(r'[A-Za-z]?\d[\d-]{2,}|[A-Za-z]\d{5,}', code):
    rows = sparql(f'SELECT ?n WHERE {{ ?n f:code ?c . FILTER (?c = {lit(code.upper())} || ?c = {lit(code)}) }} LIMIT 10')
    out += view([r['n'] for r in rows])
  with ThreadPoolExecutor(len(kinds)) as pool:
    for hits in pool.map(one, kinds):
      out += [h for h in hits if h['id'] not in {o['id'] for o in out}]
  return out


def node(n):
  """one node: kind, label, its literal properties, and how many edges of each predicate go out and come in"""
  rows = sparql(f'SELECT ?p ?o WHERE {{ {iri(n)} ?p ?o }} LIMIT 2000', timeout=30)
  if not rows:
    incoming = sparql(f'SELECT ?s WHERE {{ ?s ?p {iri(n)} }} LIMIT 1')
    if not incoming:
      return None
  props, out_count, types = [], collections.Counter(), []
  for r in rows:
    o = r['o']
    if r['p'] == RDF_TYPE:
      types.append(short(o))
    elif re.match(r'^(https?://|urn:)', o):
      out_count[r['p']] += 1
    else:
      props.append({'p': short(r['p']), 'value': o[:20000]})
  ins = sparql(f'SELECT ?p (COUNT(?s) AS ?n) WHERE {{ ?s ?p {iri(n)} }} GROUP BY ?p', timeout=30)
  groups = [{'p': p, 'name': short(p), 'dir': 'out', 'count': c} for p, c in out_count.most_common()]
  groups += [{'p': r['p'], 'name': short(r['p']), 'dir': 'in', 'count': int(r['n'])} for r in sorted(ins, key=lambda r: -int(r['n']))]
  lab = labels([n]).get(n, local(n))
  return {'id': n, 'kind': kind_of(n), 'label': lab, 'types': types, 'props': props, 'groups': groups}


def neighbours(n, pred=None, direction=None, limit=40, offset=0):
  """the nodes one edge away, both directions unless one is asked for, optionally of one predicate"""
  limit, offset = max(1, min(int(limit), 200)), max(0, int(offset))
  pf = f'VALUES ?p {{ {iri(pred)} }} ' if pred else ''
  links = []
  if direction in (None, 'out'):
    for r in sparql(f'SELECT ?p ?o WHERE {{ {pf}{iri(n)} ?p ?o . FILTER (isIRI(?o) && ?p != rdf:type) }} LIMIT {limit} OFFSET {offset}'):
      links.append({'source': n, 'target': r['o'], 'p': short(r['p'])})
  if direction in (None, 'in'):
    for r in sparql(f'SELECT ?p ?s WHERE {{ {pf}?s ?p {iri(n)} }} LIMIT {limit} OFFSET {offset}'):
      links.append({'source': r['s'], 'target': n, 'p': short(r['p'])})
  others = [x for l in links for x in (l['source'], l['target']) if x != n]
  return {'nodes': view(others), 'links': links}


_counts = {'data': None, 'running': False}
_counts_lock = threading.Lock()


def _count_all():
  """how many nodes of each kind the graph holds, one cheap query per kind, one after the other (QLever refuses
  too many heavy queries at once). once per process: the graph does not change while the app runs"""
  counts = {}
  for k, spec in KINDS.items():
    if spec[4]:
      try:
        counts[k] = int(sparql(f'SELECT (COUNT(DISTINCT ?n) AS ?c) WHERE {{ {spec[4]} }}', timeout=120)[0]['c'])
      except (GraphUnavailable, KeyError, IndexError, ValueError):
        counts[k] = None
  with _counts_lock:
    _counts.update(data=counts, running=False)


def kinds():
  """every kind with its colour and name, and its node count once counted. the first call starts the counting in the
  background and returns at once; the explorer asks again"""
  with _counts_lock:
    if _counts['data'] is None and not _counts['running']:
      _counts['running'] = True
      threading.Thread(target=_count_all, daemon=True).start()
    counts = _counts['data'] or {}
  return [{'kind': k, 'name': v[1], 'color': v[2], 'searchable': bool(v[3]), 'count': counts.get(k)} for k, v in KINDS.items()]

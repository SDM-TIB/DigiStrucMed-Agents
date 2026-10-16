"""Live facts for a graph concept, pulled from Wikidata (CC0) the moment someone opens it.

how a concept finds its Wikidata item, best link first. the ui shows which one was used:
  umls_kg             UMLS CUI from the graph's own normalization -> item with that CUI (P2892)
  umls_mhh_candidate  rank-1 UMLS retrieval candidate from the MHH export, unconfirmed -> P2892
  kg                  Wikidata QID from the graph's normalization. sometimes a brand or a neighbour item
the MHH Wikidata candidates are never used. spot checks found rivaroxaban -> warfarin. the CUI join is exact.
links come from pipeline/build_kg_links.py. results are cached for 6 hours.
"""
import gzip
import json
import threading
import time
import urllib.parse
import urllib.request

import config
import kg

ENDPOINT = 'https://query.wikidata.org/sparql'
USER_AGENT = 'TIB-Chat/1.0 (TIB Leibniz Information Centre; research demo; https://www.tib.eu)'
TTL = 6 * 3600
LINKS = json.load(gzip.open(config.DATA / 'kg_links.json.gz', 'rt', encoding='utf-8'))

# (title, property, tone). tone colours the card section
RELATIONS = [('Significant drug interactions', 'P769', 'warn'), ('Side effects', 'P1909', 'warn'),
             ('Used to treat', 'P2175', 'good'), ('Treated with', 'P2176', 'good'),
             ('Symptoms and signs', 'P780', 'info'), ('Molecular targets', 'P129', 'info'), ('Role', 'P2868', 'info'),
             ('Instance of', 'P31', 'muted'), ('Subclass of', 'P279', 'muted'), ('Medical specialty', 'P1995', 'muted')]
IDENTIFIERS = [('ATC code', 'P267', 'https://www.whocc.no/atc_ddd_index/?code={}'), ('MeSH', 'P486', 'https://meshb.nlm.nih.gov/record/ui?ui={}'),
               ('ICD-10', 'P494', 'https://icd.who.int/browse10/2019/en#/{}'), ('PubChem CID', 'P662', 'https://pubchem.ncbi.nlm.nih.gov/compound/{}'),
               ('DrugBank', 'P715', 'https://go.drugbank.com/drugs/DB{}'), ('UMLS CUI', 'P2892', None)]

_cache, _lock = {}, threading.Lock()


def _sparql(query):
  url = ENDPOINT + '?' + urllib.parse.urlencode({'query': query, 'format': 'json'})
  req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT, 'Accept': 'application/sparql-results+json'})
  for attempt in range(2):  # the public endpoint has slow moments. one more try before giving up
    try:
      with urllib.request.urlopen(req, timeout=12) as r:
        return json.load(r)['results']['bindings']
    except Exception:
      if attempt:
        raise
      time.sleep(0.5)


def _cached(key, fn):
  with _lock:
    hit = _cache.get(key)
  if hit and time.time() - hit[0] < TTL:
    return hit[1]
  try:
    val = fn()
  except Exception:
    if hit:  # wikidata unreachable: what we fetched earlier beats no card
      return hit[1]
    raise
  with _lock:
    _cache[key] = (time.time(), val)
  return val


def qid_for_cui(cui):
  def run():
    rows = _sparql(f'SELECT ?item WHERE {{ ?item wdt:P2892 "{cui}" }} LIMIT 3')
    return rows[0]['item']['value'].rsplit('/', 1)[-1] if rows else None
  return _cached('cui:' + cui, run)


def resolve(cid):
  e = LINKS.get(cid, {})
  graph, mhh = e.get('kg', {}), e.get('mhh', {})
  umls = None
  if graph.get('cui'):
    umls = {'cui': graph['cui'], 'source': 'kg'}
    if mhh.get('umls', {}).get('cui') == graph['cui']:  # same cui, borrow its name and semantic type
      umls.update({k: mhh['umls'][k] for k in ('name', 'type') if mhh['umls'].get(k)})
  elif mhh.get('umls'):
    umls = {**mhh['umls'], 'source': 'mhh_candidate'}
  if umls:
    try:
      qid = qid_for_cui(umls['cui'])
      if qid:
        return umls, qid, 'umls_kg' if umls['source'] == 'kg' else 'umls_mhh_candidate'
    except Exception:
      pass
  return umls, graph.get('qid'), 'kg' if graph.get('qid') else None


def facts(qid):
  def run():
    props = ' '.join('wdt:' + p for _, p, _ in RELATIONS)
    rel = _sparql(f'''SELECT ?p ?v ?vLabel WHERE {{ VALUES ?p {{ {props} }} wd:{qid} ?p ?v . FILTER(isIRI(?v))
                      SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }} }} LIMIT 400''')
    ids = ' '.join('wdt:' + p for _, p, _ in IDENTIFIERS)
    head = _sparql(f'''SELECT ?label ?desc ?img ?wiki ?p ?id WHERE {{
          OPTIONAL {{ wd:{qid} rdfs:label ?label FILTER(lang(?label)="en") }}
          OPTIONAL {{ wd:{qid} schema:description ?desc FILTER(lang(?desc)="en") }}
          OPTIONAL {{ wd:{qid} wdt:P18 ?img }}
          OPTIONAL {{ ?wiki schema:about wd:{qid}; schema:isPartOf <https://en.wikipedia.org/> }}
          OPTIONAL {{ VALUES ?p {{ {ids} }} wd:{qid} ?p ?id }} }} LIMIT 60''')
    out = {'qid': qid, 'url': f'https://www.wikidata.org/wiki/{qid}', 'label': None, 'description': None, 'image': None,
           'wikipedia': None, 'ids': [], 'relations': [], 'fetched_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
    seen = set()
    for r in head:
      out['label'] = out['label'] or r.get('label', {}).get('value')
      out['description'] = out['description'] or r.get('desc', {}).get('value')
      out['image'] = out['image'] or r.get('img', {}).get('value')
      out['wikipedia'] = out['wikipedia'] or r.get('wiki', {}).get('value')
      if 'p' in r and 'id' in r:
        pid, val = r['p']['value'].rsplit('/', 1)[-1], r['id']['value']
        if (pid, val) in seen:
          continue
        seen.add((pid, val))
        name, _, link = next(x for x in IDENTIFIERS if x[1] == pid)
        out['ids'].append({'label': name, 'value': val, 'href': link.format(val[2:] if pid == 'P715' else val) if link else None})
    groups = {}
    for r in rel:
      pid, v = r['p']['value'].rsplit('/', 1)[-1], r['v']['value'].rsplit('/', 1)[-1]
      name = r.get('vLabel', {}).get('value') or v
      if name != v or not v.startswith('Q'):  # items without an english label are noise
        groups.setdefault(pid, {})[v] = name
    for title, pid, tone in RELATIONS:
      items = groups.get(pid)
      if items:
        out['relations'].append({'title': title, 'property': pid, 'tone': tone, 'total': len(items),
                                 'items': [{'qid': q, 'label': n, 'url': f'https://www.wikidata.org/wiki/{q}'}
                                           for q, n in sorted(items.items(), key=lambda x: x[1].lower())[:14]]})
    return out
  return _cached('facts:' + qid, run)


def concept_card(cid):
  """umls + live wikidata for a concept. items that also exist in our graph get its id and come first"""
  umls, qid, via = resolve(cid)
  out = {'concept': cid, 'label': kg.CONCEPTS[cid]['label'], 'n_recs': kg.n_recs(cid),
         'umls': umls, 'qid_via': via, 'wikidata': None, 'error': None}
  if not qid:
    return out
  try:
    out['wikidata'] = json.loads(json.dumps(facts(qid)))  # copy, the cached one stays clean
  except Exception as e:
    out['error'] = f'Wikidata unavailable ({type(e).__name__})'
    return out
  for rel in out['wikidata']['relations']:
    for it in rel['items']:
      k = kg.concept_for_label(it['label'])
      if k and k != cid:
        it['kg_id'], it['kg_recs'] = k, kg.n_recs(k)
    rel['items'].sort(key=lambda it: (not it.get('kg_id'), -it.get('kg_recs', 0), it['label'].lower()))
  return out


def prefetch(cids):
  """fetch the cards of concepts the ui is about to show, in the background, so the inspector finds them cached"""
  def run():
    for cid in cids:
      try:
        concept_card(cid)
      except Exception:
        pass
  threading.Thread(target=run, daemon=True).start()

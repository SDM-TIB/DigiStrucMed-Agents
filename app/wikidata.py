"""Wikidata facts for a graph concept, from a dated snapshot (CC0): app/data/wikidata_snapshot.json.gz.

the snapshot is built once by pipeline/build_wikidata_snapshot.py with the revision of every item, so a card shows
the same facts every time and can be traced to the Wikidata version it came from. nothing is queried at runtime.

how a concept finds its Wikidata item, best link first. the ui shows which one was used:
  umls_kg             UMLS CUI from the graph's own normalization -> item with that CUI (P2892)
  umls_mhh_candidate  rank-1 UMLS retrieval candidate from the MHH export, unconfirmed -> P2892
  kg                  Wikidata QID from the graph's normalization. sometimes a brand or a neighbour item
the MHH Wikidata candidates are never used. spot checks found rivaroxaban -> warfarin. the CUI join is exact.
links come from pipeline/build_kg_links.py.
"""
import gzip
import json

import config
import kg

USER_AGENT = 'TIB-Chat/1.0 (TIB Leibniz Information Centre; research demo; https://www.tib.eu)'
LINKS = json.load(gzip.open(config.DATA / 'kg_links.json.gz', 'rt', encoding='utf-8'))

# (title, property, tone). tone colours the card section
RELATIONS = [('Significant drug interactions', 'P769', 'warn'), ('Side effects', 'P1909', 'warn'),
             ('Used to treat', 'P2175', 'good'), ('Treated with', 'P2176', 'good'),
             ('Symptoms and signs', 'P780', 'info'), ('Molecular targets', 'P129', 'info'), ('Role', 'P2868', 'info'),
             ('Instance of', 'P31', 'muted'), ('Subclass of', 'P279', 'muted'), ('Medical specialty', 'P1995', 'muted')]
IDENTIFIERS = [('ATC code', 'P267', 'https://www.whocc.no/atc_ddd_index/?code={}'), ('MeSH', 'P486', 'https://meshb.nlm.nih.gov/record/ui?ui={}'),
               ('ICD-10', 'P494', 'https://icd.who.int/browse10/2019/en#/{}'), ('PubChem CID', 'P662', 'https://pubchem.ncbi.nlm.nih.gov/compound/{}'),
               ('DrugBank', 'P715', 'https://go.drugbank.com/drugs/DB{}'), ('UMLS CUI', 'P2892', None)]

_path = config.DATA / 'wikidata_snapshot.json.gz'
SNAPSHOT = json.load(gzip.open(_path, 'rt', encoding='utf-8')) if _path.exists() else {'meta': {}, 'cui_qid': {}, 'items': {}}


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
  qid = umls and SNAPSHOT['cui_qid'].get(umls['cui'])
  if qid:
    return umls, qid, 'umls_kg' if umls['source'] == 'kg' else 'umls_mhh_candidate'
  return umls, graph.get('qid'), 'kg' if graph.get('qid') else None


def facts(qid):
  item = SNAPSHOT['items'].get(qid)
  return item and {**item, 'snapshot': SNAPSHOT['meta'].get('created')}


def concept_card(cid):
  """umls + wikidata for a concept. items that also exist in our graph get its id and come first"""
  umls, qid, via = resolve(cid)
  out = {'concept': cid, 'label': kg.CONCEPTS[cid]['label'], 'n_recs': kg.n_recs(cid),
         'umls': umls, 'qid_via': via, 'wikidata': None, 'error': None}
  item = qid and facts(qid)
  if not item:
    return out
  out['wikidata'] = json.loads(json.dumps(item))  # copy, the snapshot stays clean
  for rel in out['wikidata']['relations']:
    for it in rel['items']:
      k = kg.concept_for_label(it['label'])
      if k and k != cid:
        it['kg_id'], it['kg_recs'] = k, kg.n_recs(k)
    rel['items'].sort(key=lambda it: (not it.get('kg_id'), -it.get('kg_recs', 0), it['label'].lower()))
  return out

"""Wikidata facts for a graph concept, from a dated snapshot (CC0): app/data/wikidata_snapshot.json.gz.

the snapshot is built once by pipeline/build_wikidata_snapshot.py with the revision of every item, so a card shows
the same facts every time and can be traced to the Wikidata version it came from. nothing is queried at runtime.

how a concept finds its Wikidata item, best link first. the ui shows which one was used:
  mhh_wikidata_selected  Wikidata item the native CLEF selector chose for the concept's mentions (Mewsli-X subset)
  kg                     Wikidata QID from the graph's normalization. sometimes a brand or a neighbour item
  umls_mhh_selected      UMLS CUI our selectors chose for the concept's mentions in the final MHH graph -> P2892
  umls_kg                UMLS CUI from the graph's own normalization -> item with that CUI (P2892)
  umls_mhh_candidate     rank-1 UMLS retrieval candidate, unconfirmed -> P2892
the UMLS CUI shown on the card is our selected one when there is one (LaSER retrieval plus a contextual choice),
else the colleague's; direct Wikidata links come before the CUI join, which can land on a neighbour item.
unconfirmed Wikidata candidates are never used: spot checks found rivaroxaban -> warfarin. the CUI join is exact.
links come from pipeline/build_kg_links.py (final MHH graph of 16 October 2026).
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
  """best link first. UMLS: our selected CUI (LaSER retrieval, then a CLEF/Gemini/Luna choice) beats the colleague
  graph's normalization, which beats an unconfirmed candidate; a differing colleague CUI stays visible as graph_cui.
  Wikidata, direct evidence before the CUI join: the item CLEF selected for the concept's mentions, the graph's QID,
  the item of the selected or graph CUI (P2892), the item of an unconfirmed CUI. unconfirmed Wikidata candidates are
  never used"""
  e = LINKS.get(cid, {})
  graph, mhh = e.get('kg', {}), e.get('mhh', {})
  mu, mw = mhh.get('umls') or {}, mhh.get('wikidata') or {}
  if mu.get('status') == 'selected':
    umls = {**mu, 'source': 'mhh_selected'}
  elif graph.get('cui'):
    umls = {'cui': graph['cui'], 'source': 'kg'}
    if mu.get('cui') == graph['cui']:  # same cui, borrow its name
      umls.update({k: mu[k] for k in ('name', 'type') if mu.get(k)})
  elif mu:
    umls = {**mu, 'source': 'mhh_candidate'}
  else:
    umls = None
  if umls and graph.get('cui') and graph['cui'] != umls['cui']:
    umls['graph_cui'] = graph['cui']
  # a Wikidata item chosen for this concept beats one reached through its CUI: Wikidata attaches CUIs to neighbour
  # items too (C0018801 heart failure sits on Q19000661 congestive heart failure, not on Q181754 heart failure)
  if mw.get('status') == 'selected' and mw.get('qid') in SNAPSHOT['items']:
    return umls, mw['qid'], 'mhh_wikidata_selected'
  if graph.get('qid'):
    return umls, graph['qid'], 'kg'
  qid = umls and umls['source'] != 'mhh_candidate' and SNAPSHOT['cui_qid'].get(umls['cui'])
  if qid:
    return umls, qid, 'umls_kg' if umls['source'] == 'kg' else 'umls_mhh_selected'
  qid = umls and SNAPSHOT['cui_qid'].get(umls['cui'])
  if qid:
    return umls, qid, 'umls_mhh_candidate'
  return umls, None, None


def identifiers(cid):
  """the selected SNOMED CT, MeSH, RxNorm and LOINC ids of the final MHH graph, for the card"""
  mhh = LINKS.get(cid, {}).get('mhh', {})
  return [{'source': s, **mhh[s]} for s in ('snomed', 'mesh', 'rxnorm', 'loinc') if mhh.get(s, {}).get('status') == 'selected']


def facts(qid):
  item = SNAPSHOT['items'].get(qid)
  return item and {**item, 'snapshot': SNAPSHOT['meta'].get('created')}


def concept_card(cid):
  """umls + wikidata for a concept. items that also exist in our graph get its id and come first"""
  umls, qid, via = resolve(cid)
  out = {'concept': cid, 'label': kg.CONCEPTS[cid]['label'], 'n_recs': kg.n_recs(cid), 'identifiers': identifiers(cid),
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

"""Wikidata facts for a graph concept, from a dated snapshot (CC0): app/data/wikidata_snapshot.json.gz.

the snapshot is built once by pipeline/build_wikidata_snapshot.py with the revision of every item, so a card shows
the same facts every time and can be traced to the Wikidata version it came from. nothing is queried at runtime.

the concept's codes come from the final MHH graph (its entity link sets, kg.CONCEPTS[...]['codes']). how a concept
finds its Wikidata item, best link first; the ui shows which one was used:
  mhh_wikidata_selected  the Wikidata item selected for the concept's mentions (native CLEF selector)
  kg                     the concept's Wikidata QID in the graph (where no selection replaced it)
  umls_mhh_selected      the selected UMLS CUI -> the item with that CUI (P2892)
  umls_kg                the graph's UMLS CUI -> P2892
  umls_mhh_candidate     the first unconfirmed UMLS candidate -> P2892
direct Wikidata links come before the CUI join, which can land on a neighbour item (C0018801 heart failure sits on
Q19000661 congestive heart failure, not on Q181754 heart failure). unconfirmed Wikidata candidates are never used:
spot checks found rivaroxaban -> warfarin. the CUI join is exact.
"""
import gzip
import json

import config
import kg

USER_AGENT = 'TIB-Chat/1.0 (TIB Leibniz Information Centre; research demo; https://www.tib.eu)'
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


MAJORITY = 'majority of the mention selections'


def link(cid, source):
  """the concept's link set of one terminology, as the card shows it: the code, its name, and how it was chosen"""
  ls = ((kg.CONCEPTS.get(cid) or {}).get('codes') or {}).get(source)
  if not ls or not ls['concepts']:
    return None
  top, rec = ls['concepts'][0], ls['record']
  if ls['status'] == 'selected_top1':
    return {'id': top['id'], 'name': top.get('name'), 'status': 'selected', 'selector': MAJORITY, 'evidence': rec.get('evidence'),
            'support': rec.get('support'), 'mentions': rec.get('votes'), 'alternatives': [c for c in rec.get('selected_votes', {}) if c != top['id']][:3]}
  votes = rec.get('unconfirmed_votes', {})
  return {'id': top['id'], 'name': top.get('name'), 'status': 'candidate', 'support': votes.get(top['id']), 'mentions': sum(votes.values()),
          'alternatives': [c['id'] for c in ls['concepts'][1:]]}


def resolve(cid):
  """best link first. UMLS: the CUI selected for the concept's mentions, else the graph's CUI (the colleague's
  normalization where no selection replaced it), else the first unconfirmed candidate. Wikidata: see the module doc"""
  c = kg.CONCEPTS.get(cid) or {}
  mu, mw = link(cid, 'umls'), link(cid, 'wikidata')
  if mu and mu['status'] == 'selected':
    umls = {**mu, 'cui': mu['id'], 'source': 'mhh_selected'}
  elif c.get('cui'):
    umls = {'cui': c['cui'], 'source': 'kg'}
  elif mu:
    umls = {**mu, 'cui': mu['id'], 'source': 'mhh_candidate'}
  else:
    umls = None
  if mw and mw['status'] == 'selected' and mw['id'] in SNAPSHOT['items']:
    return umls, mw['id'], 'mhh_wikidata_selected'
  if c.get('wikidata'):
    return umls, c['wikidata'], 'kg'
  qid = umls and umls['source'] != 'mhh_candidate' and SNAPSHOT['cui_qid'].get(umls['cui'])
  if qid:
    return umls, qid, 'umls_kg' if umls['source'] == 'kg' else 'umls_mhh_selected'
  qid = umls and SNAPSHOT['cui_qid'].get(umls['cui'])
  if qid:
    return umls, qid, 'umls_mhh_candidate'
  return umls, None, None


def identifiers(cid):
  """the selected SNOMED CT, MeSH, RxNorm and LOINC codes of the concept, for the card"""
  out = []
  for s in ('snomed', 'mesh', 'rxnorm', 'loinc'):
    x = link(cid, s)
    if x and x['status'] == 'selected':
      out.append({'source': s, **x})
  return out


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

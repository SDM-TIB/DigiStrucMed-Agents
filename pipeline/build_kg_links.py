"""Graph concepts -> terminology ids, for the concept cards. writes app/data/kg_links.json.gz

  python pipeline/build_kg_links.py <final_graph_16_10>

two sources, kept apart so the ui can say where a link came from:
  kg   the normalization inside guidelineKG.nt (umls cui, wikidata qid on the entity)
  mhh  the final MHH graph of 16 October 2026 (mhh_concept_links_16_10.jsonl.gz, its compact export): every detected
       mention on the 2,780 pages, and every UMLS mention inside the Level-4 units, carries per terminology (UMLS,
       SNOMED CT, MeSH, RxNorm, LOINC, Wikidata) either a selector's choice (selected_top1: native CLEF selector for
       the five catalogues; for UMLS the native CLEF selector among the Top20 first, else Luna or Gemini among the Top10), a checked no_match, or the unconfirmed retrieval Top3.
the mentions whose surface form equals a concept label or alias vote per terminology. a selected choice beats any
candidate: the majority among selections gives status "selected", without any selection the majority of the rank-1
candidates gives status "candidate". support is the votes for the winner, mentions all votes cast, no_match the
checked mentions that matched nothing. names come from mhh_concept_names_16_10.json.gz
"""
import collections
import gzip
import json
import pathlib
import re
import sys

DATA = pathlib.Path(__file__).resolve().parents[1] / 'app' / 'data'
SOURCES = ('umls', 'snomed', 'mesh', 'rxnorm', 'loinc', 'wikidata')
KEY = {'umls': 'cui', 'wikidata': 'qid'}  # the id field per terminology; 'id' for the others


SELECTORS = (('clef', 'CLEF'), ('gemini', 'Gemini 2.5 Flash Lite'), ('luna', 'Luna'))


def selector(method):
  """the model that chose, from a method id of the final graph: 'clef_native_selector:umls2026aa_top20',
  'umls2026aa_top20:native_clef' -> CLEF; 'gemini_batch_top10_page_selection:gemini-2.5-flash-lite' -> Gemini 2.5 Flash
  Lite. several agreeing selectors ('a + b') are all named"""
  names = []
  for part in method.split(' + '):
    name = next((n for key, n in SELECTORS if key in part.lower()), part)
    if name not in names:
      names.append(name)
  return ' + '.join(names)


def norm(text):
  """lowercase, punctuation out, plural s off: 'SGLT2 inhibitors' == 'sglt2 inhibitor'"""
  words = re.sub(r'[^a-z0-9]+', ' ', text.lower()).split()
  return ' '.join(w[:-1] if len(w) > 4 and w.endswith('s') and not w.endswith(('ss', 'us', 'is')) else w for w in words)


if __name__ == '__main__':
  final = pathlib.Path(sys.argv[1])
  graph = json.load(gzip.open(DATA / 'kg_graph.json.gz', 'rt', encoding='utf-8'))
  names = json.load(gzip.open(final / 'mhh_concept_names_16_10.json.gz', 'rt', encoding='utf-8'))
  by_form = collections.defaultdict(set)
  for c in graph['concepts']:
    for t in [c['label']] + c.get('aliases', []):
      if len(norm(t)) > 2:
        by_form[norm(t)].add(c['id'])

  # concept -> terminology -> {'selected': Counter, 'candidate': Counter, 'no_match': n, 'methods': Counter}
  votes = collections.defaultdict(lambda: collections.defaultdict(lambda: {'selected': collections.Counter(), 'candidate': collections.Counter(),
                                                                            'no_match': 0, 'methods': collections.Counter()}))
  rows = 0
  for line in gzip.open(final / 'mhh_concept_links_16_10.jsonl.gz', 'rt', encoding='utf-8'):
    r = json.loads(line)
    rows += 1
    ids = by_form.get(norm(r.get('surface_form') or ''))
    if not ids:
      continue
    for source, link in r['links'].items():
      for cid in ids:
        v = votes[cid][source]
        if link['status'] == 'selected_top1':
          v['selected'][link['concepts'][0]] += 1
          v['methods'][link['method']] += 1
        elif link['status'] == 'no_match':
          v['no_match'] += 1
        elif link['concepts']:
          v['candidate'][link['concepts'][0]] += 1

  out = {}
  for c in graph['concepts']:
    entry = {}
    cui, qid = (c.get('cui') or '').rsplit('/', 1)[-1] or None, (c.get('wikidata') or '').rsplit('/', 1)[-1] or None
    if cui or qid:
      entry['kg'] = {k: v for k, v in (('cui', cui), ('qid', qid)) if v}
    mhh = {}
    for source in SOURCES:
      v = votes.get(c['id'], {}).get(source)
      if not v:
        continue
      pool, status = (v['selected'], 'selected') if v['selected'] else (v['candidate'], 'candidate')
      if not pool:
        continue
      best, n = pool.most_common(1)[0]
      link = {KEY.get(source, 'id'): best, 'status': status, 'support': n, 'mentions': sum(pool.values()), 'no_match': v['no_match'],
              'alternatives': [x for x, _ in pool.most_common(4)[1:]]}  # the runners-up, so an agent can see them too
      if status == 'selected':
        link['method'] = v['methods'].most_common(1)[0][0]
        link['selector'] = selector(link['method'])
      name = names.get(f'{source}:{best}')
      if name:
        link['name'] = name
      mhh[source] = link
    if mhh:
      entry['mhh'] = {**mhh, 'graph': 'MHH final graph 16.10.2026'}
    if entry:
      out[c['id']] = entry

  with gzip.open(DATA / 'kg_links.json.gz', 'wt', encoding='utf-8') as fh:
    json.dump(out, fh, ensure_ascii=False, separators=(',', ':'))
  stat = collections.Counter()
  for v in out.values():
    for s in SOURCES:
      if s in v.get('mhh', {}):
        stat[f"{s}:{v['mhh'][s]['status']}"] += 1
  print(f"{rows} mention rows, {len(graph['concepts'])} concepts, linked {len(out)} |",
        f"kg cui {sum('cui' in v.get('kg', {}) for v in out.values())} qid {sum('qid' in v.get('kg', {}) for v in out.values())} |", dict(sorted(stat.items())))

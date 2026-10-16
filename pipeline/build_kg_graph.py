"""guidelineKG.nt -> app/data/kg_graph.json.gz, the compact graph Guido walks.

  python pipeline/build_kg_graph.py <guidelineKG.nt>

nodes
  concept  dsm:entity/*     label, aliases, umls cui, wikidata qid, description
  rec      dsm:text-unit/*  verbatim recommendation + class of recommendation, level of evidence, direction, and
                            src: where it was extracted, the physical blocks of its semantic unit
                            ("pb_<document sha256>_p<page>_g<group>"), as the knowledge graph records them,
                            and doc: the sha256 of the document its semantic unit belongs to
  guide    guideline slug   author-year reference
edges
  rec -> concept   population | target | condition | parameter | mention   (through the normative assertions)
  rec -> guide     source
  concept -> concept  subclass of | instance of | treated with | has symptom | medical specialty | main subject
grade iris that are no real class or level (extraction noise) are dropped. so are fragments and abbreviation lists.
"""
import collections
import gzip
import json
import pathlib
import re
import sys

OUT = pathlib.Path(__file__).resolve().parents[1] / 'app' / 'data' / 'kg_graph.json.gz'
NS = 'http://digistrucmed.org/'
TRIPLE = re.compile(r'^<([^>]+)> <([^>]+)> (.+) \.$')
KEEP = {'verbatimText', 'hasNormativeAssertion', 'hasClassOfRecommendation', 'hasLevelOfEvidence', 'hasExtractedEntity',
        'rdfs:label', 'surfaceForm', 'alias', 'direction', 'hasPopulationEntity', 'hasTargetEntity', 'hasConditionEntity',
        'hasParameterEntity', 'subclass_of', 'umls_cui', 'wikidataId', 'hasdescription',
        'instance_of', 'drug_or_therapy_used_for_treatment', 'symptoms', 'health_specialty', 'main_subject',
        'hasTextUnit', 'physicalBlockId', 'documentSha256'}
ROLES = {'hasPopulationEntity': 'population', 'hasTargetEntity': 'target', 'hasConditionEntity': 'condition', 'hasParameterEntity': 'parameter'}
RELATIONS = {'subclass_of': 'subclass of', 'instance_of': 'instance of', 'drug_or_therapy_used_for_treatment': 'treated with',
             'symptoms': 'has symptom', 'health_specialty': 'medical specialty', 'main_subject': 'main subject'}
VALID_CLASS = re.compile(r'^(I|IIa|IIb|III|1|2a|2b|3)$', re.I)
VALID_LOE = re.compile(r'^(A|B|B-R|B-NR|C|C-LD|C-EO)$', re.I)


def load(path):
  props = collections.defaultdict(lambda: collections.defaultdict(list))
  for line in open(path, encoding='utf-8'):
    m = TRIPLE.match(line.strip())
    if not m:
      continue
    s, p, o = m.groups()
    p = p.replace(NS, '').replace('http://www.w3.org/2000/01/rdf-schema#', 'rdfs:')
    if p in KEEP:
      props[s][p].append(json.loads(re.match(r'^(".*")', o).group(1)) if o.startswith('"') else o.strip('<>'))
  return props


def grades(iris, prefix, valid):
  vals = [i.rsplit('/', 1)[-1].replace(prefix, '').replace('_', ' ').strip() for i in iris]
  return sorted({v for v in vals if valid.match(v.replace(' ', ''))})


def reference(slug):
  a = re.match(r'^([a-z]+(?:-[a-z]+)?)-(\d{4})-', slug)
  return (a.group(1).replace('-', ' ').title().replace(' ', '-') + ' ' + a.group(2)) if a else None


def build(props):
  concepts, recs, guides, edges = {}, {}, {}, set()

  def concept(ent):
    if ent not in concepts:
      e = props.get(ent, {})
      concepts[ent] = {'id': 'c:' + ent.rsplit('/', 1)[-1],
                       'label': (e.get('rdfs:label') or [ent.rsplit('/', 1)[-1].replace('_', ' ')])[0],
                       'aliases': sorted({x.rsplit('/', 1)[-1].replace('_', ' ') for x in e.get('alias', [])})[:12],
                       'cui': (e.get('umls_cui') or [None])[0], 'wikidata': (e.get('wikidataId') or [None])[0],
                       'desc': (e.get('hasdescription') or [None])[0]}
    return concepts[ent]['id']

  unit_of = {tu: su for su, p in props.items() for tu in p.get('hasTextUnit', [])}  # text unit -> semantic unit
  block = re.compile(r'^pb_[0-9a-f]{64}_p\d{4}_g\d{3}$')

  def concept_of(mention):
    ents = props.get(mention, {}).get('surfaceForm', [])
    return concept(ents[0]) if ents else None

  for s, p in list(props.items()):
    if '/text-unit/' not in s or not p.get('hasNormativeAssertion') or not p.get('verbatimText'):
      continue
    text = p['verbatimText'][0]
    if len(text) < 40 or text.count(';') > 6:
      continue  # fragments and abbreviation lists
    unit = s.split('/text-unit/', 1)[1]
    slug, rid = unit.split('_n0')[0], 'r:' + unit
    directions, roles = set(), {}
    for na in p['hasNormativeAssertion']:
      directions.update(props.get(na, {}).get('direction', []))
      for pred, role in ROLES.items():
        for mention in props.get(na, {}).get(pred, []):
          c = concept_of(mention)
          if c:
            roles.setdefault(c, role)
    for mention in p.get('hasExtractedEntity', []):
      c = concept_of(mention)
      if c:
        roles.setdefault(c, 'mention')
    recs[rid] = {'id': rid, 'text': text, 'class': grades(p.get('hasClassOfRecommendation', []), 'ClassOfRecommendation_', VALID_CLASS),
                 'loe': grades(p.get('hasLevelOfEvidence', []), 'LevelOfEvidence_', VALID_LOE), 'direction': sorted(directions),
                 'ref': reference(slug), 'guide': 'g:' + slug,
                 'src': sorted({b for b in props.get(unit_of.get(s), {}).get('physicalBlockId', []) if block.match(b)}),
                 'doc': (props.get(unit_of.get(s), {}).get('documentSha256') or [None])[0]}
    guides.setdefault('g:' + slug, {'id': 'g:' + slug, 'label': reference(slug) or slug[:40]})
    edges.update((rid, c, role) for c, role in roles.items())
    edges.add((rid, 'g:' + slug, 'source'))

  for ent in list(concepts):
    for pred, rel in RELATIONS.items():
      for target in props.get(ent, {}).get(pred, []):
        if target.startswith(NS + 'entity/') and target != ent:
          edges.add((concepts[ent]['id'], concept(target), rel))

  used = {a for a, _, _ in edges} | {b for _, b, _ in edges}
  return {'concepts': [c for c in concepts.values() if c['id'] in used], 'recs': list(recs.values()),
          'guides': [g for g in guides.values() if g['id'] in used], 'edges': sorted(edges)}


if __name__ == '__main__':
  graph = build(load(sys.argv[1]))
  with gzip.open(OUT, 'wt', encoding='utf-8') as fh:
    json.dump(graph, fh, ensure_ascii=False)
  print({k: len(v) for k, v in graph.items()}, f'-> {OUT} ({OUT.stat().st_size // 1024} KiB)')
  print(collections.Counter(r for _, _, r in graph['edges']))

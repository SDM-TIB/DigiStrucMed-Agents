"""Check the app's reads of the final MHH guideline graph against the graph server (GRAPH_ENDPOINT).

  python tests/graph_check.py            (locally: open the tunnel first, graph-server/ui-tunnel.ps1)

1. Guido's working view: recommendations, concepts and edges, compared with the counts of the view the app used before
   it read the graph server (built from the colleague graph, 16 October 2026: 3,977 recommendations, 331 guidelines,
   23,183 edges; 9,117 concepts, fewer distinct ids then because entities that share the last path segment, 'S/D' and
   'D', were merged into one id)
2. codes: the heart failure concept has a selected UMLS CUI, a selected SNOMED CT and MeSH code
3. explorer: search finds the concept by its words and a catalogue entry by its code; a node has properties and edge
   groups; its neighbours come back with labels and kinds
exits 1 when a check fails
"""
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'app'))
import graph  # noqa: E402

BEFORE = {'recs': 3977, 'guides': 331, 'edges': 23183, 'concepts': 9117}
HF = 'http://digistrucmed.org/entity/heart_failure'
failed = []


def check(name, ok, detail=''):
  print(('ok   ' if ok else 'FAIL ') + name + (f'  ({detail})' if detail else ''))
  if not ok:
    failed.append(name)


t0 = time.time()
view = graph.agent_view()
print(f'agent view in {time.time() - t0:.1f}s')
counts = {k: len(view[k]) for k in BEFORE}
check('recommendations as before', counts['recs'] == BEFORE['recs'], f"{counts['recs']} vs {BEFORE['recs']}")
check('guidelines as before', counts['guides'] == BEFORE['guides'], f"{counts['guides']} vs {BEFORE['guides']}")
check('edges at least as before (relations of every concept, every entity a mention names)', counts['edges'] >= BEFORE['edges'], f"{counts['edges']} vs {BEFORE['edges']}")
check('concepts at least as before', counts['concepts'] >= BEFORE['concepts'], f"{counts['concepts']} vs {BEFORE['concepts']}")

hf = next((c for c in view['concepts'] if c['iri'] == HF), None)
check('heart failure concept present', hf is not None)
if hf:
  for source in ('umls', 'snomed', 'mesh'):
    ls = hf['codes'].get(source) or {}
    check(f'heart failure {source} selected', ls.get('status') == 'selected_top1',
          f"{ls.get('status')} {[c['id'] for c in ls.get('concepts', [])]}")

t0 = time.time()
hits = graph.search('heart failure')
check('search by words finds the concept', any(h['id'] == HF for h in hits), f'{len(hits)} hits in {time.time() - t0:.1f}s')
check('search covers several kinds', len({h['kind'] for h in hits}) >= 3, str(sorted({h['kind'] for h in hits})))
hits = graph.search('C0018801')
check('search by code finds the catalogue entry', any(h['kind'] == 'code' and 'C0018801' in h['label'] for h in hits),
      str([h['label'] for h in hits[:3]]))

t0 = time.time()
n = graph.node(HF)
check('node has properties and edge groups', bool(n and n['props'] and n['groups']), f'{time.time() - t0:.1f}s')
nb = graph.neighbours(HF, limit=20)
check('neighbours come with labels and kinds', bool(nb['nodes']) and all(x['label'] and x['kind'] for x in nb['nodes']),
      f"{len(nb['nodes'])} nodes, kinds {sorted({x['kind'] for x in nb['nodes']})}")
ls = [x for x in graph.neighbours(HF, pred='urn:mhh:final:20261016:hasEffectiveLinkSet', direction='out')['nodes']]
check('the concept reaches its six link sets', len(ls) == 6, str([x['label'] for x in ls]))

print('FAILED: ' + ', '.join(failed) if failed else 'all checks passed')
sys.exit(1 if failed else 0)

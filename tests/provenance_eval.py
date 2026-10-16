r"""Where does the app show a recommendation? The source page the knowledge graph records, measured over all of them.

  python tests/provenance_eval.py

for every recommendation in app/data/kg_graph.json.gz:
  found        'graph' when the graph records its physical blocks (semantic unit -> physicalBlockId), 'text match'
               when it does not and the page is searched by its verbatim text, none when neither finds a page
  page holds   whether the shown page contains the verbatim text (first 70 letters and digits, case and markup
               ignored), or at least MIN_RECALL of its words: the graph writes latex (\leq) where the page has ≤,
               and joins table cells differently
  table        whether a <table> on that page holds the text, the original table the ui shows
and, where the graph records a page, how often a pure text search (first page with the text in the same guideline)
lands on a different page. that is why the graph's record is used and the text search is only the fallback.
"""
import collections
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'app'))
import corpus  # noqa: E402
import guido  # noqa: E402
import kg  # noqa: E402


def first_text_hit(text, ref):
  key = corpus._squash(text)[:70]
  return next((i for i, p in enumerate(corpus.PAGES) if p.get('ref') == ref and key and key in corpus._page_squash(i)), None)


def main():
  c = collections.Counter()
  for r in kg.RECS.values():
    page, table, found = corpus.source(r, [x['text'] for x in guido.UNITS.get(r['id'].rsplit('/tu_', 1)[0], []) if x is not r])
    c['recommendations'] += 1
    c['found: ' + str(found)] += 1
    if page is None:
      continue
    key = corpus._squash(r['text'])[:70]
    c[f'{found}: page holds the text verbatim'] += bool(key and key in corpus._page_squash(page))
    c[f'{found}: page holds the text (verbatim or {corpus.MIN_RECALL:.0%} of its words)'] += corpus.holds(page, r['text'])
    c[f'{found}: table holds the text'] += bool(table)
    if found == 'graph':
      hit = first_text_hit(r['text'], r['ref'])
      if hit is not None:
        c['graph page, text search finds a page'] += 1
        c['graph page, text search finds the same page'] += hit == page
  for k, v in c.items():
    print(f'{v:6d}  {k}')


if __name__ == '__main__':
  main()

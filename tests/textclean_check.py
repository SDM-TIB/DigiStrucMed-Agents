"""Checks that removing HTML tags keeps guideline text, on constructed cases and on the whole page corpus.

  python tests/textclean_check.py

Cases: real tags go, clinical "<" and ">" stay (the OCR text of Positionspapiere p. 9 and the LVAD criteria table
lost whole paragraphs to a <[^>]+> pattern). Corpus: after cleaning, every word of every page's raw text, with the
real tags removed by an HTML parser, is still there.
"""
import html
import pathlib
import re
import sys
from html.parser import HTMLParser

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'app'))
import textclean  # noqa: E402

CASES = [
  ('<td>5. Hypoperfusion (PAWP > 20 mm Hg und SBP &#x3C; 80–90 mm Hg)</td>', '5. Hypoperfusion (PAWP > 20 mm Hg und SBP &#x3C; 80–90 mm Hg)'),
  ('CPAP auf <15/h gesenkt. Kleinere Untersuchungen: ca. 50%. LVEF >40% aus', 'CPAP auf <15/h gesenkt. Kleinere Untersuchungen: ca. 50%. LVEF >40% aus'),
  ('cost <$50,000 per QALY; intermediate $50,000 to <$150,000', 'cost <$50,000 per QALY; intermediate $50,000 to <$150,000'),
  ('Class<sup>a</sup> I', 'Class a I'),
  ('<table class="x"><tr><th>Level</th></tr></table>', 'Level'),
  ('a<br/>b<BR>c', 'a b c'),
  ('x < y and y > z', 'x < y and y > z'),
]


class _Text(HTMLParser):
  def __init__(self):
    super().__init__(convert_charrefs=False)
    self.parts = []

  def handle_data(self, data):
    self.parts.append(data)


def main():
  bad = 0
  for raw, want in CASES:
    got = ' '.join(textclean.strip_tags(raw).split())
    if got != want:
      bad += 1
      print('FAIL', repr(raw), '->', repr(got))
  print(f'cases: {len(CASES) - bad}/{len(CASES)} ok')

  import corpus  # the app's page corpus (needs GRAPH_ENDPOINT only for other modules, not this one)
  lost_pages = 0
  for p in corpus.PAGES:
    parser = _Text()
    parser.feed(p['text'])
    reference = set(re.findall(r'\w+', html.unescape(' '.join(parser.parts)).lower()))
    kept = set(re.findall(r'\w+', html.unescape(textclean.strip_tags(p['text'])).lower()))
    if reference - kept:
      lost_pages += 1
  print(f'pages losing words: {lost_pages} of {len(corpus.PAGES)}')
  return 1 if bad or lost_pages else 0


if __name__ == '__main__':
  raise SystemExit(main())

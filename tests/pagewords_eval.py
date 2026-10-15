"""How often app/pagewords.py finds a sentence of the transcription on the original page.

  python tests/pagewords_eval.py [pages per kind]

takes random pages (fixed seed), from the PDF text layer and from OCR'd scans separately, picks up to three prose
sentences per page from the corpus transcription and asks pagewords.locate to find them (exact mode). a found
sentence counts only if its marked region holds the sentence: the rectangles must not be empty and their combined
height must be at most three times what the sentence needs. what it does not measure: whether the right words are
underlined, that needs a look at the page (the source viewer shows it).
"""
import random
import re
import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
import corpus  # noqa: E402
import pagewords  # noqa: E402

N = int(sys.argv[1]) if len(sys.argv) > 1 else 80
pagewords._load()
rng = random.Random(42)
for kind in ('pdf', 'ocr'):
  ids = sorted(pid for pid, (src, words, _) in pagewords._pages.items() if src == kind and len(words) > 40)
  found = total = 0
  for pid in rng.sample(ids, min(N, len(ids))):
    text = re.sub(r'\s+', ' ', corpus.PAGE_BY_ID[pid]['plain'])
    sents = [s.strip() for s in re.split(r'(?<=[.!?])\s+', text) if 60 < len(s) < 300 and '|' not in s and '#' not in s]
    for s in rng.sample(sents, min(3, len(sents))):
      total += 1
      r = pagewords.locate(pid, s, 'exact')
      lines = max(1, len(s) // 90)
      if r and r['rects'] and sum(y1 - y0 for _, y0, _, y1 in r['rects']) < 3 * lines * 0.02 + 0.03:
        found += 1
  print(f'{kind}: {found}/{total} sentences found on {min(N, len(ids))} pages ({found / max(1, total):.0%})')

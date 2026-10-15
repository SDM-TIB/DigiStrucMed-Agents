"""Every word of every guideline page with its position on the original A4 page -> app/data/page_words.jsonl.gz.

  python pipeline/build_page_words.py <MHH-Dataset-OCR-Pipeline>

the corpus text (the MHH OCR transcription) has no coordinates. this adds them, so a quote can be underlined on the
page image itself (app/pagewords.py finds the words, the source viewer draws the marks):
  - pages with a text layer (2,220 of 2,780): the words and boxes straight from the PDF (PyMuPDF), exact
  - scanned pages without one (560): OCR on the rendered page PNG with RapidOCR, latin PP-OCRv5 mobile model, word
    boxes. needs `pip install rapidocr onnxruntime` (only for this script; the app does not need it)
positions are stored in 1/10000 of the page width and height, so they fit any rendering of the page.
an interrupted run resumes: finished pages are kept in page_words.partial.jsonl next to the output.
"""
import base64
import gzip
import json
import os
import pathlib
import sys
from array import array
from concurrent.futures import ProcessPoolExecutor

import pymupdf

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / 'app' / 'data' / 'page_words.jsonl.gz'
PARTIAL = OUT.with_name('page_words.partial.jsonl')
SCALE = 10000
MIN_WORDS = 20  # fewer words in the text layer than this: treat the page as a scan
_ocr = None


def _norm(x, size):
  return max(0, min(SCALE, round(x / size * SCALE)))


def pdf_words(page):
  """words and boxes from the text layer, mapped to the page as it is displayed (rotation included)"""
  w, h = page.rect.width, page.rect.height
  words, boxes = [], []
  # the pdf's own reading order. sort=True would sort by line across the page and interleave two-column layouts
  for x0, y0, x1, y1, text, *_ in page.get_text('words', sort=False):
    r = pymupdf.Rect(x0, y0, x1, y1) * page.rotation_matrix
    words.append(text)
    boxes += [_norm(r.x0, w), _norm(r.y0, h), _norm(r.x1, w), _norm(r.y1, h)]
  return words, boxes


def _init_ocr():
  global _ocr
  from rapidocr import RapidOCR
  from rapidocr.utils.typings import LangRec, ModelType, OCRVersion
  _ocr = RapidOCR(params={'Rec.lang_type': LangRec.LATIN, 'Rec.ocr_version': OCRVersion.PPOCRV5, 'Rec.model_type': ModelType.MOBILE,
                          'Global.log_level': 'warning'})


def ocr_words(job):
  """(page_id, png path) -> (page_id, words, boxes) from rapidocr word boxes on the half-size render"""
  from PIL import Image
  pid, png = job
  im = Image.open(png).convert('RGB')
  im = im.resize((im.width // 2, im.height // 2))
  try:
    out = _ocr(im, return_word_box=True)
  except Exception as e:  # one unreadable page must not stop the run
    print(f'ocr failed on {pid}: {type(e).__name__}', flush=True)
    return pid, [], []
  words, boxes = [], []
  for line in out.word_results or ():
    for item in line or ():
      # rapidocr returns empty or malformed entries for lines it detected but could not read: skip them
      if not isinstance(item, (tuple, list)) or len(item) < 3 or not isinstance(item[0], str) or not item[0].strip():
        continue
      poly = item[2]
      if not isinstance(poly, (tuple, list)) or not all(isinstance(q, (tuple, list)) and len(q) >= 2 for q in poly):
        continue
      text = item[0]
      xs, ys = [q[0] for q in poly], [q[1] for q in poly]
      words.append(text)
      boxes += [_norm(min(xs), im.width), _norm(min(ys), im.height), _norm(max(xs), im.width), _norm(max(ys), im.height)]
  return pid, words, boxes


def main(base):
  base = pathlib.Path(base)
  corpus = {p['id'] for p in json.load(gzip.open(ROOT / 'app' / 'data' / 'corpus.json.gz', 'rt', encoding='utf-8'))['pages']}
  pngs = {}
  for line in open(base / '1_rendered_pages' / 'manifest.jsonl', encoding='utf-8'):
    if line.strip():
      r = json.loads(line)
      pngs[r['page_id']] = base / r['artifact_path']
  done = {}
  if PARTIAL.exists():
    for line in open(PARTIAL, encoding='utf-8'):
      r = json.loads(line)
      done[r['id']] = r
  log = open(PARTIAL, 'a', encoding='utf-8')
  scans = []
  for line in open(base / '0_deduplicated_pdfs' / 'manifest.jsonl', encoding='utf-8'):
    if not line.strip():
      continue
    rec = json.loads(line)
    doc = pymupdf.open(base / rec['artifact_path'])
    for i, page in enumerate(doc):
      pid = f"{rec['document_id']}_p{i + 1:04d}"
      if pid not in corpus or pid in done:
        continue
      words, boxes = pdf_words(page)
      if len(words) >= MIN_WORDS:
        done[pid] = {'id': pid, 'src': 'pdf', 'words': words, 'boxes': boxes}
        log.write(json.dumps(done[pid], ensure_ascii=False) + '\n')
      elif pid in pngs:
        scans.append((pid, str(pngs[pid])))
  log.flush()
  print(f'{len(done)} pages done, {len(scans)} scanned pages to OCR', flush=True)
  os.environ.setdefault('OMP_NUM_THREADS', '4')
  with ProcessPoolExecutor(4, initializer=_init_ocr) as pool:
    for n, (pid, words, boxes) in enumerate(pool.map(ocr_words, scans, chunksize=2), 1):
      done[pid] = {'id': pid, 'src': 'ocr', 'words': words, 'boxes': boxes}
      log.write(json.dumps(done[pid], ensure_ascii=False) + '\n')
      log.flush()
      if n % 20 == 0:
        print(f'ocr {n}/{len(scans)}', flush=True)
  log.close()
  # compact for the server: words joined by \x1f, boxes as little-endian uint16 in base64. a few MB in memory, not hundreds
  pages = {pid: {'src': r['src'], 'words': '\x1f'.join(r['words']),
                 'boxes': base64.b64encode(array('H', r['boxes']).tobytes()).decode()} for pid, r in done.items() if pid in corpus}
  with gzip.open(OUT, 'wt', encoding='utf-8') as fh:  # one page per line, so the server reads it line by line
    fh.write(json.dumps({'scale': SCALE, 'pages': len(pages)}) + '\n')
    for pid, p in pages.items():
      fh.write(json.dumps({'id': pid, **p}, ensure_ascii=False, separators=(',', ':')) + '\n')
  missing = len(corpus) - len(pages)
  print(f'{len(pages)} pages -> {OUT} ({OUT.stat().st_size / 1e6:.1f} MB), {missing} pages without words')
  PARTIAL.unlink()


if __name__ == '__main__':
  if len(sys.argv) != 2:
    raise SystemExit(__doc__)
  main(sys.argv[1])

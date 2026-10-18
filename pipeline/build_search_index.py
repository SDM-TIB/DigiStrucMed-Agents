"""Build the EmbeddingGemma 2 page index of Luna's search (app/search.py) and pin it in app/search.lock.json.

  python pipeline/build_search_index.py --from <dir with the study's npz files> [--publish]
  python pipeline/build_search_index.py --embed-url https://tib-embed-...run.app [--publish]
  python pipeline/build_search_index.py --base app/data/search_index.npz --images page_images.npz [--publish]

--from packs vectors computed on a GPU by the retrieval study (embed.py there: chunk_title_s400.npz,
chunk_title_s600.npz, chunk_title.npz, page_plain.npz, made from the same page corpus), after checking that every
view has exactly the units app/pageunits.py makes from app/data/corpus.json.gz, page by page.
--embed-url embeds the units through the embedding service instead (slow on CPU; for a changed corpus).
--base takes the text views from an existing index (checked against app/search.lock.json).
--images adds the page-image view (IMAGE_VIEW): one vector per page, the rendered PDF page embedded as an image by the
same model at an image budget of 280 soft tokens (embed_page_images.py of the semantic page search study,
MASTERTHESIS experiments/2026-10-18_semantic_page_search; key image_280, ids = page ids). Every page must have one.

Writes pipeline/out/search_index.npz. --publish uploads it to gs://<PAGE_BUCKET>/search/<sha256[:12]>/ and writes
app/search.lock.json, which the app checks before it uses an index.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
import corpus  # noqa: E402
import pageunits  # noqa: E402
from search import IMAGE_VIEW, LOCK_FILE, VIEWS  # noqa: E402

MODEL = {'id': 'google/embeddinggemma-2', 'revision': '914f7f89142e33e77833254d9c9b90c3cef7303b',
         'document_prompt': 'title: {title} | text: {text}', 'pooling': 'mean over all tokens, L2-normalised'}


def from_study(src):
  views = {}
  for v in VIEWS:
    z = np.load(src / f'{v}.npz')
    owner = z['page'].astype(np.int32)
    units = pageunits.page_units(corpus.PAGES, v)
    if len(units) != len(owner) or any(int(a) != b for a, (b, _) in zip(owner, units)):
      raise SystemExit(f'{v}: the study vectors do not match the units of app/pageunits.py on this corpus')
    views[v] = (z['vec'].astype(np.float16), owner)
  return views


def from_index(path):
  """the text views of an existing index, after checking it is the pinned one"""
  data = Path(path).read_bytes()
  if hashlib.sha256(data).hexdigest() != json.loads(LOCK_FILE.read_text(encoding='utf-8'))['sha256']:
    raise SystemExit(f'{path} is not the index pinned in app/search.lock.json')
  z = np.load(path)
  if [str(x) for x in z['page_ids']] != [p['id'] for p in corpus.PAGES]:
    raise SystemExit(f'{path}: pages differ from the page corpus')
  return {v: (z[f'{v}_vec'].astype(np.float16), z[f'{v}_page'].astype(np.int32)) for v in VIEWS}


def page_images(path, key='image_280'):
  """the page-image view in corpus page order; every page needs its image vector"""
  z = np.load(path, allow_pickle=True)
  row = {str(pid): j for j, pid in enumerate(z['ids'])}
  missing = [p['id'] for p in corpus.PAGES if p['id'] not in row]
  if missing:
    raise SystemExit(f'{path}: {len(missing)} pages without an image vector, e.g. {missing[0]}')
  vec = z[key][[row[p['id']] for p in corpus.PAGES]].astype(np.float16)
  return vec, np.arange(len(corpus.PAGES), dtype=np.int32), {'budget': int(key.split('_')[1]), 'source': str(z['meta'])}


def from_service(url, batch=16):
  sys.path.insert(0, str(ROOT / 'app'))
  import search
  import config
  config.EMBED_URL = url
  views = {}
  for v in VIEWS:
    units = pageunits.page_units(corpus.PAGES, v)
    t0, out = time.time(), []
    for i in range(0, len(units), batch):
      out.append(search.embed([t for _, t in units[i:i + batch]], timeout=600))
    views[v] = (np.concatenate(out).astype(np.float16), np.array([p for p, _ in units], dtype=np.int32))
    print(f'{v}: {len(units)} units in {time.time() - t0:.0f}s', flush=True)
  return views


def main():
  ap = argparse.ArgumentParser()
  src = ap.add_mutually_exclusive_group(required=True)
  src.add_argument('--from', dest='src', type=Path)
  src.add_argument('--embed-url')
  src.add_argument('--base', type=Path)
  ap.add_argument('--images', type=Path)
  ap.add_argument('--publish', action='store_true')
  a = ap.parse_args()
  views = from_study(a.src) if a.src else from_index(a.base) if a.base else from_service(a.embed_url)
  meta = {'model': MODEL, 'views': {v: {'units': len(views[v][1])} for v in VIEWS}, 'pages': len(corpus.PAGES),
          'source': str(a.src or a.base or a.embed_url), 'built': time.strftime('%Y-%m-%d')}
  if a.images:
    vec, owner, info = page_images(a.images)
    views[IMAGE_VIEW] = (vec, owner)
    meta['views'][IMAGE_VIEW] = {'units': len(owner), 'image_budget': info['budget'], 'source': info['source'],
                                 'document': 'the rendered page as an image, no prompt'}
  out = ROOT / 'pipeline' / 'out' / 'search_index.npz'
  out.parent.mkdir(exist_ok=True)
  arrays = {'page_ids': np.array([p['id'] for p in corpus.PAGES]), 'meta': np.array(json.dumps(meta))}
  for v, (vec, owner) in views.items():
    arrays[f'{v}_vec'], arrays[f'{v}_page'] = vec, owner
  np.savez(out, **arrays)
  data = out.read_bytes()
  sha = hashlib.sha256(data).hexdigest()
  print(f'{out}: {len(data)} bytes, sha256 {sha}')
  if a.publish:
    bucket = os.environ.get('PAGE_BUCKET') or json.loads((ROOT / 'app' / 'search.lock.json').read_text())['bucket']
    obj = f'search/{sha[:12]}/search_index.npz'
    subprocess.run(['gcloud', 'storage', 'cp', str(out), f'gs://{bucket}/{obj}'], check=True, shell=os.name == 'nt')
    lock = {'object': obj, 'bucket': bucket, 'sha256': sha, 'bytes': len(data), 'meta': meta}
    (ROOT / 'app' / 'search.lock.json').write_text(json.dumps(lock, indent=1) + '\n', encoding='utf-8')
    print('published', obj)


if __name__ == '__main__':
  main()

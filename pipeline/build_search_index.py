"""Build the EmbeddingGemma 2 page index of Luna's search (app/search.py) and pin it in app/search.lock.json.

  python pipeline/build_search_index.py --from <dir with the study's npz files> [--publish]
  python pipeline/build_search_index.py --embed-url https://tib-embed-...run.app [--publish]

--from packs vectors computed on a GPU by the retrieval study (embed.py there: chunk_title_s400.npz,
chunk_title_s600.npz, chunk_title.npz, page_plain.npz, made from the same page corpus), after checking that every
view has exactly the units app/pageunits.py makes from app/data/corpus.json.gz, page by page.
--embed-url embeds the units through the embedding service instead (slow on CPU; for a changed corpus).

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
from search import VIEWS  # noqa: E402

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
  ap.add_argument('--publish', action='store_true')
  a = ap.parse_args()
  views = from_study(a.src) if a.src else from_service(a.embed_url)
  meta = {'model': MODEL, 'views': {v: {'units': len(views[v][1])} for v in VIEWS}, 'pages': len(corpus.PAGES),
          'source': str(a.src or a.embed_url), 'built': time.strftime('%Y-%m-%d')}
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

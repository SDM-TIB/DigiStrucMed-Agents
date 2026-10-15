"""Add a Tavus account to the video call pool, or complete the ones that are in it.

  python deploy/deploy_new_tavus.py <api-key>                       # 60 plan minutes, period from today
  python deploy/deploy_new_tavus.py <api-key> --plan 120 --since 2026-10-07
  python deploy/deploy_new_tavus.py <api-key> --deploy              # then also push the secrets and deploy the app
  python deploy/deploy_new_tavus.py --all                           # check every account: agents and knowledge base

what it does:
  1. adds the key to TAVUS_EXTRA_KEYS in .env (unless it is already TAVUS_API_KEY or in there)
  2. creates or updates luna, lino and guido with their tools in every account (tavus/sync.py)
  3. copies luna's knowledge base into every account that has none: the 132 texts listed in tavus/knowledge_base.json,
     each checked against its sha256, handed to tavus as signed links to our private bucket (valid 3 hours).
     documents already in the account under the same tag and name are reused, so it can be run again after a failure
  4. attaches the ready documents to luna there and records them in app/data/tavus_accounts.json

needs: gcloud signed in with the Service Account Token Creator role on the app's runtime service account (signed links).
"""
import concurrent.futures
import hashlib
import json
import pathlib
import subprocess
import shutil
import sys
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
KB = ROOT / 'tavus' / 'knowledge_base'
sys.path.insert(0, str(ROOT / 'deploy'))
sys.path.insert(0, str(ROOT / 'tavus'))
import deploy  # noqa: E402
import sync  # noqa: E402


def api(key, method, path, body=None):
  req = urllib.request.Request('https://tavusapi.com/v2/' + path, data=json.dumps(body).encode() if body is not None else None,
                               method=method, headers={'x-api-key': key, 'Content-Type': 'application/json'})
  try:
    with urllib.request.urlopen(req, timeout=60) as r:
      raw = r.read()
      return json.loads(raw) if raw else {}
  except urllib.error.HTTPError as e:
    raise RuntimeError(f'{method} {path}: HTTP {e.code} {e.read()[:200].decode(errors="replace")}') from None


def remember(key):
  env = deploy.dotenv()
  if key in (env.get('TAVUS_API_KEY', ''), *env.get('TAVUS_EXTRA_KEYS', '').split(',')):
    return
  path = ROOT / '.env'
  lines = path.read_text(encoding='utf-8').splitlines()
  extra = ','.join(k for k in [*env.get('TAVUS_EXTRA_KEYS', '').split(','), key] if k.strip())
  lines = [ln for ln in lines if not ln.startswith('TAVUS_EXTRA_KEYS=')]
  at = next((n + 1 for n, ln in enumerate(lines) if ln.startswith('TAVUS_API_KEY=')), len(lines))
  lines.insert(at, f'TAVUS_EXTRA_KEYS={extra}')
  path.write_bytes(('\n'.join(lines) + '\n').encode('utf-8'))


def signed_links(env):
  """verify the local copies against the manifest, then sign the bucket objects. returns file -> url"""
  manifest = json.loads((ROOT / 'tavus' / 'knowledge_base.json').read_text(encoding='utf-8'))
  gcloud, bucket, project = shutil.which('gcloud') or 'gcloud', env['PAGE_BUCKET'], env['GCP_PROJECT']
  if not KB.exists():
    KB.mkdir(parents=True)
    subprocess.run([gcloud, 'storage', 'rsync', f'gs://{bucket}/assets/tavus-kb', str(KB)], check=True, capture_output=True)
  for d in manifest['documents']:
    if hashlib.sha256((KB / d['file']).read_bytes()).hexdigest() != d['sha256']:
      raise SystemExit(f'{d["file"]} does not match its sha256 in tavus/knowledge_base.json')
  number = subprocess.run([gcloud, 'projects', 'describe', project, '--format=value(projectNumber)'], check=True, capture_output=True,
                          text=True).stdout.strip()
  out = subprocess.run([gcloud, 'storage', 'sign-url', f'gs://{bucket}/assets/tavus-kb/*.txt', '--duration=3h', f'--project={project}',
                        f'--impersonate-service-account={number}-compute@developer.gserviceaccount.com', '--format=json'],
                       check=True, capture_output=True, text=True).stdout
  return manifest, {r['resource'].split('#')[0].rsplit('/', 1)[-1]: r['signed_url'] for r in json.loads(out)}  # resource ends in #generation


def copy_knowledge_base(key, acct, manifest, links):
  tag = manifest['tag']
  have, page = {}, 0
  while True:
    rows = api(key, 'GET', f'documents?limit=100&page={page}')['data']
    have.update({r['document_name']: r for r in rows if tag in (r.get('tags') or []) and r['status'] != 'error'})
    if len(rows) < 100:
      break
    page += 1

  def submit(d):
    if d['name'] in have:
      return have[d['name']]['document_id']
    for _ in range(240):  # tavus processes at most 15 documents at a time per account
      try:
        return api(key, 'POST', 'documents', {'document_url': links[d['file']], 'document_name': d['name'], 'tags': [tag]})['document_id']
      except RuntimeError as e:
        if 'HTTP 429' not in str(e):
          raise
        time.sleep(15)
    raise RuntimeError('tavus stayed busy for an hour')

  with concurrent.futures.ThreadPoolExecutor(3) as pool:
    ids = list(pool.map(submit, manifest['documents']))
  for _ in range(180):
    status = [api(key, 'GET', 'documents/' + i)['status'] for i in ids]
    print(f'  {acct["name"]}: {status.count("ready")} of {len(ids)} documents ready, {status.count("error")} failed', flush=True)
    if all(s in ('ready', 'error') for s in status):
      break
    time.sleep(20)
  failed = [i for i, s in zip(ids, status) if s == 'error']
  if failed:
    raise SystemExit(f'{acct["name"]}: {len(failed)} documents failed. delete them in the tavus dashboard and run again')
  return [i for i, s in zip(ids, status) if s == 'ready']


def main():
  args = [a for a in sys.argv[1:] if not a.startswith('--')]
  opt = lambda name, default: sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default
  if args:
    remember(args[0])
  elif '--all' not in sys.argv:
    raise SystemExit(__doc__)
  data, todo = sync.load(float(opt('--plan', 60)), opt('--since', None))
  sync.save(data)
  links = None
  for key, acct in todo:
    sync.sync_account(key, acct)
    if len(acct.get('documents', [])) < len(json.loads((ROOT / 'tavus' / 'knowledge_base.json').read_text())['documents']):
      if links is None:
        manifest, links = signed_links(deploy.dotenv())
      acct['documents'] = copy_knowledge_base(key, acct, manifest, links)
      sync.sync_account(key, acct)  # attach them to luna
    sync.save(data)
  if '--deploy' in sys.argv:
    deploy.deploy_app(deploy.dotenv())


if __name__ == '__main__':
  main()

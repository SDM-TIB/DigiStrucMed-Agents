"""Add a Modal workspace to Lino's GPU pool, or redeploy Lino in one that is already in it.

  python deploy/deploy_new_modal.py <token-id> <token-secret>           # the pair from `modal token new` or the dashboard
  python deploy/deploy_new_modal.py <token-id> <token-secret> --deploy  # then also push the secrets and deploy the app
  python deploy/deploy_new_modal.py --all                               # redeploy lino in every workspace of the pool

what it does, in that workspace:
  1. checks the token and reads the workspace name
  2. creates the Modal Secret lino-api-key (the same LINO_API_KEY in every workspace)
  3. merges Llama 3.2 1B and gpu/adapter into the volume lino-model, once (gpu/serve.py::prepare, a few minutes)
  4. deploys gpu/serve.py and waits until Lino answers on /health (one cold start, about 4 minutes of L4 time)
  5. records the workspace in app/data/modal_accounts.json (name, url, fingerprint of the token id. never the secret)
  6. adds the token to MODAL_EXTRA_TOKENS in .env unless it is already there or is MODAL_TOKEN_ID

the app takes the token from Secret Manager after `python deploy/deploy.py app` (or --deploy here).
needs: the modal package, gpu/adapter (python deploy/deploy.py assets pull), LINO_API_KEY in .env.
"""
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
ACCOUNTS = ROOT / 'app' / 'data' / 'modal_accounts.json'
sys.path.insert(0, str(ROOT / 'deploy'))
import deploy  # noqa: E402  same .env reader and deploy as everything else


def fingerprint(token_id):
  return hashlib.sha256(token_id.encode()).hexdigest()[:12]


def pool(env):
  """every (token id, secret) the app knows: MODAL_TOKEN_ID/SECRET plus MODAL_EXTRA_TOKENS (id:secret, comma separated)"""
  pairs = [(env.get('MODAL_TOKEN_ID', ''), env.get('MODAL_TOKEN_SECRET', ''))]
  pairs += [tuple(p.strip().split(':', 1)) for p in env.get('MODAL_EXTRA_TOKENS', '').split(',') if ':' in p]
  return [(i.strip(), s.strip()) for i, s in pairs if i.strip() and s.strip()]


def modal(token, *args, check=True):
  # utf-8: modal prints check marks, which the windows console code page cannot encode
  env = {**os.environ, 'MODAL_TOKEN_ID': token[0], 'MODAL_TOKEN_SECRET': token[1], 'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8'}
  env.pop('MODAL_PROFILE', None)
  out = subprocess.run([sys.executable, '-m', 'modal', *args], capture_output=True, text=True, encoding='utf-8', errors='replace',
                       cwd=ROOT, env=env)
  if check and out.returncode:
    raise SystemExit(f'modal {" ".join(args[:3])} failed:\n{(out.stderr or out.stdout)[-2000:]}')
  return out.stdout + out.stderr


def workspace(token):
  import modal as m
  w = m.Workspace.from_context(client=m.Client.from_credentials(*token))
  w.hydrate()
  return w.name


def healthy(url, timeout=15 * 60):
  t0 = time.time()
  while time.time() - t0 < timeout:
    try:
      with urllib.request.urlopen(url + '/health', timeout=30) as r:
        if r.status == 200:
          return round(time.time() - t0)
    except Exception:
      pass
    time.sleep(10)
  return None


def deploy_lino(token, env):
  name = workspace(token)
  print(f'{name}: secret', flush=True)
  modal(token, 'secret', 'create', '--force', 'lino-api-key', f'LINO_API_KEY={env["LINO_API_KEY"]}')
  if not modal(token, 'volume', 'ls', 'lino-model', 'lino', check=False).strip().count('config.json'):
    print(f'{name}: merging the weights (once, a few minutes)', flush=True)
    modal(token, 'run', 'gpu/serve.py::prepare')
  print(f'{name}: deploying gpu/serve.py', flush=True)
  out = modal(token, 'deploy', 'gpu/serve.py')
  found = re.findall(r'https://[\w.-]+\.modal\.(?:direct|run)', out)
  url = found[-1] if found else f'https://{name}--lino-server.eu-west.modal.direct'
  print(f'{name}: waiting for {url}/health (cold start)', flush=True)
  took = healthy(url)
  if took is None:
    raise SystemExit(f'{name}: lino did not come up within 15 minutes. look at the app logs in the modal dashboard')
  print(f'{name}: lino is up after {took} s. modal stops the container after 5 idle minutes', flush=True)
  return {'name': name, 'token_sha256': fingerprint(token[0]), 'url': url, 'plan_usd': 30.0}


def record(entry):
  data = json.loads(ACCOUNTS.read_text(encoding='utf-8'))
  data['accounts'] = [a for a in data['accounts'] if a['token_sha256'] != entry['token_sha256']] + [entry]
  ACCOUNTS.write_bytes((json.dumps(data, indent=2, ensure_ascii=False) + '\n').encode('utf-8'))


def remember(token, env):
  if token in pool(env):
    return
  path = ROOT / '.env'
  lines = path.read_text(encoding='utf-8').splitlines()
  extra = ','.join(f'{i}:{s}' for i, s in pool(env)[1:] + [token])
  lines = [ln for ln in lines if not ln.startswith('MODAL_EXTRA_TOKENS=')]
  at = next((n + 1 for n, ln in enumerate(lines) if ln.startswith('MODAL_TOKEN_SECRET=')), len(lines))
  lines.insert(at, f'MODAL_EXTRA_TOKENS={extra}')
  path.write_bytes(('\n'.join(lines) + '\n').encode('utf-8'))


if __name__ == '__main__':
  args = [a for a in sys.argv[1:] if not a.startswith('--')]
  env = deploy.dotenv()
  if '--all' in sys.argv:
    tokens = pool(env)
  elif len(args) == 2:
    tokens = [(args[0], args[1])]
  else:
    raise SystemExit(__doc__)
  for token in tokens:
    record(deploy_lino(token, env))
    remember(token, env)
    env = deploy.dotenv()
  if '--deploy' in sys.argv:
    deploy.deploy_app(deploy.dotenv())

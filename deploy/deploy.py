"""Deploy TIB Chat. reads everything from the .env at the repo root, or from the environment when there is none (CI).

  python deploy/deploy.py app      # Cloud Run service (secrets to Secret Manager, then a source deploy of app/)
  python deploy/deploy.py app --code-only   # only the source deploy. what GitHub Actions runs: no .env, no secret
                                            # values, the service keeps the Secret Manager references it already has
  python deploy/deploy.py embed    # EmbeddingGemma 2 service for Luna's search (embed-server/), private; prints EMBED_URL
  python deploy/deploy.py agent    # Luna's agent server (agent-server/, Mastra), private; prints AGENT_URL
  python deploy/deploy.py gpu      # Lino's vLLM server in every Modal workspace of the pool (deploy_new_modal.py --all)
  python deploy/deploy.py assets push|pull   # videos and the LoRA adapter, kept in the bucket, not in git

needs: gcloud (logged in), and for `gpu` the modal package with a token (pip install modal, modal token new).
secret values go to Secret Manager over stdin. nothing is printed, nothing touches disk.
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
SECRETS = ('TAVUS_API_KEY', 'TAVUS_EXTRA_KEYS', 'DEMO_PASSWORD', 'ADMIN_PASSWORD', 'LLM_API_KEY', 'LINO_API_KEY',
           'MODAL_TOKEN_ID', 'MODAL_TOKEN_SECRET', 'MODAL_EXTRA_TOKENS', 'CF_ACCOUNT_ID', 'CF_API_TOKEN')
SETTINGS = ('LANGUAGES', 'LLM_BASE_URL', 'LLM_MODEL', 'LLM_FALLBACK_MODEL', 'LLM_REASONING_EFFORT', 'MAX_CALL_SECONDS', 'GRAPH_ENDPOINT',
            'EMBED_URL', 'AGENT_URL')
# large files that live in the bucket under assets/, not in git
ASSETS = {'app/static/videos': 'assets/videos', 'gpu/adapter': 'assets/adapter', 'tavus/knowledge_base': 'assets/tavus-kb'}


def dotenv():
  if not (ROOT / '.env').exists():
    return dict(os.environ)  # ci: settings come from repository variables, secrets stay in Secret Manager
  env = {}
  for line in (ROOT / '.env').read_text(encoding='utf-8').splitlines():
    if line.strip() and not line.lstrip().startswith('#') and '=' in line:
      k, v = line.split('=', 1)
      env[k.strip()] = v.strip().strip('"')
  # settings set in the environment win over the .env, as in app/config.py. the .env holds the local GRAPH_ENDPOINT
  # (the IAP tunnel); a deploy gets the VPC address: GRAPH_ENDPOINT=http://10.156.0.3:7001 python deploy/deploy.py app
  return env | {k: os.environ[k] for k in SETTINGS if os.environ.get(k)}


def run(*cmd, stdin=None, check=True, quiet=False):
  head = [sys.executable, '-m', 'modal'] if cmd[0] == 'modal' else [shutil.which(cmd[0]) or cmd[0]]  # gcloud is gcloud.cmd on windows
  out = subprocess.run([*head, *cmd[1:]], input=stdin, text=True, capture_output=True, cwd=ROOT)
  if check and out.returncode:
    raise SystemExit(f'{" ".join(cmd[:4])} failed:\n{out.stderr[-2000:]}')
  if not quiet and out.stderr.strip():
    print(out.stderr.strip().splitlines()[-1])
  return out.stdout


def secret_name(var):
  return var.lower().replace('_', '-')


def put_secret(name, value, project):
  exists = run('gcloud', 'secrets', 'describe', name, f'--project={project}', check=False, quiet=True)
  if not exists:
    run('gcloud', 'secrets', 'create', name, '--replication-policy=automatic', '--data-file=-', f'--project={project}', stdin=value, quiet=True)
    return 'created'
  current = run('gcloud', 'secrets', 'versions', 'access', 'latest', f'--secret={name}', f'--project={project}', check=False, quiet=True)
  if current == value:
    return 'unchanged'
  run('gcloud', 'secrets', 'versions', 'add', name, '--data-file=-', f'--project={project}', stdin=value, quiet=True)
  return 'new version'


def deploy_app(env, code_only=False):
  project, region, service, bucket = env['GCP_PROJECT'], env.get('GCP_REGION', 'europe-west3'), env['CLOUD_RUN_SERVICE'], env['PAGE_BUCKET']
  if code_only:
    return source_deploy(env, project, region, service, bucket, secrets_flag=[])
  run('gcloud', 'services', 'enable', 'run.googleapis.com', 'cloudbuild.googleapis.com', 'artifactregistry.googleapis.com',
      'secretmanager.googleapis.com', f'--project={project}', quiet=True)
  number = run('gcloud', 'projects', 'describe', project, '--format=value(projectNumber)', quiet=True).strip()
  runtime = f'serviceAccount:{number}-compute@developer.gserviceaccount.com'
  for var in SECRETS:
    if env.get(var):
      print(f'{secret_name(var):20s} {put_secret(secret_name(var), env[var], project)}')
      run('gcloud', 'secrets', 'add-iam-policy-binding', secret_name(var), f'--member={runtime}',
          '--role=roles/secretmanager.secretAccessor', f'--project={project}', quiet=True)
  # the runtime reads page renders and writes the usage log, the build needs the builder role
  run('gcloud', 'storage', 'buckets', 'add-iam-policy-binding', f'gs://{bucket}', f'--member={runtime}', '--role=roles/storage.objectAdmin', quiet=True)
  run('gcloud', 'projects', 'add-iam-policy-binding', project, f'--member={runtime}', '--role=roles/cloudbuild.builds.builder',
      '--condition=None', quiet=True)
  secrets = ','.join(f'{v}={secret_name(v)}:latest' for v in SECRETS if env.get(v))
  source_deploy(env, project, region, service, bucket, secrets_flag=[f'--set-secrets={secrets}'])


def source_deploy(env, project, region, service, bucket, secrets_flag):
  # settings go through a yaml file: values can hold commas (LANGUAGES=en,de,ru). no secrets in it
  settings = {k: env[k] for k in SETTINGS if env.get(k)} | {'PAGE_BUCKET': bucket}
  fd, name = tempfile.mkstemp(suffix='.yaml')
  os.close(fd)
  env_file = pathlib.Path(name)
  env_file.write_text(''.join(f'{k}: {json.dumps(v)}' + chr(10) for k, v in settings.items()), encoding='utf-8')
  # one instance: gpu wake state, call timers and the usage log live in memory. min 1 = no cold start.
  # --no-invoker-iam-check because org policies often forbid allUsers. the app has its own password
  # Direct VPC egress: the graph server (graph-server/) has only an internal address in the default subnet
  print(run('gcloud', 'run', 'deploy', service, '--source', str(ROOT / 'app'), f'--region={region}', f'--project={project}',
            '--no-invoker-iam-check', '--min-instances=1', '--max-instances=1', '--memory=2Gi', '--cpu=1', '--timeout=900',
            '--network=default', '--subnet=default', '--vpc-egress=private-ranges-only',
            *secrets_flag, f'--env-vars-file={env_file}', '--quiet', quiet=True) or 'deployed')
  env_file.unlink()
  print(run('gcloud', 'run', 'services', 'describe', service, f'--region={region}', f'--project={project}', '--format=value(status.url)', quiet=True))


def deploy_embed(env):
  """the embedding service: private (no unauthenticated access), scales to zero, the app's runtime account may invoke
  it. 4 GiB holds the model with torch; the app wakes it when it starts and falls back to bm25 while it is cold"""
  project, region = env['GCP_PROJECT'], env.get('GCP_REGION', 'europe-west3')
  run('gcloud', 'run', 'deploy', 'tib-embed', '--source', str(ROOT / 'embed-server'), f'--region={region}', f'--project={project}',
      '--no-allow-unauthenticated', '--memory=4Gi', '--cpu=2', '--min-instances=0', '--max-instances=2', '--cpu-boost',
      '--timeout=120', '--concurrency=4', '--quiet', quiet=True)
  number = run('gcloud', 'projects', 'describe', project, '--format=value(projectNumber)', quiet=True).strip()
  run('gcloud', 'run', 'services', 'add-iam-policy-binding', 'tib-embed', f'--region={region}', f'--project={project}',
      f'--member=serviceAccount:{number}-compute@developer.gserviceaccount.com', '--role=roles/run.invoker', quiet=True)
  url = run('gcloud', 'run', 'services', 'describe', 'tib-embed', f'--region={region}', f'--project={project}',
            '--format=value(status.url)', quiet=True).strip()
  print(f'EMBED_URL={url}  (put it in .env and in the repository variable EMBED_URL, then deploy the app)')


def deploy_agent(env):
  """Luna's agent loop (Mastra on the Vercel AI SDK, Gemini on Vertex AI): private, scales to zero, the app's runtime
  account may invoke it. It runs as that same account and calls the app's tool endpoints back with an identity token
  of it, which the app accepts; no shared secret"""
  project, region, service = env['GCP_PROJECT'], env.get('GCP_REGION', 'europe-west3'), env['CLOUD_RUN_SERVICE']
  number = run('gcloud', 'projects', 'describe', project, '--format=value(projectNumber)', quiet=True).strip()
  runtime = f'serviceAccount:{number}-compute@developer.gserviceaccount.com'
  app_url = run('gcloud', 'run', 'services', 'describe', service, f'--region={region}', f'--project={project}',
                '--format=value(status.url)', quiet=True).strip()
  settings = {'APP_URL': app_url, 'GOOGLE_VERTEX_PROJECT': project, 'GOOGLE_VERTEX_LOCATION': 'global',
              'LUNA_MODEL': env.get('LUNA_MODEL', 'gemini-3.8-flash')}
  run('gcloud', 'run', 'deploy', 'luna-agent', '--source', str(ROOT / 'agent-server'), f'--region={region}', f'--project={project}',
      '--no-allow-unauthenticated', '--memory=1Gi', '--cpu=1', '--min-instances=0', '--max-instances=3', '--cpu-boost',
      '--timeout=600', '--concurrency=20', f'--service-account={runtime.split(":", 1)[1]}',
      '--set-env-vars=' + ','.join(f'{k}={v}' for k, v in settings.items()), '--quiet', quiet=True)
  run('gcloud', 'run', 'services', 'add-iam-policy-binding', 'luna-agent', f'--region={region}', f'--project={project}',
      f'--member={runtime}', '--role=roles/run.invoker', quiet=True)
  url = run('gcloud', 'run', 'services', 'describe', 'luna-agent', f'--region={region}', f'--project={project}',
            '--format=value(status.url)', quiet=True).strip()
  print(f'AGENT_URL={url}  (put it in .env and in the repository variable AGENT_URL, then deploy the app)')


def deploy_gpu(env):
  run(sys.executable, str(ROOT / 'deploy' / 'deploy_new_modal.py'), '--all')


def assets(env, direction):
  for local, remote in ASSETS.items():
    src, dst = (ROOT / local, f'gs://{env["PAGE_BUCKET"]}/{remote}') if direction == 'push' else (f'gs://{env["PAGE_BUCKET"]}/{remote}', ROOT / local)
    pathlib.Path(ROOT / local).mkdir(parents=True, exist_ok=True)
    run('gcloud', 'storage', 'rsync', str(src), str(dst), '--recursive', quiet=True)
    print(f'{direction} {local} <-> {remote}')


if __name__ == '__main__':
  what = sys.argv[1] if len(sys.argv) > 1 else ''
  env = dotenv()
  if what == 'app':
    deploy_app(env, code_only='--code-only' in sys.argv)
  elif what == 'agent':
    deploy_agent(env)
  elif what == 'embed':
    deploy_embed(env)
  elif what == 'gpu':
    deploy_gpu(env)
  elif what == 'assets' and sys.argv[2:] in (['push'], ['pull']):
    assets(env, sys.argv[2])
  else:
    raise SystemExit(__doc__)

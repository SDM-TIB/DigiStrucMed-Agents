"""Publish a graph version for the graph server, and point the server at it.

  python graph-server/publish_graph.py <graph.nq.gz>          upload (if new) and write graph.lock.json
  python graph-server/publish_graph.py --apply <graph.nq.gz>  ... then terraform apply and re-run the VM's startup
                                                                script, which indexes the new version and swaps servers

The file goes to gs://<project>-graph/graphs/<sha256>/<name>; graph.lock.json records name, sha256, bytes and object,
and terraform hands them to the VM as instance metadata. The VM checks the sha256 again before it builds the index, so
the server can only ever serve the exact file named here. Nothing is overwritten: each version has its own object.
"""
import argparse
import hashlib
import json
import pathlib
import shutil
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
LOCK = HERE / 'graph.lock.json'
PROJECT = 'tib-chat-20261019'
ZONE = 'europe-west3-a'
BUCKET = f'gs://{PROJECT}-graph'


def run(*cmd, cwd=None, capture=False):
  exe = shutil.which(cmd[0]) or cmd[0]  # gcloud and terraform are .cmd / .exe on windows
  out = subprocess.run([exe, *cmd[1:]], cwd=cwd, text=True, capture_output=capture)
  if out.returncode:
    sys.exit(f'{" ".join(cmd[:3])} failed' + (f':\n{out.stderr[-2000:]}' if capture else ''))
  return out.stdout if capture else ''


def sha256(path):
  h = hashlib.sha256()
  with open(path, 'rb') as f:
    for b in iter(lambda: f.read(16 << 20), b''):
      h.update(b)
  return h.hexdigest()


def main():
  ap = argparse.ArgumentParser()
  ap.add_argument('graph', type=pathlib.Path)
  ap.add_argument('--apply', action='store_true', help='terraform apply and re-run the startup script on the VM')
  ap.add_argument('--machine-type', default='e2-standard-4',
                  help='VM size while it indexes (default e2-standard-4, 16 GB); afterwards terraform apply without it serves on e2-small')
  a = ap.parse_args()
  digest = sha256(a.graph)
  obj = f'graphs/{digest}/{a.graph.name}'
  listed = subprocess.run([shutil.which('gcloud'), 'storage', 'ls', f'{BUCKET}/{obj}'], capture_output=True, text=True)
  if listed.returncode:
    run('gcloud', 'storage', 'cp', str(a.graph), f'{BUCKET}/{obj}')
  remote = json.loads(run('gcloud', 'storage', 'objects', 'describe', f'{BUCKET}/{obj}', '--format=json', capture=True))
  if int(remote['size']) != a.graph.stat().st_size:
    sys.exit(f'{BUCKET}/{obj}: {remote["size"]} bytes in the bucket, {a.graph.stat().st_size} local')
  lock = {'file': a.graph.name, 'sha256': digest, 'bytes': a.graph.stat().st_size, 'object': obj}
  LOCK.write_text(json.dumps(lock, indent=2) + '\n', encoding='utf-8')
  print('graph.lock.json ->', lock)
  if a.apply:
    run('terraform', 'apply', '-auto-approve', f'-var=machine_type={a.machine_type}', cwd=HERE / 'terraform')
    # detached on the VM (a transient systemd unit), so a dropped ssh session cannot stop the index build
    run('gcloud', 'compute', 'ssh', 'qlever-graph-server', f'--zone={ZONE}', f'--project={PROJECT}', '--tunnel-through-iap', '--quiet',
        '--command', 'sudo systemd-run --unit=qlever-reindex-$(date +%s) google_metadata_script_runner startup')
    print('re-indexing on the VM; follow /var/log/qlever-startup.log until it says "ready"')


if __name__ == '__main__':
  main()

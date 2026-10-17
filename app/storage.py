"""Tiny GCS client. On Cloud Run the metadata server hands us a token, no SDK needed.

Without a bucket (local dev) reads come from config.LOCAL_BUCKET and writes go nowhere.
"""
import json
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

import config

_token = {'value': '', 'exp': 0.0}
METADATA = 'http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token'


def _auth():
  if time.time() > _token['exp']:
    try:
      req = urllib.request.Request(METADATA, headers={'Metadata-Flavor': 'Google'})
      tok = json.load(urllib.request.urlopen(req, timeout=5))
      _token.update(value=tok['access_token'], exp=time.time() + tok['expires_in'] - 60)
    except OSError:  # no metadata server: local dev, use the developer's gcloud login
      out = subprocess.run([shutil.which('gcloud') or 'gcloud', 'auth', 'print-access-token'], capture_output=True, text=True, timeout=30)
      if out.returncode:
        raise
      _token.update(value=out.stdout.strip(), exp=time.time() + 30 * 60)
  return {'Authorization': 'Bearer ' + _token['value']}


def get(name):
  """bytes of one object, or None"""
  if not config.BUCKET:
    path = config.LOCAL_BUCKET / name
    return path.read_bytes() if path.is_file() else None
  url = f'https://storage.googleapis.com/storage/v1/b/{config.BUCKET}/o/{urllib.parse.quote(name, safe="")}?alt=media'
  try:
    with urllib.request.urlopen(urllib.request.Request(url, headers=_auth()), timeout=15) as r:
      return r.read()
  except (urllib.error.URLError, OSError):  # missing object, or no metadata server outside cloud run
    return None


def get_large(name, timeout=300):
  """bytes of a large object (an index file): streamed, with a long timeout; None if missing"""
  if not config.BUCKET:
    path = config.LOCAL_BUCKET / name
    return path.read_bytes() if path.is_file() else None
  url = f'https://storage.googleapis.com/storage/v1/b/{config.BUCKET}/o/{urllib.parse.quote(name, safe="")}?alt=media'
  try:
    with urllib.request.urlopen(urllib.request.Request(url, headers=_auth()), timeout=timeout) as r:
      return b''.join(iter(lambda: r.read(1 << 22), b''))
  except (urllib.error.URLError, OSError):
    return None


def put(name, data, content_type='application/octet-stream'):
  if not config.BUCKET:
    return
  url = f'https://storage.googleapis.com/upload/storage/v1/b/{config.BUCKET}/o?uploadType=media&name={urllib.parse.quote(name, safe="")}'
  req = urllib.request.Request(url, data=data, method='POST', headers={**_auth(), 'Content-Type': content_type})
  urllib.request.urlopen(req, timeout=20).read()

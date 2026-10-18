"""Luna's tools as HTTP endpoints for the agent server (agent-server/, a Mastra agent).

The agent loop runs in Mastra; retrieval stays here, in the evaluated Python code (search.py, luna.py). Every call
returns what the model sees ('model') and the ui events the browser needs ('ui': sources, read), which the agent
server forwards to the chat stream but keeps out of the model's context.

Who may call: a caller with a Google identity token for this app's url, issued to the service account this app
runs as (the agent server runs under the same account on Cloud Run; the token is checked at Google's tokeninfo
endpoint and cached until it expires). For local development AGENT_TOKEN in the environment also works.

One run is one answer: source numbers [n] count up across all tool calls of a run, so each run keeps its list of
pages here (in memory, dropped after an hour; the service runs as a single instance).
"""
import hmac
import json
import threading
import time
import urllib.parse
import urllib.request

import config
import live
import luna

RUN_TTL = 3600
_runs, _lock = {}, threading.Lock()


_verified, _own = {}, {}


def _own_account():
  """the service account this app runs as (metadata server), or '' outside Google Cloud"""
  if 'email' not in _own:
    try:
      req = urllib.request.Request('http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/email',
                                   headers={'Metadata-Flavor': 'Google'})
      _own['email'] = urllib.request.urlopen(req, timeout=5).read().decode().strip()
    except OSError:
      _own['email'] = ''
  return _own['email']


def authorised(headers, host):
  token = headers.get('X-Agent-Token')
  if token and config.AGENT_TOKEN:
    return hmac.compare_digest(token, config.AGENT_TOKEN)
  bearer = (headers.get('Authorization') or '').removeprefix('Bearer ').strip()
  if not bearer or not _own_account():
    return False
  hit = _verified.get(bearer)
  if hit is None:
    try:
      url = 'https://oauth2.googleapis.com/tokeninfo?id_token=' + urllib.parse.quote(bearer)
      info = json.load(urllib.request.urlopen(url, timeout=10))
    except OSError:
      return False
    hit = (info.get('email') == _own_account() and info.get('email_verified') in ('true', True)
           and info.get('aud') == f'https://{host}', float(info.get('exp') or 0))
    if len(_verified) > 1000:
      _verified.clear()
    _verified[bearer] = hit
  return hit[0] and time.time() < hit[1]


def _sources(run):
  now = time.time()
  with _lock:
    for r in [r for r, v in _runs.items() if now - v['t'] > RUN_TTL]:
      del _runs[r]
    entry = _runs.setdefault(str(run)[:80], {'t': now, 'sources': []})
    entry['t'] = now
    return entry['sources']


def _drive(gen):
  """run one of luna's tool generators: (ui events, return value)"""
  events = []
  try:
    while True:
      events.append(next(gen))
  except StopIteration as stop:
    return events, stop.value


def walk(run):
  """guido's walk of one run, if his tools were called"""
  with _lock:
    entry = _runs.get(str(run)[:80])
    return entry.get('walk') if entry else None


def _guido(tool, body):
  """guido v2's tools: explore, expand, search_pages, read_page over one walk per run (guido.Walk with the pages)"""
  import guido
  run = str(body.get('run') or 'default')
  _sources(run)  # creates and refreshes the run entry
  with _lock:
    entry = _runs[run[:80]]
    if 'walk' not in entry:
      entry['walk'] = guido.Walk(str(body.get('question') or ''), pages=True)
    w = entry['walk']
  events = []
  if tool == 'guido_explore':
    q = str(body.get('query', ''))[:200]
    model = w.explore(q, w.question, events)
  elif tool == 'guido_expand':
    nid = str(body.get('node_id', '')).strip()
    model = w.expand(nid, events)
  elif tool == 'guido_search':
    q = str(body.get('query', ''))[:300]
    model = w.search_pages(q, events, lambda ev: live.publish(run, {'type': 'search_progress', 'query': q, **ev}))
  elif tool == 'guido_read':
    model = w.read_pages(body.get('sources') or [], events)
  else:
    return None
  events.append({'type': 'sources', 'query': tool, 'items': w.sources()})
  return {'model': model, 'ui': events}


def call(tool, body):
  if tool.startswith('guido_'):
    return _guido(tool, body)
  run = body.get('run') or 'default'
  if tool == 'search':
    args = {k: body.get(k) for k in ('query', 'guideline', 'top_k') if body.get(k) not in (None, '')}
    progress = lambda ev: live.publish(run, {'type': 'search_progress', 'query': str(args.get('query', ''))[:300], **ev})
    ui, model = _drive(luna._search(args, _sources(run), str(body.get('question') or ''), progress))
  elif tool == 'read':
    ui, model = _drive(luna._read({'sources': body.get('sources') or []}, _sources(run)))
  elif tool == 'view':
    return luna._view({'sources': body.get('sources') or []}, _sources(run))
  elif tool == 'catalogue':
    ui, model = [], luna._catalogue({'topic': body.get('topic')})
  else:
    return None
  return {'model': model, 'ui': ui}

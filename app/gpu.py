"""Lino's GPU. A Modal L4 that scales to zero, shared by every visitor, drawn from a pool of Modal workspaces.

state machine:  offline --wake--> waking --/health ok--> online --idle--> offline
while online a thread pings /health so Modal keeps the container. it stops pinging once no session
holds a lease any more, then Modal scales down after its own 5 minute window (gpu/serve.py).

leases, one per browser session: the hold chosen on the start screen, extended by 10 minutes on every use.
the gpu stays up while any lease is live, so one visitor can never switch it off under another.
shutting down drops your own lease and stops the containers only if nobody else holds one.

pool: every modal workspace has its own monthly credits and its own copy of lino (data/modal_accounts.json).
a wake picks the workspace with the most credits left and keeps it until the gpu sleeps again, so one
conversation never jumps between machines. a workspace that does not come up is skipped for an hour and the
wake moves on to the next one.
"""
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request

import config

AWAKE_SECONDS = 10 * 60
HOLD_CHOICES = (5 * 60, 30 * 60, 60 * 60, 2 * 60 * 60, 3 * 60 * 60)  # 5 min is for testing
WAKE_TIMEOUT = 10 * 60  # per workspace. a cold start takes about 4 minutes
MODAL_APP = 'lino'
ENOUGH_USD = 3.0  # an L4 costs about $0.80 an hour, so $3 covers the longest hold on the start screen


def _pool():
  """workspaces with a token in the environment, in file order"""
  tokens = {hashlib.sha256(i.encode()).hexdigest()[:12]: (i, s) for i, s in config.MODAL_TOKENS}
  spec = json.loads((config.DATA / 'modal_accounts.json').read_text(encoding='utf-8'))['accounts']
  return [a | {'token': tokens[a['token_sha256']]} for a in spec if a['token_sha256'] in tokens]


POOL = _pool()
_failed = {}  # workspace name -> unix time it last failed to boot

_lock = threading.Lock()
STATE = {'status': 'offline', 'wake_started': 0.0, 'cold_start_s': None, 'workspace': POOL[0]['name'] if POOL else None,
         'last_cold_start_s': 220.0}  # measured 2026-10-07: L4 + vLLM 0.28 + DSpark, 222 s
LEASES = {}    # session -> unix time until which it keeps the gpu up
_pending = {}  # session -> hold seconds asked for while booting, counted from the moment it's ready


def online():
  return STATE['status'] == 'online'


def _account(name=None):
  return next((a for a in POOL if a['name'] == (name or STATE['workspace'])), None)


def url():
  a = _account()
  return a['url'] if a else ''


def _rank(rows):
  """workspace names in the order the next wake tries them: enough credits first, most left first, then the rest.
  workspaces that failed to boot in the last hour go last"""
  left = {r['name']: r.get('remaining_usd', 0.0) for r in rows}
  recent = lambda a: time.time() - _failed.get(a['name'], 0) < 3600
  return [a['name'] for a in sorted(POOL, key=lambda a: (recent(a), left.get(a['name'], 0.0) < ENOUGH_USD, -left.get(a['name'], 0.0)))]


def _candidates():
  return [_account(n) for n in _rank(credits().get('accounts', []))]


def _live(now):
  return {sid: t for sid, t in LEASES.items() if t > now}


def _lease(sid, seconds, now):
  LEASES[sid] = max(LEASES.get(sid, 0.0), now + seconds)


def status(sid=''):
  now = time.time()
  s, live = dict(STATE), _live(now)
  up = s['status'] == 'online'
  s['waking_for_s'] = round(now - s['wake_started'], 1) if s['status'] == 'waking' else None
  s['awake_for_s'] = max(0, round(max(live.values(), default=now) - now)) if up else 0  # until the last lease ends
  s['hold_for_s'] = max(0, round(live.get(sid, now) - now)) if up else 0                 # your own lease
  s['hold_s'] = _pending.get(sid, 0)
  s['sessions'] = len(live)
  s['gpu'] = 'NVIDIA L4'
  s['pool'] = len(POOL)
  return s


def _healthy():
  try:
    with urllib.request.urlopen(url() + '/health', timeout=10) as r:
      return r.status == 200
  except Exception:
    return False


def _keepalive():
  while True:
    time.sleep(60)
    with _lock:
      if STATE['status'] != 'online':
        return
      if not _live(time.time()):
        STATE['status'] = 'offline'  # nobody holds it. stop pinging, modal scales down on its own
        return
    if not _healthy():
      with _lock:
        STATE['status'] = 'offline'
      return


def _wait_for_boot():
  for acct in _candidates():
    with _lock:
      if STATE['status'] != 'waking':
        return
      STATE['workspace'] = acct['name']
    if _boot():
      return
    _failed[acct['name']] = time.time()
  with _lock:
    STATE['status'] = 'offline'


def _boot():
  """wait for lino in the current workspace. true once it answers, or when the wake was called off"""
  t0 = time.time()
  while time.time() - t0 < WAKE_TIMEOUT:
    if STATE['status'] != 'waking':
      return True  # shut down while booting
    if _healthy():
      with _lock:
        took, now = round(time.time() - t0, 1), time.time()
        STATE.update(status='online', cold_start_s=took)
        for sid, hold in _pending.items():
          _lease(sid, hold or AWAKE_SECONDS, now)
        _pending.clear()
        if took > 20:  # a container that was still up answers in under a second. that's not a cold start
          STATE['last_cold_start_s'] = took
      threading.Thread(target=_keepalive, daemon=True).start()
      return True
    time.sleep(3)
  return False


def wake(sid, hold_s=0):
  """boot the gpu, or keep it, for this session. hold_s = minimum time online, counted from the moment it's ready"""
  hold_s = min(HOLD_CHOICES, key=lambda c: abs(c - hold_s)) if hold_s else 0
  with _lock:
    if STATE['status'] == 'online':
      _lease(sid, hold_s or AWAKE_SECONDS, time.time())
      return status(sid)
    _pending[sid] = max(_pending.get(sid, 0), hold_s)
    if not POOL or STATE['status'] == 'waking':
      return status(sid)
    STATE.update(status='waking', wake_started=time.time(), cold_start_s=None)
  threading.Thread(target=_wait_for_boot, daemon=True).start()
  return status(sid)


def touch(sid):
  """this session used lino. 10 more minutes"""
  with _lock:
    if STATE['status'] == 'online':
      _lease(sid, AWAKE_SECONDS, time.time())


def mark_offline():
  with _lock:
    STATE['status'] = 'offline'
    LEASES.clear()


# ---- shutting down now instead of waiting for the idle timer
def _modal(*args, check=False):
  """the modal cli, signed in to the workspace lino currently runs in"""
  token = _account()['token']
  env = {**os.environ, 'MODAL_TOKEN_ID': token[0], 'MODAL_TOKEN_SECRET': token[1]}
  return subprocess.run([sys.executable, '-m', 'modal', 'container', *args], capture_output=True, text=True, timeout=40, check=check,
                        env=env).stdout


def _containers():
  return [c['container_id'] for c in json.loads(_modal('list', '--json', check=True) or '[]') if c.get('app_name') == MODAL_APP]


def _sweep():
  """modal reschedules the work of a hard stopped container once. stop the replacement too"""
  for delay in (75, 90):
    time.sleep(delay)
    if STATE['status'] != 'offline':
      return  # someone woke it again
    try:
      for cid in _containers():
        _modal('stop', cid, '--yes')
    except Exception:
      pass


def shutdown(sid, force=False):
  """drop this session's lease. if nobody else holds one (or an admin forces it), stop the containers:
  the deployment stays, the next wake boots a fresh one"""
  with _lock:
    LEASES.pop(sid, None)
    _pending.pop(sid, None)
    others = len(_live(time.time())) + len(_pending)
    if others and not force:
      return {'ok': True, 'stopped': 0, 'shared': others, **status(sid)}
    STATE['status'] = 'offline'
    LEASES.clear()
    _pending.clear()
  try:
    ids = _containers()
    for cid in ids:
      _modal('stop', cid, '--yes', check=True)
    threading.Thread(target=_sweep, daemon=True).start()
    return {'ok': True, 'stopped': len(ids), **status(sid)}
  except Exception as e:
    return {'ok': False, 'error': type(e).__name__, **status(sid)}


_credits = {'at': 0.0, 'data': None}


def _workspace_credits(acct):
  import modal
  summary = modal.Workspace.from_context(client=modal.Client.from_credentials(*acct['token'])).billing.summary()
  used, plan = float(-summary.adjustments.get('Credits', 0)), float(acct.get('plan_usd', 30.0))
  return {'name': acct['name'], 'key': acct['token'][0][:3] + '…' + acct['token'][0][-4:], 'used_usd': round(used, 2), 'plan_usd': plan, 'remaining_usd': round(max(0.0, plan - used), 2),
          'remaining_pct': round(max(0.0, 100 - 100 * used / plan), 1), 'resets': summary.end.date().isoformat()}


def credits():
  """modal credits left, per workspace and in total. cached 10 minutes, the billing api is slow"""
  if _credits['data'] and time.time() - _credits['at'] < 600:
    data = _credits['data']
  else:
    rows = []
    for acct in POOL:
      try:
        rows.append(_workspace_credits(acct))
      except Exception as e:
        rows.append({'name': acct['name'], 'key': acct['token'][0][:3] + '…' + acct['token'][0][-4:], 'error': type(e).__name__})
    ok = [r for r in rows if 'error' not in r]
    if ok:
      used, plan = sum(r['used_usd'] for r in ok), sum(r['plan_usd'] for r in ok)
      data = {'ok': True, 'used_pct': round(100 * used / plan, 1), 'remaining_pct': round(max(0.0, 100 - 100 * used / plan), 1),
              'remaining_usd': round(plan - used, 2), 'plan_usd': plan, 'resets': min(r['resets'] for r in ok), 'accounts': rows}
    else:
      data = {'ok': False, 'error': 'no workspace reachable', 'accounts': rows}
    _credits.update(at=time.time(), data=data)
  up, order = STATE['status'] != 'offline', _rank(data['accounts'])
  for r in data['accounts']:
    r['active'] = up and r['name'] == STATE['workspace']  # lino runs here right now
    r['next'] = not up and bool(order) and r['name'] == order[0]  # the next wake goes here
  return data

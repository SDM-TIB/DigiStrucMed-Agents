"""Lino's GPU. A Modal L4 that scales to zero, shared by every visitor.

state machine:  offline --wake--> waking --/health ok--> online --idle--> offline
while online a thread pings /health so Modal keeps the container. it stops pinging once no session
holds a lease any more, then Modal scales down after its own 5 minute window (gpu/serve.py).

leases, one per browser session: the hold chosen on the start screen, extended by 10 minutes on every use.
the gpu stays up while any lease is live, so one visitor can never switch it off under another.
shutting down drops your own lease and stops the containers only if nobody else holds one.
"""
import json
import subprocess
import sys
import threading
import time
import urllib.request

import config

AWAKE_SECONDS = 10 * 60
HOLD_CHOICES = (5 * 60, 30 * 60, 60 * 60, 2 * 60 * 60, 3 * 60 * 60)  # 5 min is for testing
WAKE_TIMEOUT = 15 * 60
MODAL_APP = 'lino'
PLAN_CREDITS_USD = 30.0  # modal starter plan, $30 of free credits a month

_lock = threading.Lock()
STATE = {'status': 'offline', 'wake_started': 0.0, 'cold_start_s': None,
         'last_cold_start_s': 220.0}  # measured 2026-10-07: L4 + vLLM 0.28 + DSpark, 222 s
LEASES = {}    # session -> unix time until which it keeps the gpu up
_pending = {}  # session -> hold seconds asked for while booting, counted from the moment it's ready


def online():
  return STATE['status'] == 'online'


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
  return s


def _healthy():
  try:
    with urllib.request.urlopen(config.LINO_URL + '/health', timeout=10) as r:
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
  t0 = time.time()
  while time.time() - t0 < WAKE_TIMEOUT:
    if STATE['status'] != 'waking':
      return  # shut down while booting
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
      return
    time.sleep(3)
  with _lock:
    STATE['status'] = 'offline'


def wake(sid, hold_s=0):
  """boot the gpu, or keep it, for this session. hold_s = minimum time online, counted from the moment it's ready"""
  hold_s = min(HOLD_CHOICES, key=lambda c: abs(c - hold_s)) if hold_s else 0
  with _lock:
    if STATE['status'] == 'online':
      _lease(sid, hold_s or AWAKE_SECONDS, time.time())
      return status(sid)
    _pending[sid] = max(_pending.get(sid, 0), hold_s)
    if not config.LINO_URL or STATE['status'] == 'waking':
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
  return subprocess.run([sys.executable, '-m', 'modal', 'container', *args], capture_output=True, text=True, timeout=40, check=check).stdout


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


def credits():
  """share of this month's modal credits left. cached 10 minutes, the billing api is slow"""
  if _credits['data'] and time.time() - _credits['at'] < 600:
    return _credits['data']
  try:
    import modal
    s = modal.Workspace.from_context().billing.summary()
    used = float(-s.adjustments.get('Credits', 0))
    data = {'ok': True, 'used_pct': round(100 * used / PLAN_CREDITS_USD, 1),
            'remaining_pct': round(max(0.0, 100 - 100 * used / PLAN_CREDITS_USD), 1), 'resets': s.end.date().isoformat()}
  except Exception as e:
    data = {'ok': False, 'error': type(e).__name__}
  _credits.update(at=time.time(), data=data)
  return data

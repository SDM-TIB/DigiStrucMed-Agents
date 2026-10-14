"""Usage log behind /dashboard: when, which ip, which browser, which agent, what was asked.

the last 5,000 events live in memory and get written to the private bucket as JSONL every 20 s, so they survive
deploys. ips and questions are personal data. they stay in our bucket and only show behind the admin password.
"""
import collections
import json
import threading
import time

import storage

OBJECT = 'audit/events.jsonl'
EVENTS = collections.deque(maxlen=5000)
VISIT_EVERY = 20 * 60  # one visit per device per 20 minutes, not one per reload
_lock, _dirty, _last_visit = threading.Lock(), threading.Event(), {}


def _load():
  try:
    raw = storage.get(OBJECT)
  except Exception:
    raw = None
  for line in (raw or b'').decode('utf-8').splitlines():
    if line.strip():
      EVENTS.append(json.loads(line))


def _flush_forever():
  while True:
    time.sleep(20)
    if not _dirty.is_set():
      continue
    _dirty.clear()
    with _lock:
      data = '\n'.join(json.dumps(e, ensure_ascii=False) for e in EVENTS).encode('utf-8')
    try:
      storage.put(OBJECT, data, 'application/x-ndjson')
    except Exception:
      _dirty.set()  # try again next round


def client(handler):
  h = handler.headers
  return {'ip': (h.get('X-Forwarded-For') or handler.client_address[0] or '').split(',')[0].strip(),
          'ua': (h.get('User-Agent') or '')[:300], 'device': (h.get('X-Device') or '')[:16],
          'session': handler.session()[:8]}  # signed by the server, unlike the device id the browser sends


def record(handler, kind, **fields):
  e = {'ts': round(time.time(), 1), 'kind': kind, **client(handler), **{k: v for k, v in fields.items() if v not in (None, '')}}
  if kind == 'visit':
    key = (e['ip'], e.get('device'))
    if time.time() - _last_visit.get(key, 0) < VISIT_EVERY:
      return
    _last_visit[key] = time.time()
  with _lock:
    EVENTS.append(e)
  _dirty.set()


def call_started(cid):
  with _lock:
    return next((e for e in reversed(EVENTS) if e.get('kind') == 'call_start' and e.get('conversation_id') == cid), None)


def events():
  with _lock:
    return list(EVENTS)


_load()
threading.Thread(target=_flush_forever, daemon=True).start()

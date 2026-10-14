"""Tavus video calls: start, end, and an estimate of the minutes left.

a call is a Tavus conversation joined over Daily in the browser. every call has a hard timer here so a
forgotten tab never burns the plan, and belongs to the session that started it: only that one can end it.
"""
import datetime as dt
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import config

PALS = json.loads((config.DATA / 'pals.json').read_text())
CALL_NAMES = {'luna': 'Luna evidence call', 'lino': 'Lino fine-tuned model call', 'guido': 'Guido knowledge graph call'}
_timers, _lock = {}, threading.Lock()


def api(method, path, body=None):
  data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
  req = urllib.request.Request('https://tavusapi.com/v2/' + path, data=data, method=method,
                               headers={'x-api-key': config.TAVUS_API_KEY, 'Content-Type': 'application/json'})
  try:
    with urllib.request.urlopen(req, timeout=30) as r:
      raw = r.read()
      return json.loads(raw) if raw else {}
  except urllib.error.HTTPError as e:
    detail = e.read()[:300].decode('utf-8', 'replace').replace(config.TAVUS_API_KEY, '[redacted]')
    raise RuntimeError(f'Tavus HTTP {e.code}: {detail}') from None


def start(agent, owner):
  """new conversation for luna, lino or guido. returns the daily room url with its meeting token"""
  pal = PALS[agent]
  body = {'pal_id': pal['pal_id'], 'face_id': pal['face_id'], 'conversation_name': CALL_NAMES[agent],
          'require_auth': True, 'max_participants': 2, 'policy': 'eu',
          'properties': {'max_call_duration': config.MAX_CALL_SECONDS, 'participant_absent_timeout': 120,
                         'participant_left_timeout': 15, 'enable_recording': False, 'languages': config.LANGUAGES}}
  if agent == 'luna':  # luna also gets a curated set of evidence pages as call context
    body['conversational_context'] = (config.DATA / 'luna_call_context.json').read_text(encoding='utf-8')
  result = api('POST', 'conversations', body)
  cid = result['conversation_id']
  timer = threading.Timer(config.MAX_CALL_SECONDS + 5, lambda: end(cid))
  timer.daemon = True
  with _lock:
    _timers[cid] = (timer, owner)
  timer.start()
  url = result['conversation_url'] + '?t=' + urllib.parse.quote(result.get('meeting_token', ''))
  return {'url': url, 'conversation_id': cid, 'max_seconds': config.MAX_CALL_SECONDS}


def end(cid, owner=None):
  """owner None is the hard timer itself"""
  with _lock:
    timer, started_by = _timers.get(cid, (None, None))
    if timer is None or owner not in (None, started_by):
      return False  # not ours, someone else's, or already ended
    del _timers[cid]
  timer.cancel()
  api('POST', f'conversations/{cid}/end', {})
  return True


_usage = {'at': 0.0, 'data': None}


def usage():
  """tavus has no balance endpoint. sum the call durations since the period start, calibrated by a fixed offset"""
  if _usage['data'] and time.time() - _usage['at'] < 60:
    return _usage['data']
  now = dt.datetime.now(dt.timezone.utc)
  start = (dt.datetime.fromisoformat(config.TAVUS_PERIOD_START).replace(tzinfo=dt.timezone.utc) if config.TAVUS_PERIOD_START
           else now.replace(day=1, hour=0, minute=0, second=0, microsecond=0))
  parse = lambda t: dt.datetime.fromisoformat(t.replace('Z', '+00:00'))
  seconds, calls = 0.0, 0
  try:
    for page in range(1, 20):
      rows = api('GET', f'conversations?limit=100&page={page}').get('data') or []
      for c in rows:
        t0 = parse(c['created_at'])
        if t0 >= start:
          seconds += max(0.0, ((parse(c['updated_at']) if c.get('status') == 'ended' else now) - t0).total_seconds())
          calls += 1
      if len(rows) < 100:
        break
    used = max(0.0, seconds / 60 + config.TAVUS_USED_OFFSET_MIN)
    data = {'ok': True, 'used_min': round(used, 1), 'plan_min': config.TAVUS_PLAN_MINUTES,
            'remaining_min': round(max(0.0, config.TAVUS_PLAN_MINUTES - used), 1), 'calls': calls,
            'since': start.date().isoformat(), 'estimate': True}
  except Exception as e:
    data = {'ok': False, 'error': type(e).__name__}
  _usage.update(at=time.time(), data=data)
  return data

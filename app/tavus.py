"""Tavus video calls over several Tavus accounts: start, end, and an estimate of the minutes left.

a call is a Tavus conversation joined over Daily in the browser. every call has a hard timer here so a
forgotten tab never burns the plan, and belongs to the session that started it: only that one can end it.

each account has its own plan minutes, renewal day and copies of luna, lino and guido (data/tavus_accounts.json,
written by tavus/sync.py). the keys themselves are secrets: TAVUS_API_KEY plus TAVUS_EXTRA_KEYS. the file only
holds a fingerprint of each key. a call goes to the account whose minutes expire soonest and still cover a full
call, so no account's minutes go to waste. if tavus refuses, the next account takes it.
"""
import calendar
import datetime as dt
import hashlib
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import config

CALL_NAMES = {'luna': 'Luna evidence call', 'lino': 'Lino fine-tuned model call', 'guido': 'Guido knowledge graph call'}
PALS = tuple(CALL_NAMES)
_timers, _lock = {}, threading.Lock()


def fingerprint(key):
  return hashlib.sha256(key.encode()).hexdigest()[:12]


def _accounts():
  """accounts that have a key in the environment and agents in the file, in file order"""
  keys = {fingerprint(k): k for k in config.TAVUS_KEYS}
  spec = json.loads((config.DATA / 'tavus_accounts.json').read_text(encoding='utf-8'))['accounts']
  return [a | {'key': keys[a['key_sha256']]} for a in spec if a['key_sha256'] in keys and set(PALS) <= set(a.get('pals', {}))]


ACCOUNTS = _accounts()


def api(method, path, body, key):
  data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
  req = urllib.request.Request('https://tavusapi.com/v2/' + path, data=data, method=method,
                               headers={'x-api-key': key, 'Content-Type': 'application/json'})
  try:
    with urllib.request.urlopen(req, timeout=30) as r:
      raw = r.read()
      return json.loads(raw) if raw else {}
  except urllib.error.HTTPError as e:
    detail = e.read()[:300].decode('utf-8', 'replace')
    for k in config.TAVUS_KEYS:
      detail = detail.replace(k, '[redacted]')
    raise RuntimeError(f'Tavus HTTP {e.code}: {detail}') from None


def _rank(rows):
  """account names in the order the next call tries them. first those whose minutes cover a full call, the one
  renewing soonest first (its minutes are lost at renewal), then the rest with anything left, most minutes first.
  none left: all of them anyway, the estimate can be off"""
  need = config.MAX_CALL_SECONDS / 60
  rows = [r for r in rows if 'error' not in r]
  full = sorted((r for r in rows if r['remaining_min'] >= need), key=lambda r: (r['renews'], -r['remaining_min']))
  some = sorted((r for r in rows if 0.5 <= r['remaining_min'] < need), key=lambda r: -r['remaining_min'])
  return [r['name'] for r in full + some or sorted(rows, key=lambda r: -r['remaining_min'])]


def _order():
  names = _rank(usage().get('accounts', []))
  by_name = {a['name']: a for a in ACCOUNTS}
  return [by_name[n] for n in names] + [a for a in ACCOUNTS if a['name'] not in names]


def start(agent, owner):
  """new conversation for luna, lino or guido. returns the daily room url with its meeting token"""
  if not ACCOUNTS:
    raise RuntimeError('No Tavus account configured')
  errors = []
  for acct in _order():
    pal = acct['pals'][agent]
    body = {'pal_id': pal['pal_id'], 'face_id': pal['face_id'], 'conversation_name': CALL_NAMES[agent],
            'require_auth': True, 'max_participants': 2, 'policy': 'eu',
            'properties': {'max_call_duration': config.MAX_CALL_SECONDS, 'participant_absent_timeout': 120,
                           'participant_left_timeout': 15, 'enable_recording': False, 'languages': config.LANGUAGES}}
    if agent == 'luna':  # luna also gets a curated set of evidence pages as call context
      body['conversational_context'] = (config.DATA / 'luna_call_context.json').read_text(encoding='utf-8')
    try:
      result = api('POST', 'conversations', body, key=acct['key'])
      break
    except RuntimeError as e:  # out of minutes, too many calls at once: next account
      errors.append(f'{acct["name"]}: {e}')
  else:
    raise RuntimeError('No Tavus account could start the call. ' + ' | '.join(errors))
  _usage['at'] = 0  # the next estimate counts this call
  cid = result['conversation_id']
  timer = threading.Timer(config.MAX_CALL_SECONDS + 5, lambda: end(cid))
  timer.daemon = True
  with _lock:
    _timers[cid] = (timer, owner, acct['key'])
  timer.start()
  url = result['conversation_url'] + '?t=' + urllib.parse.quote(result.get('meeting_token', ''))
  return {'url': url, 'conversation_id': cid, 'max_seconds': config.MAX_CALL_SECONDS}


def end(cid, owner=None):
  """owner None is the hard timer itself"""
  with _lock:
    timer, started_by, key = _timers.get(cid, (None, None, None))
    if timer is None or owner not in (None, started_by):
      return False  # not ours, someone else's, or already ended
    del _timers[cid]
  timer.cancel()
  api('POST', f'conversations/{cid}/end', {}, key=key)
  return True


def _period(first, today):
  """the plan renews monthly on the day of the first period. returns this period's start and the next renewal"""
  def shift(months):
    y, m = divmod(first.year * 12 + first.month - 1 + months, 12)
    return dt.date(y, m + 1, min(first.day, calendar.monthrange(y, m + 1)[1]))
  n = max(0, (today.year - first.year) * 12 + today.month - first.month)
  while n and shift(n) > today:
    n -= 1
  return shift(n), shift(n + 1)


def _account_usage(acct, now):
  start, renews = _period(dt.date.fromisoformat(acct['period_start']), now.date())
  start_at = dt.datetime.combine(start, dt.time(), dt.timezone.utc)
  parse = lambda t: dt.datetime.fromisoformat(t.replace('Z', '+00:00'))
  seconds, calls = 0.0, 0
  for page in range(1, 20):
    rows = api('GET', f'conversations?limit=100&page={page}', None, acct['key']).get('data') or []
    for c in rows:
      t0 = parse(c['created_at'])
      if t0 >= start_at:
        seconds += max(0.0, ((parse(c['updated_at']) if c.get('status') == 'ended' else now) - t0).total_seconds())
        calls += 1
    if len(rows) < 100:
      break
  # the offset calibrates against the tavus dashboard and only holds for the period it was measured in
  offset = acct.get('used_offset_min', 0.0) if acct.get('offset_period') == start.isoformat() else 0.0
  used = max(0.0, seconds / 60 + offset)
  plan = float(acct['plan_minutes'])
  return {'name': acct['name'], 'key': '…' + acct['key'][-4:], 'used_min': round(used, 1), 'plan_min': plan,
          'remaining_min': round(max(0.0, plan - used), 1),
          'calls': calls, 'since': start.isoformat(), 'renews': renews.isoformat()}


_usage = {'at': 0.0, 'data': None}


def usage():
  """tavus has no balance endpoint. per account: sum the call durations since its period start. totals on top"""
  if _usage['data'] and time.time() - _usage['at'] < 60:
    return _usage['data']
  now = dt.datetime.now(dt.timezone.utc)
  rows = []
  for acct in ACCOUNTS:
    try:
      rows.append(_account_usage(acct, now))
    except Exception as e:
      rows.append({'name': acct['name'], 'key': '…' + acct['key'][-4:], 'error': type(e).__name__})
  ok = [r for r in rows if 'error' not in r]
  order = _rank(rows)
  for r in rows:
    r['next'] = bool(order) and r['name'] == order[0]
  if not ok:
    data = {'ok': False, 'error': 'no account reachable', 'accounts': rows}
  else:
    data = {'ok': True, 'used_min': round(sum(r['used_min'] for r in ok), 1), 'plan_min': sum(r['plan_min'] for r in ok),
            'remaining_min': round(sum(r['remaining_min'] for r in ok), 1), 'calls': sum(r['calls'] for r in ok),
            'since': min(r['since'] for r in ok), 'next_renewal': min(r['renews'] for r in ok),
            'accounts': rows, 'estimate': True}
  _usage.update(at=time.time(), data=data)
  return data

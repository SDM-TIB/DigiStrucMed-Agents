"""Push the agents in tavus/pals/*.json and their tools in tavus/tools/*.json to every Tavus account.

  python tavus/sync.py            # update what exists, create what doesn't, in every account
  python tavus/sync.py --export   # pull the live config of the first account back into the json files

keys come from TAVUS_API_KEY and TAVUS_EXTRA_KEYS (comma separated), in the environment or the repo's .env.
app/data/tavus_accounts.json is the file the server reads: per account a fingerprint of its key, its plan minutes,
the first day of its plan period (it renews monthly on that day) and the ids of luna, lino and guido there.
a key without an entry gets one with plan 60 minutes starting today (deploy/deploy_new_tavus.py sets both).
faces and voices are tavus system ones, the same in every account. luna's knowledge base documents belong to one
account, so their ids live in that account's entry (documents), not in pals/luna.json.
"""
import datetime as dt
import hashlib
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent
ACCOUNTS = ROOT.parent / 'app' / 'data' / 'tavus_accounts.json'
PAL_FIELDS = ('pal_name', 'system_prompt', 'greeting', 'disclosure_type', 'verbal_disclosure', 'languages', 'default_face_id')
TOOL_FIELDS = ('name', 'description', 'parameters', 'delivery', 'on_call', 'on_resolve')


def keys():
  env = dict(os.environ)
  dotenv = ROOT.parent / '.env'
  if dotenv.exists():
    for line in dotenv.read_text(encoding='utf-8').splitlines():
      if '=' in line and not line.lstrip().startswith('#'):
        k, v = line.split('=', 1)
        env.setdefault(k.strip(), v.strip().strip('"'))
  return [k.strip() for k in [env.get('TAVUS_API_KEY', ''), *env.get('TAVUS_EXTRA_KEYS', '').split(',')] if k.strip()]


def fingerprint(key):
  return hashlib.sha256(key.encode()).hexdigest()[:12]


def api(key, method, path, body=None):
  req = urllib.request.Request('https://tavusapi.com/v2/' + path, data=json.dumps(body).encode() if body is not None else None,
                               method=method, headers={'x-api-key': key, 'Content-Type': 'application/json'})
  try:
    with urllib.request.urlopen(req, timeout=30) as r:
      raw = r.read()
      return json.loads(raw) if raw else {}
  except urllib.error.HTTPError as e:
    if e.code == 304:
      return {}  # nothing changed
    raise SystemExit(f'{method} {path}: HTTP {e.code} {e.read()[:300].decode(errors="replace")}')


def sync_tools(key):
  existing = {t['name']: t['tool_id'] for t in api(key, 'GET', 'tools?limit=100').get('data', []) if not t.get('is_system_tool')}
  ids = {}
  for path in sorted((ROOT / 'tools').glob('*.json')):
    spec = json.loads(path.read_text(encoding='utf-8'))
    if spec['name'] in existing:
      api(key, 'PATCH', 'tools/' + existing[spec['name']], spec)
      ids[path.stem] = existing[spec['name']]
    else:
      ids[path.stem] = api(key, 'POST', 'tools', spec)['tool_id']
  return ids


def sync_pal(key, acct, name, spec, tool_ids):
  pal_id = acct['pals'].get(name, {}).get('pal_id')
  fields = {k: spec[k] for k in PAL_FIELDS if k in spec}
  if spec.get('knowledge_base'):
    fields['document_ids'] = acct.get('documents', [])
  if pal_id:
    live = api(key, 'GET', 'pals/' + pal_id)
    ops = [{'op': 'replace' if k in live else 'add', 'path': '/' + k, 'value': v} for k, v in fields.items()]
    if spec.get('voice_id'):
      ops.append({'op': 'replace', 'path': '/layers/tts/voice_id', 'value': spec['voice_id']})
    api(key, 'PATCH', 'pals/' + pal_id, ops)
  else:
    if spec.get('voice_id'):
      fields['layers'] = {'tts': {'voice_id': spec['voice_id']}}
    pal_id = api(key, 'POST', 'pals', fields)['pal_id']
  if tool_ids:
    api(key, 'POST', f'pals/{pal_id}/tools', {'tool_ids': tool_ids})
  acct['pals'][name] = {'pal_id': pal_id, 'face_id': spec['default_face_id']}
  print(f'  {name:6s} {pal_id}  tools: {", ".join(spec.get("tools", [])) or "-"}')


def export(key, acct):
  for name, ref in acct['pals'].items():
    live = api(key, 'GET', 'pals/' + ref['pal_id'])
    out = {k: live[k] for k in PAL_FIELDS if live.get(k) not in (None, '', [])}
    if live.get('document_ids'):
      out['knowledge_base'] = True
      acct['documents'] = live['document_ids']
    if live.get('layers', {}).get('tts', {}).get('voice_id'):
      out['voice_id'] = live['layers']['tts']['voice_id']
    out['tools'] = []
    for tid in live.get('tool_ids', []):
      t = api(key, 'GET', 'tools/' + tid)
      (ROOT / 'tools' / f'{t["name"]}.json').write_text(json.dumps({k: t[k] for k in TOOL_FIELDS}, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
      out['tools'].append(t['name'])
    (ROOT / 'pals' / f'{name}.json').write_text(json.dumps(out, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print('exported', name)


def load(new_plan=60.0, new_since=None):
  """the accounts file, with an entry for every key that has none yet. returns (data, [(key, account)])"""
  data = json.loads(ACCOUNTS.read_text(encoding='utf-8'))
  by_fp = {a['key_sha256']: a for a in data['accounts']}
  todo = []
  for key in keys():
    acct = by_fp.get(fingerprint(key))
    if acct is None:
      acct = {'name': f'tavus-{len(data["accounts"]) + 1}', 'key_sha256': fingerprint(key), 'plan_minutes': new_plan,
              'period_start': new_since or dt.date.today().isoformat(), 'documents': [], 'pals': {}}
      data['accounts'].append(acct)
      by_fp[acct['key_sha256']] = acct
      print(f'new account {acct["name"]}: {new_plan:g} plan minutes from {acct["period_start"]}')
    todo.append((key, acct))
  return data, todo


def save(data):
  ACCOUNTS.write_bytes((json.dumps(data, indent=2, ensure_ascii=False) + '\n').encode('utf-8'))


def sync_account(key, acct):
  specs = {p.stem: json.loads(p.read_text(encoding='utf-8')) for p in sorted((ROOT / 'pals').glob('*.json'))}
  print(acct['name'])
  tool_ids = sync_tools(key)
  for name, spec in specs.items():
    sync_pal(key, acct, name, spec, [tool_ids[t] for t in spec.get('tools', [])])


if __name__ == '__main__':
  data, todo = load()
  if '--export' in sys.argv:
    export(*todo[0])
  else:
    for key, acct in todo:
      sync_account(key, acct)
  save(data)

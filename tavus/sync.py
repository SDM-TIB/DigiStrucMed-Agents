"""Push the agents in tavus/pals/*.json and their tools in tavus/tools/*.json to Tavus.

  TAVUS_API_KEY=... python tavus/sync.py            # update what exists, create what doesn't
  TAVUS_API_KEY=... python tavus/sync.py --export   # pull the live config back into the json files

ids live in app/data/pals.json, the file the server reads. a new account gets new ids written there.
faces (the video avatars) are picked in the Tavus dashboard. a pal's default_face_id is the one we used.
luna's document_ids point at the knowledge base in our account. on a new account remove them or upload your own.
"""
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent
IDS = ROOT.parent / 'app' / 'data' / 'pals.json'
KEY = os.environ['TAVUS_API_KEY']
PAL_FIELDS = ('pal_name', 'system_prompt', 'greeting', 'disclosure_type', 'verbal_disclosure', 'languages', 'default_face_id', 'document_ids')
TOOL_FIELDS = ('name', 'description', 'parameters', 'delivery', 'on_call', 'on_resolve')


def api(method, path, body=None):
  req = urllib.request.Request('https://tavusapi.com/v2/' + path, data=json.dumps(body).encode() if body is not None else None,
                               method=method, headers={'x-api-key': KEY, 'Content-Type': 'application/json'})
  try:
    with urllib.request.urlopen(req, timeout=30) as r:
      raw = r.read()
      return json.loads(raw) if raw else {}
  except urllib.error.HTTPError as e:
    if e.code == 304:
      return {}  # nothing changed
    raise SystemExit(f'{method} {path}: HTTP {e.code} {e.read()[:300].decode(errors="replace")}')


def tools_by_name():
  return {t['name']: t['tool_id'] for t in api('GET', 'tools?limit=100').get('data', []) if not t.get('is_system_tool')}


def sync_tool(spec, existing):
  if spec['name'] in existing:
    api('PATCH', 'tools/' + existing[spec['name']], spec)
    return existing[spec['name']]
  return api('POST', 'tools', spec)['tool_id']


def sync_pal(name, spec, ids, tool_ids):
  pal_id = ids.get(name, {}).get('pal_id')
  fields = {k: spec[k] for k in PAL_FIELDS if k in spec}
  if spec.get('voice_id'):
    fields['layers'] = {'tts': {'voice_id': spec['voice_id']}}
  if pal_id:
    live = api('GET', 'pals/' + pal_id)
    ops = [{'op': 'replace' if k in live else 'add', 'path': '/' + k, 'value': v} for k, v in fields.items() if k != 'layers']
    if spec.get('voice_id'):
      ops.append({'op': 'replace', 'path': '/layers/tts/voice_id', 'value': spec['voice_id']})
    api('PATCH', 'pals/' + pal_id, ops)
  else:
    pal_id = api('POST', 'pals', fields)['pal_id']
  if tool_ids:
    api('POST', f'pals/{pal_id}/tools', {'tool_ids': tool_ids})
  ids[name] = {'pal_id': pal_id, 'face_id': spec['default_face_id']}
  print(f'{name:6s} {pal_id}  tools: {", ".join(spec.get("tools", [])) or "-"}')


def export(ids):
  for name, ref in ids.items():
    live = api('GET', 'pals/' + ref['pal_id'])
    out = {k: live[k] for k in PAL_FIELDS if live.get(k) not in (None, '', [])}
    if live.get('layers', {}).get('tts', {}).get('voice_id'):
      out['voice_id'] = live['layers']['tts']['voice_id']
    out['tools'] = []
    for tid in live.get('tool_ids', []):
      t = api('GET', 'tools/' + tid)
      (ROOT / 'tools' / f'{t["name"]}.json').write_text(json.dumps({k: t[k] for k in TOOL_FIELDS}, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
      out['tools'].append(t['name'])
    (ROOT / 'pals' / f'{name}.json').write_text(json.dumps(out, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print('exported', name)


if __name__ == '__main__':
  ids = json.loads(IDS.read_text()) if IDS.exists() else {}
  if '--export' in sys.argv:
    export(ids)
    raise SystemExit
  existing = tools_by_name()
  tool_ids = {p.stem: sync_tool(json.loads(p.read_text(encoding='utf-8')), existing) for p in sorted((ROOT / 'tools').glob('*.json'))}
  for path in sorted((ROOT / 'pals').glob('*.json')):
    spec = json.loads(path.read_text(encoding='utf-8'))
    sync_pal(path.stem, spec, ids, [tool_ids[t] for t in spec.get('tools', [])])
  IDS.write_text(json.dumps(ids, indent=2) + '\n')

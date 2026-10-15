"""Measure app/triage.py on the labelled messages in tests/triage_cases.json, with the model the app would use.

  python tests/triage_eval.py

prints every case with its probabilities, then per banner how many it should show and how many it missed or showed
needlessly. reads the keys from .env like the app. a handful of hand-written cases, not a validation on patient data.
"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
import triage  # noqa: E402

cases = json.loads((ROOT / 'tests' / 'triage_cases.json').read_text(encoding='utf-8'))['cases']
rows, errors = [], 0
for c in cases:
  r = triage.assess(c['text'])
  if 'error' in r:
    errors += 1
    print(f'ERROR {r["error"]:20s} {c["text"]}')
    continue
  ok = r['level'] == c['want']
  rows.append((c['want'], r['level']))
  s = r['scores']
  print(f'{"ok " if ok else "BAD"} want {c["want"]:9s} got {r["level"]:9s} e={s.get("emergency_now", 0):.2f} '
        f'w={s.get("worsening", 0):.2f} h={s.get("self_harm", 0):.2f} {r.get("ms", 0):4d}ms  {c["text"][:70]}')

print(f'\nmodel {r.get("model")}, {len(rows)} cases, {errors} errors, {sum(w == g for w, g in rows)} correct')
for lv in ('emergency', 'urgent', 'support'):
  should = [g for w, g in rows if w == lv]
  extra = sum(1 for w, g in rows if g == lv and w != lv)
  print(f'{lv:9s} shown {sum(g == lv for g in should)}/{len(should)} when it should, {extra} times when it should not')

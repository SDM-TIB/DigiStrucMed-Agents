"""Measure app/check.py on the labelled statements in tests/check_cases.json, three runs each.

  python tests/check_eval.py

needs the LLM (LLM_API_KEY, locally your gcloud login for vertex) and the cloudflare token from .env.
a handful of hand-written cases: it shows whether retrieval and judgment work together, it is not a validation.
"""
import collections
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
import check  # noqa: E402

RUNS = 3
cases = json.loads((ROOT / 'tests' / 'check_cases.json').read_text(encoding='utf-8'))['cases']
correct = 0
for c in cases:
  got = collections.Counter()
  for _ in range(RUNS):
    claims = check.check(c['question'], c['answer']).get('claims', [])
    got[claims[0]['verdict'] if claims else 'no statement'] += 1
  ok = got.get(c['want'], 0) == RUNS
  correct += ok
  print(f'{"ok " if ok else "BAD"} want {c["want"]:13s} got {dict(got)}  {c["answer"][:70]}')
print(f'\n{correct}/{len(cases)} cases right in all {RUNS} runs')

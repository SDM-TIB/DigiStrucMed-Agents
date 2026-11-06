"""Checks the Graph Walker event translation on a tiny constructed layout (no graph server, no models).

  python tests/walker_check.py
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'app'))
import walkerchat  # noqa: E402

LAYOUT = {'docs': [{'title': 'A'}, {'title': 'B'}], 'topics': [{'doc': 0, 'name': 't0'}, {'doc': 1, 'name': 't1'}],
          'keys': ['p#prop0', 'p#prop1'], 'topic': [0, 1], 'concepts': [{'cui': 'C1', 'name': 'Heart failure'}], 'pc': [[0], []]}
S = {'layout': LAYOUT, 'doc_i': {'urn:A': 0, 'urn:B': 1}, 'topic_i': {(0, 't0'): 0, (1, 't1'): 1}, 'key_i': {'p#prop0': 0, 'p#prop1': 1},
     'by_key': {'p#prop0': {'text': 'x', 'kind': 'recommendation', 'cls': '1', 'lvl': 'A', 'population': '', 'quote': 'x', 'number': 3},
                'p#prop1': {'text': 'y', 'kind': 'finding', 'cls': '', 'lvl': '', 'population': '', 'quote': 'y', 'number': 4}}}
failed = []


def check(name, cond):
  print(('ok   ' if cond else 'FAIL ') + name)
  if not cond:
    failed.append(name)


g = walkerchat._translate({'t': 'scores', 'level': 'documents', 'scorer': 's', 'probs': {'urn:A': .9, 'urn:B': .1, 'urn:X': .5}}, S)
check('guidelines: known documents only, as indices', g['level'] == 'guidelines' and g['items'] == [[0, .9], [1, .1]])
t = walkerchat._translate({'t': 'scores', 'level': 'topics', 'scorer': 's', 'probs': {'urn:B|t1': .7}}, S)
check('topics: topic index and its guideline', t['items'] == [[1, .7]] and t['doc'] == 1)
r = walkerchat._translate({'t': 'scores', 'level': 'pieces', 'scorer': 's', 'probs': {'urn:mhh:rec/1': .8}}, S)
check('recommendation nodes of the 28.10 graph are their own level', r['level'] == 'recommendations' and r['items'] == [])
p = walkerchat._translate({'t': 'scores', 'level': 'pieces', 'scorer': 's', 'probs': {'p#prop1': .4, 'p#prop0': .9}}, S)
check('statements: sorted by score, indices of the layout', p['level'] == 'statements' and p['items'] == [[0, .9], [1, .4]])
k = walkerchat._translate({'t': 'taken', 'candidates': 2, 'keys': ['p#prop0'], 'scores': {'p#prop0': .9}}, S)
card = k['items'][0]
check('taken: card with UMLS concept, guideline and topic', card['concepts'] == [{'ci': 0, 'cui': 'C1', 'name': 'Heart failure', 'de': ''}] and card['doc'] == 0 and card['topic'] == 0)
check('unknown event types are dropped', walkerchat._translate({'t': 'nothing'}, S) is None)
sys.exit(1 if failed else 0)

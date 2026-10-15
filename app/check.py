"""Lino's guideline check: every medical statement in his answer, looked up in the guidelines and judged.

lino answers from memory, without sources. this checks him afterwards and changes nothing he said:
  1. the llm splits his answer into statements, each copied word for word from the answer (verified, else dropped),
     with english search terms for each
  2. bm25 finds the three best guideline pages per statement; the passages are verbatim windows of those pages
  3. a System One model (clef-flash, cloudflare's Jev) answers one choice per statement: does the closest guideline
     text support it, contradict it, or not address it? and which passage it rests on
the pattern is TypeSafe's citation check (docs.typesafe.ai/cookbooks/citation_check). a verdict below MIN_SURE shows
as unclear. what the patient sees is lino's own sentence, the verdict, and the guideline text verbatim with its page.
"""
import concurrent.futures
import json
import re
import urllib.request

import config
import corpus
import llm

MAX_CLAIMS = 6
PAGES = 3
MIN_SURE = 0.6
URL = 'https://api.cloudflare.com/client/v4/accounts/{}/ai/run/@cf/cloudflare/clef-flash'

SPLIT = (
  "Split the answer below into its medical statements: claims about a condition, a treatment, a drug, a dose, a "
  "test, a risk or a recommendation. Skip greetings, questions, advice to see a doctor, and anything without medical "
  "content. For each statement copy one sentence or clause from the answer exactly as written, character for "
  "character, in its language. Give English search terms for looking it up in clinical guidelines.\n"
  'Return JSON only: {"claims": [{"text": "exact words from the answer", "query": "english search terms"}]}. '
  f"At most {MAX_CLAIMS} statements, the most important first. None: {{\"claims\": []}}.")

QUESTIONS = {
  'verdict': {
    'type': 'choice',
    'instructions': 'Compare `claim` with the guideline passages in `passages`. Does the guideline text support the claim, '
                    'contradict it, or not address it? Judge only from the passages, not from general knowledge.',
    'criteria': {
      'supported': 'A passage states the same thing, or clearly implies it, for the same kind of patient.',
      'contradicted': 'A passage says the opposite, for example that it is not recommended, harmful or a different dose.',
      'not_addressed': 'The passages are about something else or too general to judge the claim either way.'}},
  'passage': {
    'type': 'choice',
    'instructions': 'Which passage in `passages` is the most relevant to `claim`?',
    'criteria': {f'p{n}': f'passage {n}' for n in range(1, PAGES + 1)}},
}


def _claims(question, answer):
  raw, _ = llm.complete([{'role': 'system', 'content': SPLIT},
                         {'role': 'user', 'content': f'Question: {question[:500]}\n\nAnswer:\n{answer[:3000]}'}],
                        max_tokens=700, response_format={'type': 'json_object'})
  out = json.loads(raw[raw.index('{'):raw.rindex('}') + 1]).get('claims') or []
  squash = lambda t: re.sub(r'\s+', ' ', t).strip()
  plain = squash(answer)
  claims = []
  for c in out:
    text = squash(str(c.get('text', '')))
    if len(text) >= 20 and text in plain and text not in [x['text'] for x in claims]:  # word for word from lino, or dropped
      claims.append({'text': text, 'query': str(c.get('query') or text)[:200]})
  return claims[:MAX_CLAIMS]


def _judge(claim):
  hits = corpus.bm25(claim['query'], k=PAGES)
  if not hits:
    return {**claim, 'verdict': 'not_addressed', 'p': 1.0, 'source': None}
  passages = [{'n': n, 'source': f"{corpus.PAGES[i].get('ref') or corpus.PAGES[i]['title']}, page {corpus.PAGES[i]['page']}",
               'text': corpus.snippet(corpus.PAGES[i]['plain'], claim['query'], 700)} for n, i in enumerate(hits, 1)]
  questions = dict(QUESTIONS, passage={**QUESTIONS['passage'], 'criteria': {f'p{p["n"]}': p['source'] for p in passages}})
  body = {'model': 'clef-flash', 'state': {'claim': claim['text'], 'passages': passages}, 'questions': questions}
  req = urllib.request.Request(URL.format(config.CF_ACCOUNT_ID), data=json.dumps(body, ensure_ascii=False).encode(), method='POST',
                               headers={'Authorization': 'Bearer ' + config.CF_API_TOKEN, 'Content-Type': 'application/json'})
  with urllib.request.urlopen(req, timeout=25) as r:
    answers = json.load(r).get('result', {}).get('answers', {})
  v = answers.get('verdict', {})
  verdict, p = v.get('choice', 'not_addressed'), float((v.get('probabilities') or {}).get(v.get('choice'), 0.0))
  pick = int(str(answers.get('passage', {}).get('choice', 'p1'))[1:] or 1)
  page = corpus.PAGES[hits[min(pick, len(hits)) - 1]]
  source = {'page_id': page['id'], 'ref': page.get('ref'), 'title': page['title'], 'page': page['page'],
            'text': passages[min(pick, len(hits)) - 1]['text']}
  return {'text': claim['text'], 'verdict': verdict if p >= MIN_SURE else 'unclear', 'p': round(p, 2),
          'source': source if verdict != 'not_addressed' else None}


def check(question, answer):
  answer = str(answer or '')
  if not (config.LLM_API_KEY and config.CF_ACCOUNT_ID and config.CF_API_TOKEN) or len(answer) < 40 or answer.lstrip()[:1] in '{[':
    return {'claims': []}  # nothing to check, or a storyboard
  try:
    claims = _claims(str(question or ''), answer)
  except Exception as e:
    return {'error': type(e).__name__}
  with concurrent.futures.ThreadPoolExecutor(MAX_CLAIMS) as pool:
    results = list(pool.map(lambda c: _safe(_judge, c), claims))
  return {'claims': [r for r in results if r]}


def _safe(fn, claim):
  try:
    return fn(claim)
  except Exception:
    return {'text': claim['text'], 'verdict': 'unclear', 'p': 0.0, 'source': None}

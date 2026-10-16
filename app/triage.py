"""Triage: does what the patient just said call for action outside the chat, right now or today?

a System One model reads the latest message with the recent conversation and answers three yes/no questions
(nouls) with a probability each. it generates no text: the banners it can trigger are fixed in static/js/care.js,
the model only decides which one, if any. one noul per condition, phrased so that a high value means yes, as the
TypeSafe docs recommend (docs.typesafe.ai/primitives/noul, cookbooks/llm_guardrails).

the model: Cloudflare's clef-flash on Workers AI. a missed emergency costs more than a needless banner, so the emergency threshold sits below 0.5.
the thresholds are starting points measured on tests/triage_cases.json (python tests/triage_eval.py), not on
real patient traffic.
"""
import json
import time
import urllib.error
import urllib.request

import config

CRITERIA_NOW = {
  'true': 'They describe such symptoms in themselves (or someone with them) now or within the last hours, in any language.',
  'false': 'A general, hypothetical or educational question, something in the past that is over, other people in general, '
           'or only mild or long-standing symptoms.'}

QUESTIONS = {
  'emergency_now': {
    'type': 'noul',
    'instructions': 'Does `latest_message` describe symptoms happening right now, or that started within the last hours, that can be '
                    'a medical emergency: chest pain or pressure, severe or sudden breathlessness, fainting or nearly fainting, a very '
                    'fast or irregular heartbeat with dizziness, coughing up pink or frothy sputum, or signs of a stroke such as a '
                    'drooping face, arm weakness or slurred speech? Use `recent_conversation` only as context.',
    'criteria': CRITERIA_NOW},
  'worsening': {
    'type': 'noul',
    'instructions': 'Does `latest_message` describe signs that the person\'s heart failure has been getting worse over the last days, '
                    'short of an acute emergency: weight gain of about 2 kg or more within a few days, more swelling of the legs or '
                    'belly, more breathlessness during daily activities or when lying flat, or needing more pillows to sleep? '
                    'Use `recent_conversation` only as context.',
    'criteria': {'true': 'They report such a change in their own condition over recent days.',
                 'false': 'A general question about these signs, a stable condition, or an acute emergency right now.'}},
  'self_harm': {
    'type': 'noul',
    'instructions': 'Does `latest_message` suggest that the person may be thinking about harming themselves or ending their life? '
                    'Use `recent_conversation` only as context.'},
}

# probability at or above which a banner shows. a missed emergency costs more than a needless banner, so 0.35.
# worsening over days also raises the emergency probability a little (0.43 to 0.50 on the test cases, real
# emergencies 0.79 and up): when worsening is clear and emergency only moderate, the urgent banner shows, and it
# still says to call 112 if things get suddenly worse
THRESHOLDS = {'emergency_now': 0.35, 'self_harm': 0.35, 'worsening': 0.5}
CLEAR_EMERGENCY = 0.6


def _level(s):
  e, h, w = (s.get(q, 0.0) for q in ('emergency_now', 'self_harm', 'worsening'))
  if e >= CLEAR_EMERGENCY or (e >= THRESHOLDS['emergency_now'] and w < THRESHOLDS['worsening']):
    return 'emergency'
  if h >= THRESHOLDS['self_harm']:
    return 'support'
  if w >= THRESHOLDS['worsening']:
    return 'urgent'
  return 'none'

CF_URL = 'https://api.cloudflare.com/client/v4/accounts/{}/ai/run/@cf/cloudflare/clef-flash'


def _ask(state):
  """one request, three questions in parallel on the model side. returns (model, {question: noul})"""
  if not (config.CF_ACCOUNT_ID and config.CF_API_TOKEN):
    raise RuntimeError('no triage model configured')
  url, token, body = CF_URL.format(config.CF_ACCOUNT_ID), config.CF_API_TOKEN, {'model': 'clef-flash', 'state': state, 'questions': QUESTIONS}
  req = urllib.request.Request(url, data=json.dumps(body, ensure_ascii=False).encode(), method='POST',
                               headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
  with urllib.request.urlopen(req, timeout=20) as r:  # usually under a second, cloudflare sometimes takes 7
    res = json.load(r)
  res = res.get('result', res)  # cloudflare wraps the answer in result
  return res.get('model'), {q: float(a.get('noul', 0.0)) for q, a in (res.get('answers') or {}).items()}


def assess(message, conversation=()):
  """message: what the patient just said. conversation: earlier turns [{role, text}], oldest first"""
  message = str(message or '').strip()[:1500]
  if not message:
    return {'level': 'none', 'scores': {}}
  recent = [{'role': str(t.get('role', ''))[:10], 'text': str(t.get('text', ''))[:600]} for t in list(conversation)[-6:]]
  t0 = time.time()
  try:
    model, scores = _ask({'latest_message': message, 'recent_conversation': recent})
  except urllib.error.HTTPError as e:
    return {'error': f'triage HTTP {e.code}'}
  except Exception as e:
    return {'error': type(e).__name__}
  return {'level': _level(scores), 'scores': {q: round(v, 3) for q, v in scores.items()}, 'model': model, 'ms': round(1000 * (time.time() - t0))}

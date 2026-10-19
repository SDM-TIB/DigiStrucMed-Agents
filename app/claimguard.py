"""The claim guard: every statement of an answer that carries no source number is checked with clef-flash before the
reader sees it.

An answer is cut into units (a sentence of running text, a bullet). A unit passes unchecked when it cites a source
([n], [1, 15]), when it is no statement (a heading, a lead-in ending with a colon, fewer than five words) or when it
stands in a table or a code block. Every other unit gets three clef-flash questions at once:

  claim     does it state a medical fact, value or recommendation that would need a source, or is it a greeting, a
            transition, a question or the advice to see a doctor?
  sources   for each source the agent retrieved in this run: does this page support it? (the grounding question of
            RAGTruth-style claim verification, as in MaziyarPanahi/Jev-RAG-Claim-Verification-130K: the page as
            authoritative_context, the unit as candidate_claim, the user's question as query)
  deduced   does it follow from what the answer said before, counting only the statements that carry a source?

and is then
  kept      when it is no claim, when it follows from what came before (a conclusion from cited statements), or when
            the evidence is uncertain (best page between REMOVE_BELOW and CITE_MIN);
  cited     when a retrieved page clearly supports it (CITE_MIN): that page's number is added, as part of the text;
  removed   when it is a claim that no retrieved page supports (all below REMOVE_BELOW), that does not follow
            from the answer so far, and that does not report an absence (the guideline does not say X; a wrong
            premise), which no page could back.

Thresholds were chosen on human labels (experiments/2026-10-19_claim_guard in the MASTERTHESIS repository). A unit
whose checks fail (clef-flash unreachable) is kept unchanged: the guard never blocks an answer.
"""
import concurrent.futures
import json
import re
import time
import urllib.error
import urllib.request

import config

CLEF_URL = 'https://api.cloudflare.com/client/v4/accounts/{}/ai/run/@cf/cloudflare/clef-flash'
CITE = re.compile(r'\[\d+(?:\s*[,;]\s*\d+)*\]')
# three zones, from the calibration on 1,303 human-labelled RAG sentences (RAGTruth via Jev-RAG-Claim-Verification-130K,
# test split; clef-flash ROC AUC 0.863): the guard acts only where it is confident
CITE_MIN = 0.8      # a page this sure gets cited: only 15 % of unsupported sentences reach it
REMOVE_BELOW = 0.3  # below this for every page (and not deduced) the claim goes: 4 % of supported sentences fall here
DEDUCED_MIN = 0.5   # the unit follows from the answer so far
CLAIM_MIN = 0.5     # the unit is a claim that needs a source
PAGE_CHARS = 6000

CLAIM_Q = {'type': 'noul',
           'instructions': 'Does `sentence` state a medical fact, value, rate, recommendation or other claim about the '
                           'world that a reader would need a source for?',
           'criteria': {'true': 'The sentence asserts something checkable: a fact, number, recommendation, class or '
                                'level of evidence, effect, risk or definition.',
                        'false': 'The sentence asserts nothing checkable: a greeting, a transition, a question, an '
                                 'offer of help, a statement about the answer itself, or the advice to see a doctor.'}}
SUPPORT_Q = {'type': 'noul',
             'instructions': 'Is `candidate_claim` supported by `authoritative_context`?',
             'criteria': {'true': 'Everything `candidate_claim` asserts is stated in or directly follows from '
                                  '`authoritative_context`.',
                          'false': '`candidate_claim` asserts something `authoritative_context` does not state or '
                                   'contradicts.'}}

# asked only before a removal: a statement that the sources lack something, or that corrects the question's premise,
# cannot be backed by a page (absence has no page) and must stay (found on the test set: a premise correction was cut)
ABSENCE_Q = {'type': 'noul',
             'instructions': 'Does `sentence` say that the guidelines or sources do not state, cover, recommend or '
                             'support something, or that the premise of the question is wrong?',
             'criteria': {'true': 'The sentence reports an absence or a disagreement: the guideline does not say X, there '
                                  'is no recommendation on X, the question assumes something the guideline does not '
                                  'state, or the evidence is insufficient.',
                          'false': 'The sentence asserts a positive fact, value, effect or recommendation.'}}

_pool = concurrent.futures.ThreadPoolExecutor(64)


def available():
  return bool(config.CF_ACCOUNT_ID and config.CF_API_TOKEN)


def clef(state, question, timeout=20):
  """one clef-flash noul question; the probability of true, or None when it could not be asked"""
  body = {'model': 'clef-flash', 'state': state, 'questions': {'q': question}}
  req = urllib.request.Request(CLEF_URL.format(config.CF_ACCOUNT_ID), data=json.dumps(body, ensure_ascii=False).encode(),
                               method='POST', headers={'Authorization': 'Bearer ' + config.CF_API_TOKEN,
                                                       'Content-Type': 'application/json'})
  for attempt in range(3):
    try:
      with urllib.request.urlopen(req, timeout=timeout) as r:
        res = json.load(r)
      return float(res.get('result', res).get('answers', {}).get('q', {}).get('noul', 0.0))
    except urllib.error.HTTPError as e:
      if e.code not in (429, 500, 502, 503, 504) or attempt == 2:
        return None
    except Exception:
      if attempt == 2:
        return None
    time.sleep(0.5 * 2 ** attempt)


def plain(text):
  return re.sub(r'\s+', ' ', re.sub(CITE.pattern + r'|[*_`#>]', ' ', text)).strip()


def needs_check(unit):
  """a statement without a source number, outside tables and code"""
  s = unit.strip()
  if not s or s.startswith(('#', '|', '```')) or CITE.search(s):
    return False
  if s.rstrip('*_ ').endswith(':'):
    return False
  return len(plain(s).split()) >= 5


def check(unit, question, prior, sources, lead=''):
  """decide one unit. sources: {n: (label, text)} retrieved in this run; prior: the answer so far as shown;
  lead: for a bullet, the line that introduces the list ("The guidelines recommend:"), which a bullet needs to be
  a statement at all. returns {'action': keep|cite|remove, 'n', 'claim', 'support', 'deduced'}"""
  claim_text = plain(f'{lead} {unit}' if lead else unit)
  jobs = {'claim': _pool.submit(clef, {'question': question, 'sentence': claim_text}, CLAIM_Q)}
  for n, (label, text) in sources.items():
    jobs[n] = _pool.submit(clef, {'query': question, 'authoritative_context': f'{label}\n{text[:PAGE_CHARS]}',
                                  'candidate_claim': claim_text}, SUPPORT_Q)
  if prior.strip():
    jobs['deduced'] = _pool.submit(clef, {'query': question, 'authoritative_context': prior[-6000:],
                                          'candidate_claim': claim_text}, SUPPORT_Q)
  got = {k: f.result() for k, f in jobs.items()}
  claim = got.pop('claim')
  deduced = got.pop('deduced', None)
  support = {n: p for n, p in got.items() if p is not None}
  out = {'claim': claim, 'deduced': deduced, 'support': support}
  if claim is None:
    return {**out, 'action': 'keep', 'why': 'unchecked'}
  if claim < CLAIM_MIN:
    return {**out, 'action': 'keep', 'why': 'no claim'}
  best_n, best = max(support.items(), key=lambda kv: kv[1]) if support else (None, 0.0)
  if best >= CITE_MIN:
    return {**out, 'action': 'cite', 'n': best_n, 'why': f'source {best_n}'}
  if deduced is not None and deduced >= DEDUCED_MIN:
    return {**out, 'action': 'keep', 'why': 'follows from the answer'}
  failed = (sources and not support) or (prior.strip() and deduced is None)
  if failed:  # a check could not be asked: never remove on missing evidence about the evidence
    return {**out, 'action': 'keep', 'why': 'unchecked'}
  if best < REMOVE_BELOW:
    absence = clef({'question': question, 'sentence': claim_text}, ABSENCE_Q)
    out['absence'] = absence
    if absence is None or absence >= 0.5:
      return {**out, 'action': 'keep', 'why': 'states an absence' if absence is not None else 'unchecked'}
    return {**out, 'action': 'remove', 'why': 'unsupported claim'}
  return {**out, 'action': 'keep', 'why': 'uncertain'}


def cite(unit, n):
  """the unit with [n] before its closing punctuation (and before trailing whitespace)"""
  body = unit.rstrip()
  tail = unit[len(body):]
  m = re.search(r'([.!?;])(\**)$', body)
  if m:
    return body[:m.start()] + f' [{n}]' + body[m.start():] + tail
  return body + f' [{n}]' + tail


# ---- units in a stream of text
UNIT_END = re.compile(r'(?<=[.!?])(?:\[\d+(?:\s*[,;]\s*\d+)*\])*(\**)(?=\s)|\n')


class Splitter:
  """cuts a growing text into complete units; a unit keeps its trailing whitespace, so the units join to the text"""

  def __init__(self):
    self.buf = ''
    self.fence = False

  def feed(self, delta):
    self.buf += delta
    out = []
    while True:
      m = UNIT_END.search(self.buf)
      if not m:
        return out
      end = m.end()
      while end < len(self.buf) and self.buf[end] in ' \t\n':
        end += 1
      if end == len(self.buf) and self.buf[m.end():].strip() == '' and not self.buf.endswith('\n'):
        return out  # the whitespace after the unit may still grow; wait for the next character
      out.append(self._unit(self.buf[:end]))
      self.buf = self.buf[end:]

  def rest(self):
    u, self.buf = self.buf, ''
    return [self._unit(u)] if u else []

  def _unit(self, text):
    """(text, checkable): lines inside a code fence and table rows are never checked"""
    line = text.strip()
    inside = self.fence
    if line.startswith('```'):
      self.fence = not self.fence
      inside = True
    return text, (not inside and needs_check(text))


# ---- the guard on a chat stream
import queue  # noqa: E402
import threading  # noqa: E402

WORD_DELAY = 0.012  # seconds between the words of a checked unit, so the answer keeps streaming


def _words(text):
  return re.findall(r'\S+\s*|\s+', text)


def guard(events, question, sources_of):
  """the chat events with every text delta passed through the guard, in their original order.
  sources_of(): {n: (label, text)} of the sources retrieved so far in this run"""
  inbox, end = queue.Queue(), object()

  def pump():
    try:
      for ev in events:
        inbox.put(ev)
    except Exception as e:  # handed on in order
      inbox.put(e)
    inbox.put(end)

  threading.Thread(target=pump, daemon=True).start()
  splitter = Splitter()
  pending = []  # in order: ('event', ev) | ('unit', text, future or None)
  said = []     # the original units so far: what came before a unit, for the deduction check
  stats = {'checked': 0, 'kept': 0, 'cited': 0, 'removed': 0}
  items = []
  started = [False]

  def progress(status):
    return {'type': 'verify', 'status': status, **stats, 'items': items[-12:]}

  lead = ['']  # the line introducing the current list

  def take(units):
    for text, checkable in units:
      line = text.strip()
      bullet = re.match(r'([-*+]|\d+\.)\s', line) is not None
      if line and not bullet:
        lead[0] = line if line.rstrip('*_ ').endswith(':') else ''
      fut = None
      if checkable:
        # a conclusion may rest only on statements that carry a source: the uncited ones are no basis (else an
        # unsupported claim could "follow" from another; found on the test set, items N-012 and N-035)
        fut = _pool.submit(check, text, question, ''.join(u for u in said if CITE.search(u)), sources_of(),
                           lead[0] if bullet else '')
      said.append(text)
      pending.append(('unit', text, fut))

  def ready():
    """the items at the head that can go out now"""
    while pending:
      head = pending[0]
      if head[0] == 'event':
        pending.pop(0)
        yield head[1]
        continue
      _, text, fut = head
      if fut is not None and not fut.done():
        return
      pending.pop(0)
      if fut is not None:
        d = fut.result()
        stats['checked'] += 1
        stats[{'keep': 'kept', 'cite': 'cited', 'remove': 'removed'}[d['action']]] += 1
        items.append({'action': d['action'], 'why': d['why'], 'n': d.get('n'), 'text': plain(text)[:160]})
        yield progress('running')
        if d['action'] == 'cite':
          text = cite(text, d['n'])
        elif d['action'] == 'remove':
          # a removed bullet goes with its line; a removed sentence keeps the paragraph break that followed it
          bullet = re.match(r'\s*([-*+]|\d+\.)\s', text) is not None
          text = '' if bullet else text[len(text.rstrip()):] if '\n' in text[len(text.rstrip()):] else ''
      for w in _words(text):
        yield {'type': 'text', 'delta': w}
        time.sleep(WORD_DELAY)

  done = False
  while not done or pending:
    try:
      ev = inbox.get(timeout=0.05)
    except queue.Empty:
      ev = None
    if ev is end:
      done = True
      take(splitter.rest())
    elif isinstance(ev, Exception):
      yield from ready()
      raise ev
    elif ev is not None:
      if ev.get('type') == 'text':
        if not started[0]:
          started[0] = True
          pending.append(('event', progress('running')))
        take(splitter.feed(ev.get('delta') or ''))
      else:
        pending.append(('event', ev))
    yield from ready()
    if done and pending and all(p[0] == 'unit' and p[2] is not None and not p[2].done() for p in pending[:1]):
      time.sleep(0.02)
  if started[0]:
    yield progress('done')

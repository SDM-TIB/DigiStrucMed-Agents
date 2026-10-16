"""One client for every model: the OpenAI Chat Completions API, streamed.

Gemini is the default because Google serves it behind an OpenAI compatible endpoint. Point LLM_BASE_URL at
OpenAI, OpenRouter, vLLM, Ollama or anything else that speaks /chat/completions and nothing else changes.
Lino's vLLM server on Modal goes through the same code.
LLM_API_KEY=vertex uses Vertex AI's OpenAI endpoint with the Cloud Run service account's token instead of a key
(billed to the Cloud billing account; model names then carry the google/ prefix).

stream() yields ('text', delta) while the model talks and ('calls', [tool_call]) once at the end.
"""
import json
import time
import urllib.error
import urllib.request

import config
import storage


class Endpoint:
  def __init__(self, base_url, api_key, model, extra=None):
    self.url = base_url.rstrip('/') + '/chat/completions'
    self.api_key, self.model, self.extra = api_key, model, extra or {}


AGENTS = Endpoint(config.LLM_BASE_URL, config.LLM_API_KEY, config.LLM_MODEL,
                  {'reasoning_effort': config.LLM_REASONING_EFFORT} if config.LLM_REASONING_EFFORT else {})


def _open(ep, body, timeout, fallback=True):
  auth = storage._auth() if ep.api_key == 'vertex' else {'Authorization': 'Bearer ' + ep.api_key}
  req = urllib.request.Request(ep.url, data=json.dumps(body).encode(), method='POST',
                               headers={**auth, 'Content-Type': 'application/json'})
  for attempt in range(4):  # 429/503 happen under load. back off ~6 s before the first byte, never after
    try:
      return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
      if e.code not in (429, 500, 503):
        raise
      e.read()
      if attempt == 3:
        if fallback and ep is AGENTS and config.LLM_FALLBACK_MODEL and body['model'] != config.LLM_FALLBACK_MODEL:
          return _open(ep, {**body, 'model': config.LLM_FALLBACK_MODEL}, timeout, False)  # a model with its own quota
        raise
      time.sleep(0.8 * 2 ** attempt)


def stream(messages, tools=None, tool_choice=None, max_tokens=1200, ep=AGENTS, timeout=60, **extra):
  body = {'model': ep.model, 'messages': messages, 'stream': True, 'max_tokens': max_tokens, **ep.extra, **extra}
  if tools:
    body['tools'] = tools
    if tool_choice:
      body['tool_choice'] = tool_choice
  calls = {}
  with _open(ep, body, timeout) as r:
    for raw in r:
      line = raw.decode('utf-8').strip()
      if not line.startswith('data:') or line == 'data: [DONE]':
        continue
      for choice in json.loads(line[5:]).get('choices', []):
        delta = choice.get('delta') or {}
        if delta.get('content'):
          yield 'text', delta['content']
        for tc in delta.get('tool_calls') or []:  # tool calls arrive in pieces, glue them by index
          call = calls.setdefault(tc.get('index', 0), {'id': None, 'type': 'function', 'function': {'name': '', 'arguments': ''}})
          call['id'] = tc.get('id') or call['id']
          call['function']['name'] += (tc.get('function') or {}).get('name') or ''
          call['function']['arguments'] += (tc.get('function') or {}).get('arguments') or ''
          if tc.get('extra_content'):  # gemini thought signatures. must go back verbatim or the next turn fails
            call['extra_content'] = tc['extra_content']
  yield 'calls', [calls[i] for i in sorted(calls)]


def warm():
  """one tiny request when an instance starts: the vertex token and the model's first response cost about 15 s,
  which the first patient (often in a video call, where tavus does not wait long) should not pay. failures are ignored"""
  t0 = time.time()
  try:
    complete([{'role': 'user', 'content': 'Reply with OK.'}], 16, timeout=40)
  except Exception as e:
    print(f'llm warm-up failed: {type(e).__name__}', flush=True)
  print(f'llm warm-up {time.time() - t0:.1f} s', flush=True)


def complete(messages, max_tokens, ep=AGENTS, timeout=120, **extra):
  """non streaming. returns (text, finish_reason)"""
  body = {'model': ep.model, 'messages': messages, 'stream': False, 'max_tokens': max_tokens, **ep.extra, **extra}
  with _open(ep, body, timeout) as r:
    choice = json.load(r)['choices'][0]
  return (choice['message'].get('content') or '').strip(), choice.get('finish_reason')


def args(call):
  try:
    return json.loads(call['function']['arguments'] or '{}')
  except json.JSONDecodeError:
    return {}


def tool(name, description, properties, required):
  return {'type': 'function', 'function': {'name': name, 'description': description,
          'parameters': {'type': 'object', 'properties': properties, 'required': required}}}


def history(messages, keep, max_chars, system=None):
  """[{role, text}] from the browser -> chat messages"""
  out = [{'role': 'system', 'content': system}] if system else []
  return out + [{'role': 'assistant' if m.get('role') == 'assistant' else 'user', 'content': m['text'][:max_chars]}
                for m in messages[-keep:] if m.get('text')]


def tool_turn(text, calls, results):
  """the assistant's tool calls plus one tool message per result"""
  return ([{'role': 'assistant', 'content': text or None, 'tool_calls': calls}] +
          [{'role': 'tool', 'tool_call_id': c['id'], 'content': r if isinstance(r, str) else json.dumps(r, ensure_ascii=False)}
           for c, r in zip(calls, results)])


def error_event(e, name='LLM'):
  """any exception as a ui error event. never leaks a key"""
  msg = f'{name} HTTP {e.code}' if isinstance(e, urllib.error.HTTPError) else f'{name} unavailable ({type(e).__name__})'
  return {'type': 'error', 'message': msg}

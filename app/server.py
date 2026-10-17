"""TIB Chat. one process, stdlib http server, no framework. runs on Cloud Run with a single instance.

single instance on purpose: gpu wake state, call timers and the usage log live in memory.
auth: one team password, no accounts. signing in gives this browser its own random session id in a signed
HttpOnly cookie (the usual signed-cookie session), so every visitor has their own gpu lease and their own calls.
every POST also needs the X-TIB-Chat header and a matching Origin, enough CSRF protection for a same-origin app.
"""
import hashlib
import hmac
import http.cookies
import json
import mimetypes
import secrets
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import agent_tools
import audit
import board
import config
import corpus
import director
import evidence
import gpu
import graph
import guido
import kg
import check
import followups
import lino
import llm
import pagewords
import search as page_search
import triage
import luna
import storage
import tavus
import wikidata

SESSION_COOKIE, ADMIN_COOKIE = 'tib_session', 'tib_admin'
SESSION_SECONDS, ADMIN_SECONDS = 30 * 24 * 3600, 12 * 3600
ACTION_HEADER = 'X-TIB-Chat'
# stateless sessions: signed with a key derived from the passwords, so rotating a password logs everyone out
_secret = (config.TAVUS_API_KEY or 'tib-chat').encode()
SESSION_TOKEN = hmac.new(_secret, b'session:' + config.DEMO_PASSWORD.encode(), hashlib.sha256).hexdigest()
ADMIN_TOKEN = hmac.new(_secret, b'admin:' + config.ADMIN_PASSWORD.encode(), hashlib.sha256).hexdigest() if config.ADMIN_PASSWORD else ''
STATIC_TYPES = {'.html', '.css', '.js', '.json', '.jpg', '.png', '.svg', '.mp4'}
CHATS = {'luna': luna.run, 'lino': lino.chat, 'guido': guido.run}


def same(given, expected):
  return bool(expected) and hmac.compare_digest(given.encode(), expected.encode())


def session_cookie(sid):
  """sid.signature. only the server can make one, so a visitor can't pick someone else's session"""
  return sid + '.' + hmac.new(SESSION_TOKEN.encode(), sid.encode(), hashlib.sha256).hexdigest()[:32]


class Handler(BaseHTTPRequestHandler):
  def log_message(self, fmt, *args):
    pass  # cloud run logs requests already

  # ---- io
  def cookie(self, name):
    c = http.cookies.SimpleCookie(self.headers.get('Cookie', ''))
    return c[name].value if name in c else ''

  def session(self):
    """this browser's session id, or '' when not signed in"""
    value = self.cookie(SESSION_COOKIE)
    sid = value.partition('.')[0]
    return sid if sid and config.DEMO_PASSWORD and same(value, session_cookie(sid)) else ''

  def signed_in(self):
    return bool(self.session())

  def admin(self):
    return same(self.cookie(ADMIN_COOKIE), ADMIN_TOKEN)

  def set_cookie(self, name, value, max_age):
    secure = '; Secure' if self.headers.get('X-Forwarded-Proto') == 'https' else ''
    return f'{name}={value}; Path=/; Max-Age={max_age}; HttpOnly; SameSite=Strict{secure}'

  def send(self, code, data, content_type='application/json', cookie=None, cache='no-store'):
    raw = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode()
    self.send_response(code)
    self.send_header('Content-Type', content_type)
    self.send_header('Cache-Control', cache)
    self.send_header('Referrer-Policy', 'no-referrer')
    if cookie:
      self.send_header('Set-Cookie', cookie)
    self.send_header('Content-Length', str(len(raw)))
    self.end_headers()
    self.wfile.write(raw)

  def stream(self, events):
    """server-sent events. one json object per event"""
    self.send_response(200)
    self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
    self.send_header('Cache-Control', 'no-store')
    self.send_header('X-Accel-Buffering', 'no')
    self.end_headers()
    try:
      for event in events:
        self.wfile.write(('data: ' + json.dumps(event, ensure_ascii=False) + '\n\n').encode())
        self.wfile.flush()
    except (BrokenPipeError, ConnectionResetError):
      pass  # tab closed

  def static(self, rel):
    path = (config.STATIC / rel).resolve()
    if config.STATIC not in path.parents or path.suffix not in STATIC_TYPES or not path.is_file():
      return self.send(404, {'error': 'Not found'})
    mime = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
    if path.suffix == '.mp4':
      return self.ranged(path, mime)
    cache = 'no-cache' if path.suffix in ('.html', '.css', '.js') else 'private, max-age=86400'
    self.send(200, path.read_bytes(), mime + ('; charset=utf-8' if mime.startswith('text') else ''), cache=cache)

  def ranged(self, path, mime):
    """http range requests, so the video player can seek"""
    size = path.stat().st_size
    start, end = 0, size - 1
    rng = self.headers.get('Range', '')
    if rng.startswith('bytes='):
      a, _, b = rng[6:].split(',')[0].partition('-')
      if a:
        start, end = int(a), min(int(b) if b else size - 1, size - 1)
      elif b:
        start = max(0, size - int(b))
      end = min(end, start + 4 * 1024 * 1024 - 1)  # at most 4 MiB per request
    self.send_response(206 if rng else 200)
    self.send_header('Content-Type', mime)
    self.send_header('Accept-Ranges', 'bytes')
    self.send_header('Cache-Control', 'private, max-age=3600')
    if rng:
      self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
    self.send_header('Content-Length', str(end - start + 1))
    self.end_headers()
    with open(path, 'rb') as fh:
      fh.seek(start)
      left = end - start + 1
      try:
        while left > 0:
          chunk = fh.read(min(256 * 1024, left))
          if not chunk:
            break
          self.wfile.write(chunk)
          left -= len(chunk)
      except (BrokenPipeError, ConnectionResetError):
        pass

  # ---- GET
  def do_GET(self):
    url = urllib.parse.urlparse(self.path)
    path, query = url.path, {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}

    # open to everyone
    if path == '/healthz':
      return self.send(200, {'ok': True})
    if path in ('/', '/index.html'):
      return self.static('index.html')
    if path == '/dashboard':
      return self.static('dashboard.html')
    if path.startswith('/static/') and not path.startswith('/static/videos/'):
      return self.static(path[len('/static/'):])
    if path == '/api/session':
      ok = self.signed_in()
      audit.record(self, 'visit', signed_in=ok)
      return self.send(200, {'authenticated': ok})
    if path == '/api/admin/events':
      return self.send(200, {'events': audit.events(), 'now': time.time()}) if self.admin() else self.send(401, {'error': 'Admin password required'})

    if not self.signed_in():
      return self.send(401, {'error': 'Password required'})
    if path.startswith('/static/videos/'):
      return self.static(path[len('/static/'):])
    if path.startswith('/api/page/'):
      p = corpus.PAGE_BY_ID.get(path.rsplit('/', 1)[-1])
      return self.send(200, {k: p.get(k) for k in ('id', 'title', 'ref', 'page', 'plain')}) if p else self.send(404, {'error': 'Unknown page'})
    if path == '/api/gpu':
      return self.send(200, gpu.status(self.session()))
    if path == '/api/board-schema':
      return self.send(200, board.schema())
    if path == '/api/settings':
      return self.send(200, {'modal': gpu.credits(), 'lino': gpu.status(self.session())})
    if path.startswith('/api/page-image/'):
      pid = path.rsplit('/', 1)[-1]
      img = storage.get(pid + '.webp') if pid in corpus.PAGE_BY_ID else None
      return self.send(200, img, 'image/webp', cache='private, max-age=86400') if img else self.send(404, {'error': 'Image unavailable'})
    route = GET_ROUTES.get(path)
    if route is None:
      return self.send(404, {'error': 'Not found'})
    try:
      res = route(query)
    except ValueError:
      return self.send(400, {'error': 'Bad request'})
    except graph.GraphUnavailable:
      return self.send(503, {'error': 'The knowledge graph server is not reachable right now'})
    return self.send(404, {'error': 'Not found'}) if res is None else self.send(200, res)

  # ---- POST
  def do_POST(self):
    origin = self.headers.get('Origin')
    if origin and origin.split('://', 1)[-1] != self.headers.get('Host'):
      return self.send(403, {'error': 'Same-origin request required'})
    length = int(self.headers.get('Content-Length') or 0)
    body = json.loads(self.rfile.read(length) or b'{}') if length else {}
    if self.path.startswith('/api/agent/tools/'):  # luna's tools for the agent server, server to server, by token
      if not agent_tools.authorised(self.headers, self.headers.get('Host', '')):
        return self.send(401, {'error': 'Agent identity required'})
      res = agent_tools.call(self.path.rsplit('/', 1)[-1], body)
      return self.send(200, res) if res is not None else self.send(404, {'error': 'Unknown tool'})
    if self.path == '/api/call/end-beacon':  # navigator.sendBeacon on tab close cannot set headers
      return self.send(200, {'ended': self.end_call(body, via='page closed')}) if self.signed_in() else self.send(401, {})
    if self.headers.get(ACTION_HEADER) != '1':
      return self.send(403, {'error': 'Action header required'})

    if self.path == '/api/login':
      return self.login(body, config.DEMO_PASSWORD, SESSION_COOKIE, session_cookie(secrets.token_urlsafe(16)), SESSION_SECONDS, 'login')
    if self.path == '/api/admin/login':
      return self.login(body, config.ADMIN_PASSWORD, ADMIN_COOKIE, ADMIN_TOKEN, ADMIN_SECONDS, 'admin_login')
    if self.path in ('/api/logout', '/api/admin/logout'):
      return self.send(200, {'ok': True}, cookie=self.set_cookie(ADMIN_COOKIE if 'admin' in self.path else SESSION_COOKIE, '', 0))
    if not self.signed_in():
      return self.send(401, {'error': 'Password required'})

    if self.path.startswith('/api/chat/'):
      return self.chat(self.path.rsplit('/', 1)[-1], body)
    if self.path == '/api/evidence':  # luna's call: verbatim guideline quotes for what she just said, or nothing
      return self.send(200, evidence.board(body.get('text', '')))
    if self.path == '/api/board':
      return self.stream(board.board(body.get('text', '')))
    if self.path == '/api/locate':  # where on the original page: the first of the texts that is found there
      for i, it in enumerate((body.get('items') or [])[:4]):
        r = pagewords.locate(str(body.get('page_id', '')), str(it.get('text', ''))[:3000], 'near' if it.get('mode') == 'near' else 'exact')
        if r:
          return self.send(200, {**r, 'item': i})
      return self.send(200, {'rects': []})
    if self.path == '/api/check':  # lino's statements against the guidelines, after he answered
      return self.send(200, check.check(body.get('question', ''), body.get('answer', '')))
    if self.path == '/api/followups':  # questions to ask next and to take to the doctor
      return self.send(200, followups.suggest(body.get('question', ''), body.get('answer', '')))
    if self.path == '/api/triage':  # does what the patient said call for action now? decides which fixed banner shows
      res = triage.assess(body.get('message', ''), body.get('conversation') or [])
      if res.get('level') not in (None, 'none'):
        audit.record(self, 'triage', level=res['level'])
      return self.send(200, res)
    if self.path == '/api/director':
      return self.send(200, director.decide(body.get('state') or {}))
    if self.path == '/api/gpu/wake':
      hold = int(body.get('hold_s') or 0)
      audit.record(self, 'gpu_wake', hold_min=round(hold / 60) or None, was=gpu.STATE['status'])
      return self.send(200, gpu.wake(self.session(), hold))
    if self.path == '/api/gpu/touch':
      gpu.touch(self.session())
      return self.send(200, gpu.status(self.session()))
    if self.path == '/api/gpu/shutdown':  # admins stop it for everyone, visitors only drop their own lease
      res = gpu.shutdown(self.session(), force=self.admin())
      audit.record(self, 'gpu_shutdown', stopped=res.get('stopped'), shared=res.get('shared'))
      return self.send(200, res)
    if self.path == '/api/call/lino':  # tavus tool call ask_lino, the answer is spoken verbatim
      question = str(body.get('question', ''))
      audit.record(self, 'call_tool', agent='Lino', text=question[:200])
      gpu.touch(self.session())
      try:
        return self.send(200, {'output': lino.answer(question)})
      except Exception as e:
        return self.send(502, {'error': f'Lino unavailable ({type(e).__name__})'})
    if self.path == '/api/call/start':
      agent = body.get('agent') if body.get('agent') in tavus.PALS else 'luna'
      try:
        res = tavus.start(agent, self.session())
      except Exception as e:
        return self.send(502, {'error': str(e)})
      audit.record(self, 'call_start', agent=agent.title(), conversation_id=res['conversation_id'])
      return self.send(200, res)
    if self.path == '/api/call/end':
      return self.send(200, {'ended': self.end_call(body)})
    return self.send(404, {'error': 'Not found'})

  # ---- handlers that need the request
  def login(self, body, password, name, token, max_age, kind):
    if same(str(body.get('password', '')), password):
      audit.record(self, kind)
      return self.send(200, {'authenticated': True}, cookie=self.set_cookie(name, token, max_age))
    audit.record(self, kind + '_failed')
    time.sleep(1)  # slow down guessing
    return self.send(401, {'error': 'Wrong password'})

  def chat(self, agent, body):
    if agent not in CHATS:
      return self.send(404, {'error': 'Unknown agent'})
    messages = body.get('messages') or []
    last = next((m.get('text', '') for m in reversed(messages) if m.get('role') == 'user'), '')
    audit.record(self, 'call_tool' if body.get('via') == 'call' else 'chat', agent=agent.title(), text=str(last)[:200], turn=len(messages))
    if agent == 'lino':  # only lino runs on the gpu. luna and guido use the llm api and are always there
      gpu.touch(self.session())
    if agent == 'luna':  # optional step bound and agent harness ('mastra' or 'aisdk'), for evaluations
      return self.stream(luna.run(messages, rounds=body.get('rounds'), harness=body.get('harness'), image_tool=body.get('image_tool')))
    if agent == 'guido':  # optional tool round budget, used by the evaluation ablations
      return self.stream(CHATS[agent](messages, rounds=body.get('rounds')))
    return self.stream(CHATS[agent](messages))

  def end_call(self, body, via=None):
    cid = str(body.get('conversation_id') or '')
    started = audit.call_started(cid)
    if cid and started:
      audit.record(self, 'call_end', conversation_id=cid, agent=started.get('agent'), seconds=round(time.time() - started['ts']), via=via)
    try:
      return tavus.end(cid, self.session()) if cid else False
    except Exception:
      return False


def search(q):
  q = q.get('q', '')[:160]
  if not q.strip():
    return {'pages': [], 'kg': []}
  return {'pages': [corpus.card(0, i, q, width=260) for i in corpus.bm25(q, k=8)], 'kg': kg.search(q, k=6)}


def facts(q):
  cid = q.get('id', '')
  return wikidata.concept_card(cid) if cid in kg.CONCEPTS else None


GET_ROUTES = {
  '/api/library': lambda q: corpus.library(),
  '/api/search': search,
  '/api/kg/node': lambda q: kg.neighbours(q.get('id', '')),
  '/api/kg/search': lambda q: {'items': kg.search(q.get('q', '')[:120])},
  '/api/kg/facts': facts,
  '/api/kg/evidence': lambda q: guido.evidence(q.get('id', '')),
  '/api/tavus/usage': lambda q: tavus.usage(),
  # the explorer of the complete final graph (Sources > Graph), straight from the graph server
  '/api/graph/kinds': lambda q: {'kinds': graph.kinds()},
  '/api/graph/search': lambda q: {'items': graph.search(q.get('q', '')[:160], [k for k in q.get('kinds', '').split(',') if k] or None)},
  '/api/graph/node': lambda q: graph.node(q.get('id', '')),
  '/api/search/status': lambda q: page_search.status(),
  '/api/graph/neighbours': lambda q: graph.neighbours(q.get('id', ''), q.get('p') or None, q.get('dir') or None,
                                                       q.get('limit', 40), q.get('offset', 0)),
}

if __name__ == '__main__':
  mimetypes.add_type('image/svg+xml', '.svg')
  llm.warm()  # before listening: cloud run only routes to a new revision once it accepts connections
  page_search.load()  # luna's page index, before listening (full cpu at startup); without it the search uses bm25
  page_search.warm()  # wakes the embedding service
  ThreadingHTTPServer(('0.0.0.0', config.PORT), Handler).serve_forever()

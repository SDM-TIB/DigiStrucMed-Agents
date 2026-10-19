"""Forwards every request to the app's current address, path and query kept (HTTP 308: a POST stays a POST).

Runs as the service that used to be the app (luna-demo in project tib-chat-1011-13273), so old links keep working
after the move to project tib-chat-20261019 (19 October 2026). TARGET is the new address.
"""
import http.server
import os

TARGET = os.environ['TARGET'].rstrip('/')


class Forward(http.server.BaseHTTPRequestHandler):
  def forward(self):
    self.send_response(308)
    self.send_header('Location', TARGET + self.path)
    self.send_header('Cache-Control', 'public, max-age=3600')
    self.send_header('Content-Length', '0')
    self.end_headers()

  do_GET = do_HEAD = do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = forward

  def log_message(self, *args):
    pass


if __name__ == '__main__':
  http.server.ThreadingHTTPServer(('', int(os.environ.get('PORT', '8080'))), Forward).serve_forever()

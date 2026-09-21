#!/usr/bin/env python3
"""Static server for the Image Mutation Tool UI.

Serves an explicit allowlist of files and nothing else. It used to hand out the
whole project directory through SimpleHTTPRequestHandler, which meant
`GET /src/backend/.env` returned the configuration file and `GET /outputs/` returned
a directory listing. That is only a localhost problem until you start the tool
with `./run.sh --host 0.0.0.0`, which is a supported mode -- so the allowlist is
the fix, not a warning in the docs.

    FRONTEND_PORT   port to listen on                    (default 3000)
    FRONTEND_BIND   address to bind                      (default 127.0.0.1)
    FLASK_PORT      port the backend API listens on,     (default 5000)
                    injected into the page so the UI
                    calls the right one
"""

import http.server
import os
import socket
import socketserver
import sys
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get('FRONTEND_PORT', 3000))
BIND = os.environ.get('FRONTEND_BIND', '127.0.0.1')
API_PORT = int(os.environ.get('FLASK_PORT', 5000))

# advanced-index.html carries this line verbatim; we rewrite it on the way out
# so the page calls whatever port the backend was actually started on.
API_PORT_MARKER = '<script>window.__API_PORT__ = 5000;</script>'

# The only files the UI loads. advanced-index.html references exactly one
# asset (vendor/jszip.min.js); everything else it needs comes from the API on
# port 5000. Add a line here if the UI grows a new asset.
ALLOWED = {
    '/': ('advanced-index.html', 'text/html; charset=utf-8'),
    '/advanced-index.html': ('advanced-index.html', 'text/html; charset=utf-8'),
    '/vendor/jszip.min.js': ('vendor/jszip.min.js', 'application/javascript'),
    '/favicon.ico': (None, None),          # answered 204, not 404, to keep logs quiet
}


class UIHandler(http.server.BaseHTTPRequestHandler):
    server_version = 'ImageMutationUI'
    sys_version = ''

    def _headers(self, status, ctype=None, length=None):
        self.send_response(status)
        if ctype:
            self.send_header('Content-Type', ctype)
        if length is not None:
            self.send_header('Content-Length', str(length))
        # The UI is same-origin with itself and talks to :5000 cross-origin;
        # the backend is what has to allow that, so these stay permissive.
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()

    def do_OPTIONS(self):
        self._headers(204)

    def do_HEAD(self):
        self.do_GET(body=False)

    def do_GET(self, body=True):
        # Match on the path alone: a query string or fragment must not turn an
        # allowed path into an unknown one.
        path = urlparse(self.path).path
        entry = ALLOWED.get(path)
        if entry is None:
            self._headers(404, 'text/plain; charset=utf-8')
            if body:
                self.wfile.write(b'404\n')
            return

        rel, ctype = entry
        if rel is None:                     # /favicon.ico
            self._headers(204)
            return

        full = os.path.join(HERE, rel)
        try:
            with open(full, 'rb') as fh:
                data = fh.read()
            if rel == 'advanced-index.html' and API_PORT != 5000:
                marker = API_PORT_MARKER.encode()
                if marker not in data:
                    print(f'WARNING: {rel} has no API-port marker; the UI will '
                          f'call port 5000, not {API_PORT}')
                data = data.replace(
                    marker,
                    f'<script>window.__API_PORT__ = {API_PORT};</script>'.encode())
        except OSError:
            self._headers(500, 'text/plain; charset=utf-8')
            if body:
                self.wfile.write(f'{rel} is missing from the install\n'.encode())
            return

        self._headers(200, ctype, len(data))
        if body:
            self.wfile.write(data)

    def log_message(self, fmt, *args):
        print(f'[{self.client_address[0]}] {fmt % args}')


def main():
    try:
        local_ip = socket.gethostbyname(socket.gethostname())
    except OSError:
        local_ip = '127.0.0.1'

    print()
    print('Image Mutation Tool - UI server')
    print(f'  serving : {", ".join(sorted(p for p in ALLOWED if p != "/"))}')
    print(f'  root    : {HERE}')
    print(f'  api on  : port {API_PORT}')

    # Must be set on the class before binding. Setting it on the instance
    # afterwards is too late, and a socket still in TIME_WAIT from a previous
    # run then rejects the bind with "address already in use".
    socketserver.TCPServer.allow_reuse_address = True
    try:
        with socketserver.TCPServer((BIND, PORT), UIHandler) as httpd:
            if BIND in ('127.0.0.1', 'localhost'):
                print(f'  open    : http://localhost:{PORT}  (this machine only)')
            else:
                print(f'  open    : http://{local_ip}:{PORT}  (reachable from the network)')
            print('  Ctrl+C to stop')
            print()
            httpd.serve_forever()
    except KeyboardInterrupt:
        print('\nstopped')
    except OSError as e:
        if e.errno in (48, 98):
            print(f'\nERROR: port {PORT} is already in use.')
            print(f"       Find it with:  ss -lptn 'sport = :{PORT}'")
            print('       Or pick another: FRONTEND_PORT=3001 ./run.sh')
        else:
            print(f'\nERROR: {e}')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())

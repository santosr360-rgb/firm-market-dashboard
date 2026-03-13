#!/usr/bin/env python3
"""
Bloomberg Dashboard - Local Proxy Server
Run this file, it opens the dashboard automatically in your browser.
Press Ctrl+C in Terminal to stop.
"""
import http.server
import urllib.request
import urllib.parse
import threading
import webbrowser
import os
import sys

PORT = 8765
DIR  = os.path.dirname(os.path.abspath(__file__))

class Handler(http.server.BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        pass  # suppress request logs

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)

        # ── Serve dashboard HTML ──────────────────────────────
        if parsed.path in ('/', '/index.html', '/bloomberg-dashboard.html'):
            path = os.path.join(DIR, 'bloomberg-dashboard.html')
            try:
                with open(path, 'rb') as f:
                    body = f.read()
                self._respond(200, 'text/html; charset=utf-8', body)
            except FileNotFoundError:
                self.send_error(404, 'bloomberg-dashboard.html not found')
            return

        # ── Proxy any external API call ───────────────────────
        if parsed.path == '/proxy':
            qs  = urllib.parse.parse_qs(parsed.query)
            url = qs.get('url', [''])[0]
            if not url:
                self.send_error(400, 'Missing url param')
                return
            try:
                req = urllib.request.Request(
                    url,
                    headers={
                        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                                      'AppleWebKit/537.36 (KHTML, like Gecko) '
                                      'Chrome/120.0.0.0 Safari/537.36',
                        'Accept': 'application/json, text/plain, */*',
                        'Accept-Language': 'en-US,en;q=0.9',
                    }
                )
                with urllib.request.urlopen(req, timeout=12) as resp:
                    body = resp.read()
                self._respond(200, 'application/json', body,
                              extra_headers={'Access-Control-Allow-Origin': '*'})
            except urllib.error.HTTPError as e:
                body = e.read()
                self._respond(e.code, 'application/json', body,
                              extra_headers={'Access-Control-Allow-Origin': '*'})
            except Exception as e:
                err = str(e).encode()
                self._respond(502, 'text/plain', err,
                              extra_headers={'Access-Control-Allow-Origin': '*'})
            return

        self.send_error(404)

    def _respond(self, code, ctype, body, extra_headers=None):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)


def main():
    server = http.server.ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
    url    = f'http://localhost:{PORT}'
    print(f'\n  Bloomberg Dashboard running at  {url}')
    print('  Press Ctrl+C to stop.\n')
    threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n  Server stopped.')
        server.server_close()

if __name__ == '__main__':
    main()

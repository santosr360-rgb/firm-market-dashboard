#!/usr/bin/env python3
"""
Bloomberg Dashboard - Local Data Server
Run this file. It opens the dashboard in your browser automatically.
Keep this Terminal window open while using the dashboard.
Press Ctrl+C to stop.
"""
import http.server
import urllib.request
import urllib.parse
import threading
import webbrowser
import os
import json
import time
import sys

PORT = 8765
DIR  = os.path.dirname(os.path.abspath(__file__))

# ── Simple in-memory cache ────────────────────────────────────
_cache = {}
def cache_get(key, ttl):
    entry = _cache.get(key)
    if entry and (time.time() - entry['ts']) < ttl:
        return entry['data']
    return None
def cache_set(key, data):
    _cache[key] = {'data': data, 'ts': time.time()}

# ── yfinance ─────────────────────────────────────────────────
try:
    import yfinance as yf
except ImportError:
    print('\n  ERROR: yfinance not installed.')
    print('  Run:  pip3 install yfinance\n')
    sys.exit(1)

def get_quotes(symbols):
    """Return {sym: {price, change, changePct}} for a list of symbols."""
    key = 'q:' + ','.join(sorted(symbols))
    cached = cache_get(key, 60)
    if cached is not None:
        return cached

    result = {}
    try:
        raw = yf.download(
            symbols,
            period='5d',
            interval='1d',
            auto_adjust=True,
            progress=False,
            threads=True,
        )
        closes = raw['Close'] if 'Close' in raw.columns else raw

        if len(symbols) == 1:
            sym   = symbols[0]
            vals  = closes.dropna()
            if len(vals) >= 2:
                price = float(vals.iloc[-1])
                prev  = float(vals.iloc[-2])
                chg   = price - prev
                result[sym] = {
                    'price':     round(price, 4),
                    'change':    round(chg, 4),
                    'changePct': round(chg / prev * 100, 4) if prev else 0,
                }
        else:
            for sym in symbols:
                try:
                    col  = closes[sym] if sym in closes.columns else None
                    if col is None:
                        continue
                    vals = col.dropna()
                    if len(vals) >= 2:
                        price = float(vals.iloc[-1])
                        prev  = float(vals.iloc[-2])
                        chg   = price - prev
                        result[sym] = {
                            'price':     round(price, 4),
                            'change':    round(chg, 4),
                            'changePct': round(chg / prev * 100, 4) if prev else 0,
                        }
                except Exception:
                    pass
    except Exception as e:
        print(f'  Quote error: {e}')

    cache_set(key, result)
    return result


def get_history(symbol):
    """Return {weekChg, monthChg, ytdChg, yearChg} for one symbol."""
    key = 'h:' + symbol
    cached = cache_get(key, 3600)
    if cached is not None:
        return cached

    try:
        raw    = yf.download(symbol, period='1y', interval='1d',
                             auto_adjust=True, progress=False)
        closes = raw['Close'].dropna()

        if len(closes) < 10:
            return None

        current = float(closes.iloc[-1])

        def pct(idx):
            try:
                old = float(closes.iloc[idx])
                return round((current - old) / old * 100, 2) if old else None
            except Exception:
                return None

        # YTD — last close before Jan 1 of current year
        current_year = time.localtime().tm_year
        ytd_chg = None
        dates = list(closes.index)
        for i, dt in enumerate(dates):
            yr = dt.year if hasattr(dt, 'year') else time.localtime(dt).tm_year
            if yr >= current_year and i > 0:
                old = float(closes.iloc[i - 1])
                ytd_chg = round((current - old) / old * 100, 2) if old else None
                break

        result = {
            'weekChg':  pct(-6),   # ~5 trading days
            'monthChg': pct(-22),  # ~21 trading days
            'ytdChg':   ytd_chg,
            'yearChg':  pct(0),    # first data point ≈ 1 year ago
        }
        cache_set(key, result)
        return result

    except Exception as e:
        print(f'  History error {symbol}: {e}')
        return None


# ── HTTP Handler ──────────────────────────────────────────────
class Handler(http.server.BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        pass  # silence request log

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        qs     = urllib.parse.parse_qs(parsed.query)

        # Serve dashboard HTML
        if parsed.path in ('/', '/index.html', '/bloomberg-dashboard.html'):
            fpath = os.path.join(DIR, 'bloomberg-dashboard.html')
            try:
                with open(fpath, 'rb') as f:
                    body = f.read()
                self._send(200, 'text/html; charset=utf-8', body)
            except FileNotFoundError:
                self.send_error(404)
            return

        # /api/quotes?s=^GSPC,^DJI,GC=F
        if parsed.path == '/api/quotes':
            symbols = [s.strip() for s in qs.get('s', [''])[0].split(',') if s.strip()]
            if not symbols:
                self._json({'error': 'no symbols'})
                return
            data = get_quotes(symbols)
            self._json(data)
            return

        # /api/history?s=AAPL,MSFT
        if parsed.path == '/api/history':
            symbols = [s.strip() for s in qs.get('s', [''])[0].split(',') if s.strip()]
            result  = {}
            for sym in symbols:
                h = get_history(sym)
                if h:
                    result[sym] = h
            self._json(result)
            return

        # /api/fred?key=KEY&series=DGS10
        if parsed.path == '/api/fred':
            fred_key = qs.get('key', [''])[0]
            series   = qs.get('series', [''])[0]
            if not fred_key or not series:
                self._json({'error': 'missing key or series'})
                return
            url = (f'https://api.stlouisfed.org/fred/series/observations'
                   f'?series_id={series}&api_key={fred_key}'
                   f'&limit=1&sort_order=desc&file_type=json')
            try:
                req = urllib.request.Request(url, headers={'Accept': 'application/json'})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    body = resp.read()
                self._send(200, 'application/json', body,
                           extra={'Access-Control-Allow-Origin': '*'})
            except Exception as e:
                self._json({'error': str(e)})
            return

        self.send_error(404)

    def _json(self, data):
        body = json.dumps(data).encode()
        self._send(200, 'application/json', body,
                   extra={'Access-Control-Allow-Origin': '*'})

    def _send(self, code, ctype, body, extra=None):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        if extra:
            for k, v in extra.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)


def main():
    print('\n  Bloomberg Dashboard starting...')
    # Warm up yfinance with a quick test fetch
    try:
        test = get_quotes(['^GSPC'])
        if test:
            spx = test.get('^GSPC', {})
            print(f'  Market data OK — S&P 500: {spx.get("price", "?")}')
        else:
            print('  Warning: test quote returned empty — market may be closed')
    except Exception as e:
        print(f'  Warning: {e}')

    server = http.server.ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
    url    = f'http://localhost:{PORT}'
    print(f'  Dashboard: {url}')
    print('  Press Ctrl+C to stop.\n')
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n  Stopped.')
        server.server_close()


if __name__ == '__main__':
    main()

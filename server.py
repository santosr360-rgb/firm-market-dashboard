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
import concurrent.futures

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

def _quote_one(sym):
    """Fetch a single symbol's current quote via fast_info."""
    try:
        fi    = yf.Ticker(sym).fast_info
        price = fi.last_price
        prev  = fi.previous_close
        if price is None or prev is None:
            return sym, None
        import math
        if math.isnan(price) or math.isnan(prev) or prev == 0:
            return sym, None
        chg = price - prev
        return sym, {
            'price':     round(float(price), 6),
            'change':    round(float(chg), 6),
            'changePct': round(float(chg / prev * 100), 4),
        }
    except Exception:
        return sym, None


def get_quotes(symbols):
    """Return {sym: {price, change, changePct}} for a list of symbols."""
    key = 'q:' + ','.join(sorted(symbols))
    cached = cache_get(key, 60)
    if cached is not None:
        return cached

    result = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as ex:
        futures = {ex.submit(_quote_one, sym): sym for sym in symbols}
        for fut in concurrent.futures.as_completed(futures, timeout=20):
            sym, data = fut.result()
            if data:
                result[sym] = data

    cache_set(key, result)
    return result


def get_history_batch(symbols):
    """Return {sym: {weekChg,monthChg,ytdChg,yearChg}} for many symbols at once.
    Uses weekly bars to minimise data transfer and maximise speed."""
    result  = {}
    to_fetch = []
    for sym in symbols:
        c = cache_get('h:' + sym, 3600)
        if c is not None:
            result[sym] = c
        else:
            to_fetch.append(sym)

    if not to_fetch:
        return result

    try:
        import pandas as pd
        raw = yf.download(
            to_fetch,
            period='1y',
            interval='1wk',
            auto_adjust=True,
            progress=False,
            threads=True,
        )
        if raw.empty:
            return result

        # Normalise to a DataFrame where columns = symbols
        if isinstance(raw.columns, pd.MultiIndex):
            closes_df = raw['Close']
        else:
            closes_df = raw[['Close']].rename(columns={'Close': to_fetch[0]})

        current_year = time.localtime().tm_year

        for sym in to_fetch:
            try:
                col = closes_df.get(sym)
                if col is None:
                    continue
                closes = col.dropna()
                if len(closes) < 4:
                    continue

                current = float(closes.iloc[-1])

                def pct(idx):
                    try:
                        old = float(closes.iloc[idx])
                        return round((current - old) / old * 100, 2) if old else None
                    except Exception:
                        return None

                ytd_chg = None
                for i, dt in enumerate(closes.index):
                    yr = dt.year if hasattr(dt, 'year') else current_year
                    if yr >= current_year and i > 0:
                        old = float(closes.iloc[i - 1])
                        ytd_chg = round((current - old) / old * 100, 2) if old else None
                        break

                h = {
                    'weekChg':  pct(-2),   # 1 weekly bar ago
                    'monthChg': pct(-5),   # ~4 weekly bars ago
                    'ytdChg':   ytd_chg,
                    'yearChg':  pct(0),    # first bar ~1yr ago
                }
                result[sym] = h
                cache_set('h:' + sym, h)
            except Exception:
                pass

    except Exception as e:
        print(f'  History batch error: {e}')

    return result


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

        # /api/history?s=AAPL,MSFT,...  (all symbols in one call)
        if parsed.path == '/api/history':
            symbols = [s.strip() for s in qs.get('s', [''])[0].split(',') if s.strip()]
            self._json(get_history_batch(symbols))
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

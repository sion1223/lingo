# -*- coding: utf-8 -*-
"""로컬 데모 서버 (표준 라이브러리만 사용).

python -m scorer.server --port 8765
브라우저/아이패드에서 http://<PC-IP>:8765 접속 -> 캔버스에 쓰고 채점.
"""
import argparse
import json
import os
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .score_api import KanjiScorer

STATIC_DIR = os.path.join(os.path.dirname(__file__), 'static')


def make_handler(ks):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, body, ctype='application/json; charset=utf-8'):
            data = body if isinstance(body, bytes) else json.dumps(
                body, ensure_ascii=False).encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            url = urllib.parse.urlparse(self.path)
            if url.path in ('/', '/index.html'):
                with open(os.path.join(STATIC_DIR, 'index.html'), 'rb') as f:
                    self._send(200, f.read(), 'text/html; charset=utf-8')
            elif url.path == '/template':
                q = urllib.parse.parse_qs(url.query)
                ch = q.get('char', [''])[0]
                try:
                    tmpl = ks.template(ch)
                    self._send(200, {'strokes': [s.tolist() for s in tmpl]})
                except Exception as e:
                    self._send(404, {'error': str(e)})
            else:
                self._send(404, {'error': 'not found'})

        def do_POST(self):
            if self.path != '/score':
                return self._send(404, {'error': 'not found'})
            n = int(self.headers.get('Content-Length', 0))
            try:
                req = json.loads(self.rfile.read(n))
                report = ks.score(req['char'], req['strokes'])
                self._send(200, report)
            except Exception as e:
                self._send(400, {'error': str(e)})

        def log_message(self, fmt, *args):
            pass
    return Handler


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=8765)
    ap.add_argument('--checkpoint', default='checkpoints/scorer.pt')
    ap.add_argument('--kanji-dir', default='kanji')
    args = ap.parse_args()
    ks = KanjiScorer(args.checkpoint, args.kanji_dir)
    srv = ThreadingHTTPServer(('0.0.0.0', args.port), make_handler(ks))
    print(f'serving on http://localhost:{args.port}')
    srv.serve_forever()


if __name__ == '__main__':
    main()

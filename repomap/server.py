"""HTTP server local: phuc vu giao dien web (repomap/web) va JSON API.

  GET /api/tree                       -> danh sach repo (tang 0)
  GET /api/insight?repo=PATH[&force=1] -> tang 1
  GET /api/deep?repo=PATH[&force=1]    -> tang 2
  GET /api/file?repo=PATH&f=REL        -> tang 3
"""
import json
import mimetypes
import pkgutil
import posixpath
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import __version__
from .deepscan import deep_scan
from .fileview import file_view
from .insight import insight


def load_asset(path):
    """Doc file tinh trong package (chay duoc ca khi dong goi .pyz)."""
    path = posixpath.normpath(path).lstrip('/')
    if path.startswith('..'):
        return None
    try:
        return pkgutil.get_data('repomap', 'web/' + path)
    except OSError:
        return None


def make_handler(ws):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'repomap/' + __version__

        def log_message(self, *a):
            pass

        def _send(self, data, ctype='application/json; charset=utf-8', code=200):
            if not isinstance(data, bytes):
                data = json.dumps(data, ensure_ascii=False).encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            u = urlparse(self.path)
            try:
                if u.path.startswith('/api/'):
                    return self._api(u.path[5:], {k: v[0] for k, v in parse_qs(u.query).items()})
                name = 'index.html' if u.path in ('', '/') else u.path
                data = load_asset(name)
                if data is None:
                    return self._send({'error': 'not found'}, code=404)
                ctype = mimetypes.guess_type(name)[0] or 'application/octet-stream'
                if name.endswith('.js'):
                    ctype = 'text/javascript'
                self._send(data, ctype + '; charset=utf-8')
            except ConnectionError:      # trinh duyet da huy request - khong con ai de tra loi
                pass
            except Exception as e:  # noqa: BLE001 - tra loi loi cho UI thay vi dong ket noi
                self._send({'error': '%s: %s' % (type(e).__name__, e)}, code=500)

        def _api(self, ep, q):
            repo, force = q.get('repo', ''), q.get('force') == '1'
            if ep == 'tree':
                return self._send({'root': ws.root, 'mode': ws.mode, 'took': round(ws.took, 2),
                                   'version': __version__, 'repos': ws.repos})
            if repo not in ws.by_path and not (ep == 'file' and repo == ''):
                return self._send({'error': 'khong co repo: ' + repo}, code=404)
            if ep == 'insight':
                return self._send(ws.cached(('insight', repo), lambda: insight(ws, repo), force))
            if ep == 'deep':
                return self._send(ws.cached(('deep', repo), lambda: deep_scan(ws, repo), force))
            if ep == 'file':
                return self._send(file_view(ws, repo, q.get('f', '')))
            self._send({'error': 'api khong ton tai: ' + ep}, code=404)

    return Handler


class Server(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        # Client ngat ket noi giua chung (huy request, dong tab, reset) la binh thuong, khong in traceback.
        if isinstance(sys.exc_info()[1], ConnectionError):
            return
        super().handle_error(request, client_address)


def start_server(ws, host='127.0.0.1', port=8800, tries=20):
    """Mo server o cong trong dau tien trong [port, port+tries)."""
    handler = make_handler(ws)
    for p in range(port, port + tries):
        try:
            return Server((host, p), handler)
        except OSError:
            continue
    raise SystemExit('khong mo duoc cong %d..%d' % (port, port + tries - 1))

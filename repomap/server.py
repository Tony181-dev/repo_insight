"""HTTP server local: phuc vu giao dien web (repomap/web) va JSON API.

  GET /api/tree                       -> danh sach repo (tang 0)
  GET /api/insight?repo=PATH[&force=1] -> tang 1
  GET /api/deep?repo=PATH[&force=1]    -> tang 2
  GET /api/file?repo=PATH&f=REL        -> tang 3 (metadata + doan dau)
  GET /api/lines?repo=PATH&f=REL&from=N&n=M -> doan dong tiep theo cua file dai
  GET /api/ls?repo=PATH&dir=REL        -> full source: noi dung 1 thu muc
  GET /api/search?repo=PATH&base=REL&q=TEXT&mode=name|text -> full source: tim file / tim noi dung
  GET /api/arch[?force=1]              -> kien truc tong: component theo tang (block view, ban gon)
  GET /api/archbox?id=BOX              -> chi tiet 1 khoi (file, phu thuoc cua tung component)
  GET /api/script?repo=PATH&name=FILE  -> noi dung build script hoac script rieng cua module
  POST /api/script-create / script-empty-create / script-save -> tao va luu script
  POST /api/build-start / build-stop / build-clear -> chay, dung, xoa log
  GET /api/build?repo=PATH&name=FILE&from=N -> trang thai + log tu byte N
  GET /api/builds                      -> trang thai script dang chay cua moi module
API ghi (POST) chi nhan tu chinh trang repomap cua user dang chay server: xem guard.py.
"""
import json
import mimetypes
import pkgutil
import posixpath
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import __version__
from .arch import box_detail, build_arch, slim_arch
from .builds import Builds
from .buildscript import (SCRIPT, create_empty_script, create_script, custom_scripts, has_script,
                          read_script, save_script)
from .deepscan import deep_scan
from .fileview import file_lines, file_view
from .guard import write_denied
from .insight import insight
from .source import list_dir, search


def load_asset(path):
    """Doc file tinh trong package (chay duoc ca khi dong goi .pyz)."""
    path = posixpath.normpath(path).lstrip('/')
    if path.startswith('..'):
        return None
    try:
        return pkgutil.get_data('repomap', 'web/' + path)
    except OSError:
        return None


def make_handler(ws, builds):
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
                                   'version': __version__, 'repos': ws.repos, 'noEdit': write_denied(self, header=False)})
            if ep == 'arch':
                full = ws.cached(('arch',), lambda: build_arch(ws, force), force)
                return self._send(ws.cached(('arch-slim',), lambda: slim_arch(full), force))
            if ep == 'builds':
                return self._send(builds.summary())
            if ep == 'archbox':
                return self._send(box_detail(ws.cached(('arch',), lambda: build_arch(ws)), q.get('id', '')))
            if repo not in ws.by_path and not (ep in ('file', 'lines', 'ls') and repo == ''):
                return self._send({'error': 'no such repo: ' + repo}, code=404)
            if ep == 'insight':
                ins = ws.cached(('insight', repo), lambda: insight(ws, repo), force)
                return self._send(dict(ins, script=has_script(ws, repo), scripts=custom_scripts(ws, repo)))
            if ep == 'script':
                return self._send(read_script(ws, repo, q.get('name') or SCRIPT))
            if ep == 'build':
                return self._send(builds.status(repo, q.get('from', 0), q.get('name') or SCRIPT))
            if ep == 'deep':
                return self._send(ws.cached(('deep', repo), lambda: deep_scan(ws, repo), force))
            if ep == 'file':
                return self._send(file_view(ws, repo, q.get('f', '')))
            if ep == 'lines':
                return self._send(file_lines(ws, repo, q.get('f', ''), q.get('from', 1), q.get('n', 2000)))
            if ep == 'ls':
                return self._send(list_dir(ws, repo, q.get('dir', '')))
            if ep == 'search':
                return self._send(search(ws, repo, q.get('base', ''), q.get('q', ''), q.get('mode', 'name')))
            self._send({'error': 'unknown api: ' + ep}, code=404)

        def do_POST(self):
            # doc het body truoc khi tra loi: dong socket con du lieu chua doc -> client nhan RST thay vi loi
            raw = self.rfile.read(min(int(self.headers.get('Content-Length') or 0), 1 << 20))
            try:
                denied = write_denied(self)
                if denied:
                    return self._send({'error': denied}, code=403)
                if not self.headers.get('Content-Type', '').startswith('application/json'):
                    return self._send({'error': 'expected JSON'}, code=415)
                body = json.loads(raw or b'{}')
                repo, ep = body.get('repo', ''), urlparse(self.path).path[len('/api/'):]
                if repo not in ws.by_path:
                    return self._send({'error': 'no such repo: ' + repo}, code=404)
                if ep == 'script-create':
                    return self._send(create_script(ws, repo, bool(body.get('overwrite'))))
                if ep == 'script-empty-create':
                    return self._send(create_empty_script(ws, repo))
                if ep == 'script-delete':
                    result = builds.delete_script(repo, body.get('name') or SCRIPT)
                    return self._send(result, code=409 if result.get('error') else 200)
                if ep == 'script-save':
                    return self._send(save_script(ws, repo, body.get('text', ''), body.get('name') or SCRIPT))
                if ep == 'build-start':
                    return self._send(builds.start(repo, body.get('name') or SCRIPT))
                if ep == 'build-stop':
                    return self._send(builds.stop(repo, script=body.get('name') or SCRIPT))
                if ep == 'build-clear':
                    return self._send(builds.clear(repo, body.get('name') or SCRIPT))
                self._send({'error': 'unknown api: ' + ep}, code=404)
            except ConnectionError:
                pass
            except ValueError as e:
                self._send({'error': str(e)}, code=400)
            except Exception as e:  # noqa: BLE001 - tra loi loi cho UI
                self._send({'error': '%s: %s' % (type(e).__name__, e)}, code=500)

    return Handler


class Server(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        # Client ngat ket noi giua chung (huy request, dong tab, reset) la binh thuong, khong in traceback.
        if isinstance(sys.exc_info()[1], ConnectionError):
            return
        super().handle_error(request, client_address)


def start_server(ws, host='127.0.0.1', port=6969, tries=20):
    """Mo server o cong trong dau tien trong [port, port+tries)."""
    builds = Builds(ws)
    handler = make_handler(ws, builds)
    for p in range(port, port + tries):
        try:
            srv = Server((host, p), handler)
        except OSError:
            continue
        srv.builds = builds                     # cli dung moi build khi server dung
        return srv
    raise SystemExit('cannot open any port in %d..%d' % (port, port + tries - 1))

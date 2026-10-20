"""Che do PC:  python repomap.pyz --local   (hoac nhap dup repomap-pc.cmd tren Windows)

Mo giao dien web tai http://127.0.0.1:6960/ (web/local.html) gom 2 tab:
  Connect        cau hinh ssh (host/user/port + port server = port PC, mac dinh 6969), Quick setup tu lenh ssh,
                 Push to server (scp len ~/.bin), nut Connect: mo cua so ssh tunnel (-N -L) roi tu mo
                 http://127.0.0.1:<port>/ khi server tra loi
  Saved servers  toi da 5 preset
ssh/scp chay trong cua so console rieng de nhap password. Server nay tu thoat khi khong con trang nao mo (IDLE giay).

  GET  /api/local/state        -> cau hinh, preset, trang thai tunnel / deploy
  POST /api/local/config       {host, user, port, srv}          -> luu cau hinh dang dung
  POST /api/local/quick        {text}                           -> doc lenh ssh -> {host, user, port, srv}
  POST /api/local/preset       {name, host, user, port, srv}    -> luu preset (trung ten = ghi de)
  POST /api/local/preset-del   {name}
  POST /api/local/connect      {host, user, port, srv}
  POST /api/local/deploy       {host, user, port, srv}
"""
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
import zipapp
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse
from urllib.request import urlopen

from . import __version__
from .server import Server, load_asset
from .settings import (DEFAULT_PORT, Settings, app_archive, check_port, default_path, parse_ssh_command, port_busy,
                       render)

UI_PORT = 6960            # port cua giao dien PC (khac port tunnel 6969)
MAX_PRESETS = 5
IDLE = 180                # giay khong co request nao (trang da dong) -> thoat
WAIT_TUNNEL = 600         # giay cho tunnel / server repomap tra loi
REMOTE_DIR = '.bin'       # ~/.bin tren server
WINDOWS = os.name == 'nt'
HOST_RE, USER_RE = re.compile(r'^[A-Za-z0-9.-]+$'), re.compile(r'^[\w.-]+$')


# ---------------------------------------------------------------- cau hinh / preset

def state_path():
    return os.path.join(os.path.dirname(default_path()), 'local.json')


def load_state():
    try:
        with open(state_path(), encoding='utf-8') as f:
            st = json.load(f)
    except (OSError, ValueError):
        st = {}
    return {'config': st.get('config') or {}, 'presets': st.get('presets') or []}


def save_state(st):
    path = state_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(st, f, ensure_ascii=False, indent=1)


def check_cfg(d):
    """{host, user, port, srv} hop le; ValueError (thong bao cho UI) neu sai."""
    host, user = str(d.get('host', '')).strip(), str(d.get('user', '')).strip()
    if not HOST_RE.match(host):
        raise ValueError('SSH host is missing or invalid')
    if not USER_RE.match(user):
        raise ValueError('SSH user is missing or invalid')
    return {'host': host, 'user': user, 'port': _port(d, 'port', 22, 'SSH port'),
            'srv': _port(d, 'srv', DEFAULT_PORT, 'Server & local port')}


def _port(d, key, default, label):
    try:
        return check_port(d.get(key) or default)
    except ValueError as e:
        raise ValueError('%s: %s' % (label, e))


def quick(text):
    user, host, port = parse_ssh_command(text)
    return {'host': host, 'user': user, 'port': port, 'srv': DEFAULT_PORT}


def server_ini(cfg):
    """repomap.ini dat canh repomap.pyz tren server: lenh tunnel in ra khop voi PC."""
    return render(Settings(port=cfg['srv'], ssh_host=cfg['host'], ssh_user=cfg['user'], ssh_port=cfg['port']))


def build_pyz(out):
    """Dong goi package dang chay thanh 1 file .pyz (dung khi chay tu source)."""
    pkg = os.path.dirname(os.path.abspath(__file__))
    with tempfile.TemporaryDirectory() as tmp:
        shutil.copytree(pkg, os.path.join(tmp, 'repomap'), ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        zipapp.create_archive(tmp, out, interpreter='/usr/bin/env python3', main='repomap.cli:main', compressed=True)
    return out


def ssh_args(cfg):
    return ['-p', str(cfg['port']), '-o', 'ConnectTimeout=15', '%s@%s' % (cfg['user'], cfg['host'])]


def tunnel_argv(cfg):
    return (['ssh', '-N', '-L', '%d:127.0.0.1:%d' % (cfg['srv'], cfg['srv']), '-o', 'ExitOnForwardFailure=yes',
             '-o', 'ServerAliveInterval=30'] + ssh_args(cfg))


def deploy_argv(cfg, folder):
    """scp -r <tmp>/.bin user@host:  -> tao ~/.bin neu chua co, co roi thi chep de vao (1 lan nhap password)."""
    a = ssh_args(cfg)
    return ['scp', '-r', '-P', a[1]] + a[2:4] + [folder, a[4] + ':']


def quote(argv):
    return ' '.join('"%s"' % a if ' ' in a else a for a in argv)


# ---------------------------------------------------------------- cua so console (ssh / scp)

def open_console(title, argv, done_text, folder):
    """Chay argv trong cua so cmd moi (Windows); cua so dung lai khi xong de doc loi. Tra ve Popen."""
    script = os.path.join(folder, 'run.cmd')
    with open(script, 'w', encoding='utf-8', newline='') as f:
        f.write('@echo off\r\nchcp 65001 >nul\r\ntitle %s\r\necho %s\r\necho.\r\n%s\r\nset RC=%%errorlevel%%\r\n'
                'echo.\r\nif "%%RC%%"=="0" (echo %s) else (echo Failed, exit code %%RC%%.)\r\npause\r\nexit /b %%RC%%\r\n'
                % (title, title, quote(argv), done_text))
    return subprocess.Popen(['cmd', '/c', script], creationflags=subprocess.CREATE_NEW_CONSOLE)


def kill_tree(proc):
    if proc and proc.poll() is None:
        subprocess.run(['taskkill', '/F', '/T', '/PID', str(proc.pid)], capture_output=True)


def repomap_answers(port):
    """Server repomap co tra loi qua tunnel khong (header Server: repomap/...)."""
    try:
        with urlopen('http://127.0.0.1:%d/style.css' % port, timeout=4) as r:
            return r.headers.get('Server', '').startswith('repomap')
    except Exception:  # noqa: BLE001 - tunnel chua len / server chua chay
        return False


class Jobs:
    """Trang thai tunnel va deploy cho UI (poll /api/local/state)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.tunnel = {'state': 'idle'}
        self.deploy = {'state': 'idle'}
        self.tunnel_proc = self.deploy_proc = None

    def snapshot(self):
        with self.lock:
            return dict(self.tunnel), dict(self.deploy)

    def set(self, which, **kw):
        with self.lock:
            setattr(self, which, kw)

    # -------- tunnel
    def connect(self, cfg, open_browser=True):
        port = cfg['srv']
        url = 'http://127.0.0.1:%d/' % port
        cmd = quote(tunnel_argv(cfg))
        if self.tunnel_proc and self.tunnel_proc.poll() is None:         # tunnel cu cua minh -> dong, mo lai
            kill_tree(self.tunnel_proc)
            for _ in range(20):
                if not port_busy(port):
                    break
                time.sleep(0.1)
        if port_busy(port):
            raise ValueError('Port %d on this PC is already in use (an old tunnel window, or another program). '
                             'Close it, or change "Server & local port".' % port)
        if not WINDOWS:
            self.set('tunnel', state='manual', cmd=cmd, url=url)
            return
        folder = tempfile.mkdtemp(prefix='repomap-')
        proc = open_console('repomap tunnel to %s - keep this window open' % cfg['host'], tunnel_argv(cfg),
                            'Tunnel closed.', folder)
        self.tunnel_proc = proc
        self.set('tunnel', state='waiting', cmd=cmd, url=url)
        threading.Thread(target=self._watch_tunnel, args=(proc, port, url, folder, open_browser), daemon=True).start()

    def _watch_tunnel(self, proc, port, url, folder, open_browser):
        cmd, opened, end = self.tunnel.get('cmd'), False, time.time() + WAIT_TUNNEL
        try:
            while proc.poll() is None:
                if self.tunnel_proc is not proc:
                    return
                if port_busy(port):
                    if repomap_answers(port):
                        if not opened and open_browser:
                            webbrowser.open(url)
                        opened = True
                        self.set('tunnel', state='up', cmd=cmd, url=url)
                    elif time.time() < end:
                        self.set('tunnel', state='noserver', cmd=cmd, url=url)
                    time.sleep(3 if opened else 2)
                else:
                    time.sleep(0.5)
            if self.tunnel_proc is proc:
                self.set('tunnel', state='closed' if opened else 'failed', cmd=cmd, url=url, code=proc.returncode)
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    # -------- deploy
    def push(self, cfg):
        if self.deploy_proc and self.deploy_proc.poll() is None:
            raise ValueError('A copy is already running, see its window.')
        folder = tempfile.mkdtemp(prefix='repomap-')
        target = os.path.join(folder, REMOTE_DIR)
        os.makedirs(target)
        archive = app_archive()
        if archive:
            shutil.copyfile(archive, os.path.join(target, 'repomap.pyz'))
        else:
            build_pyz(os.path.join(target, 'repomap.pyz'))
        with open(os.path.join(target, 'repomap.ini'), 'w', encoding='utf-8', newline='\n') as f:
            f.write(server_ini(cfg))
        argv = deploy_argv(cfg, target)
        if not WINDOWS:
            self.set('deploy', state='manual', cmd=quote(argv))
            return
        proc = open_console('repomap: copying to %s:~/%s' % (cfg['host'], REMOTE_DIR), argv,
                            'Done. On the server run:  python3 ~/%s/repomap.pyz' % REMOTE_DIR, folder)
        self.deploy_proc = proc
        self.set('deploy', state='running', cmd=quote(argv))
        threading.Thread(target=self._watch_deploy, args=(proc, folder, quote(argv)), daemon=True).start()

    def _watch_deploy(self, proc, folder, cmd):
        proc.wait()
        shutil.rmtree(folder, ignore_errors=True)
        self.set('deploy', state='done' if proc.returncode == 0 else 'failed', cmd=cmd, code=proc.returncode)


# ---------------------------------------------------------------- HTTP

def make_handler(jobs, srv_port, open_browser):
    seen = [time.time()]

    class Handler(BaseHTTPRequestHandler):
        server_version = 'repomap-local/' + __version__

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

        def _local(self):
            """Chi nhan request tu chinh trang nay (chan trang web khac / DNS rebinding goi vao)."""
            ok = {'127.0.0.1:%d' % srv_port, 'localhost:%d' % srv_port}
            origin = self.headers.get('Origin')
            return self.headers.get('Host') in ok and (origin is None or origin.split('//', 1)[-1] in ok)

        def do_GET(self):
            seen[0] = time.time()
            if not self._local():
                return self._send({'error': 'forbidden'}, code=403)
            path = urlparse(self.path).path
            if path == '/api/local/state':
                return self._send(state(jobs))
            if path == '/api/local/ping':
                return self._send({'app': 'repomap-local'})
            name = 'local.html' if path in ('', '/') else path
            data = load_asset(name)
            if data is None:
                return self._send({'error': 'not found'}, code=404)
            ctype = 'text/javascript' if name.endswith('.js') else mimetypes.guess_type(name)[0] or 'text/plain'
            self._send(data, ctype + '; charset=utf-8')

        def do_POST(self):
            seen[0] = time.time()
            # doc het body truoc khi tra loi: dong socket con du lieu chua doc -> Windows gui RST thay vi 403
            raw = self.rfile.read(min(int(self.headers.get('Content-Length') or 0), 65536))
            if not self._local() or not self.headers.get('Content-Type', '').startswith('application/json'):
                return self._send({'error': 'forbidden'}, code=403)
            try:
                body = json.loads(raw or b'{}')
                self._send(post(jobs, urlparse(self.path).path[len('/api/local/'):], body, open_browser))
            except ValueError as e:
                self._send({'error': str(e)}, code=400)
            except Exception as e:  # noqa: BLE001 - bao loi cho UI
                self._send({'error': '%s: %s' % (type(e).__name__, e)}, code=500)

    return Handler, seen


def state(jobs):
    st = load_state()
    tunnel, deploy = jobs.snapshot()
    archive = app_archive()
    return {'version': __version__, 'config': st['config'], 'presets': st['presets'], 'max': MAX_PRESETS,
            'defaultPort': DEFAULT_PORT, 'remoteDir': REMOTE_DIR, 'windows': WINDOWS,
            'pyz': os.path.basename(archive) if archive else None, 'tunnel': tunnel, 'deploy': deploy}


def post(jobs, ep, body, open_browser=True):
    st = load_state()
    if ep == 'quick':
        return quick(body.get('text', ''))
    if ep == 'config':
        st['config'] = {k: body.get(k, '') for k in ('host', 'user', 'port', 'srv')}
        save_state(st)
        return {'ok': True}
    if ep == 'preset':
        name = str(body.get('name', '')).strip()[:40]
        if not name:
            raise ValueError('Enter a server name')
        cfg = check_cfg(body)
        rest = [p for p in st['presets'] if p['name'] != name]
        if len(rest) >= MAX_PRESETS:
            raise ValueError('You can keep up to %d servers. Delete one in "Saved servers" first.' % MAX_PRESETS)
        st['presets'] = rest + [dict(cfg, name=name)]
        save_state(st)
        return {'ok': True}
    if ep == 'preset-del':
        st['presets'] = [p for p in st['presets'] if p['name'] != body.get('name')]
        save_state(st)
        return {'ok': True}
    if ep == 'connect':
        jobs.connect(check_cfg(body), open_browser)
        return {'ok': True}
    if ep == 'deploy':
        jobs.push(check_cfg(body))
        return {'ok': True}
    raise ValueError('unknown api: ' + ep)


def already_running(port):
    try:
        with urlopen('http://127.0.0.1:%d/api/local/ping' % port, timeout=2) as r:
            return json.load(r).get('app') == 'repomap-local'
    except Exception:  # noqa: BLE001
        return False


def run_local(port=None, open_browser=True):
    log = lambda *x: print(*x, file=sys.stderr)  # noqa: E731
    first = port or UI_PORT
    if already_running(first):                                # da mo roi -> chi mo lai trang
        log('repomap PC is already running: http://127.0.0.1:%d/' % first)
        if open_browser:
            webbrowser.open('http://127.0.0.1:%d/' % first)
        return
    for p in range(first, first + 20):
        if port_busy(p):
            log('  port %d is already in use, trying %d' % (p, p + 1))
            continue
        jobs = Jobs()
        handler, seen = make_handler(jobs, p, open_browser)
        try:
            srv = Server(('127.0.0.1', p), handler)
        except OSError:
            continue
        break
    else:
        raise SystemExit('cannot open any port in %d..%d' % (first, first + 19))
    url = 'http://127.0.0.1:%d/' % p
    log('repomap %s (PC)  %s   closes by itself %d s after the page is closed (Ctrl+C to stop)' % (__version__, url, IDLE))

    def watchdog():
        while True:
            time.sleep(10)
            if time.time() - seen[0] > IDLE:
                srv.shutdown()
                return
    threading.Thread(target=watchdog, daemon=True).start()
    if open_browser:
        threading.Timer(0.3, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()

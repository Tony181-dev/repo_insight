"""Che do PC:  python repomap.pyz --local   (hoac nhap dup repomap-pc.cmd tren Windows)

Mo giao dien web tai http://127.0.0.1:6960/ (web/local.html) gom 2 tab:
  Connect        cau hinh ssh (host/user/port + port server = port PC, mac dinh 6969), Quick setup tu lenh ssh,
                 Server folder (noi dat repomap.pyz + repomap.ini, mac dinh ~/.bin), Push to server (scp),
                 Connect: 1 cua so ssh vua mo tunnel (-L) vua chay repomap.pyz tren server (= menu "1. Run scan"),
                 tu mo http://127.0.0.1:<port>/ khi server tra loi; Cancel / Disconnect / Check connection
  Saved servers  toi da 5 preset
ssh/scp chay trong cua so console rieng de nhap password. Server nay tu thoat khi khong con trang nao mo (IDLE giay).

Moi luc chi 1 ket noi: Connect khi dang ket noi thi bao loi (Disconnect truoc); tunnel cu (cua so ssh khac) dang tra
loi o port do thi dung lai, khong mo them. Tren server, repomap --connect thay repomap cua chinh user dang chay o port
do thi dung chung (khong chay them ban thu 2), cua user khac / chuong trinh khac thi bao loi.

  GET  /api/local/state        -> cau hinh, preset, trang thai tunnel / deploy
  POST /api/local/config       {host, user, port, srv, dir}     -> luu cau hinh dang dung
  POST /api/local/quick        {text}                           -> doc lenh ssh -> {host, user, port, srv}
  POST /api/local/preset       {name, host, user, port, srv, dir} -> luu preset (trung ten = ghi de)
  POST /api/local/preset-del   {name}
  POST /api/local/connect      {host, user, port, srv, dir}
  POST /api/local/disconnect   {}                               -> dong cua so ssh (huy luc dang noi / ngat ket noi)
  POST /api/local/check        {srv}                            -> hoi repomap qua tunnel: con song khong, workspace
  POST /api/local/deploy       {host, user, port, srv, dir}
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
DIR_RE = re.compile(r'^[\w.~/+-]+$', re.A)          # khong dau cach / ky tu shell: lenh ssh chay qua shell server
CFG_KEYS = ('host', 'user', 'port', 'srv', 'dir')
# ma thoat cua lenh tren server (xem remote_command / cli --connect)
EXIT_NO_PYZ, EXIT_PORT_TAKEN, EXIT_NO_PYTHON = 3, 4, 127


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
    """{host, user, port, srv, dir} hop le; ValueError (thong bao cho UI) neu sai."""
    host, user = str(d.get('host', '')).strip(), str(d.get('user', '')).strip()
    if not HOST_RE.match(host):
        raise ValueError('SSH host is missing or invalid')
    if not USER_RE.match(user):
        raise ValueError('SSH user is missing or invalid')
    remote_dir(d.get('dir'))
    return {'host': host, 'user': user, 'port': _port(d, 'port', 22, 'SSH port'),
            'srv': _port(d, 'srv', DEFAULT_PORT, 'Server & local port'), 'dir': str(d.get('dir') or '').strip()}


def _port(d, key, default, label):
    try:
        return check_port(d.get(key) or default)
    except ValueError as e:
        raise ValueError('%s: %s' % (label, e))


def remote_dir(value):
    """O "Server folder" -> noi dat repomap.pyz + repomap.ini tren server:
      ''                       ~/.bin (tao neu chua co)
      ten thu muc (vd tools)   ~/tools, cung cap voi ~/.bin; tao neu chua co, co roi thi chep de vao
      duong dan (/opt/x, ~/a/b, a/b)  phai co san tren server, khong co thi Push bao loi
    -> {'rel': duong dan cho scp / ssh (tuong doi = tinh tu ~), 'path': True neu la duong dan, 'show': de hien}."""
    v = str(value or '').strip()
    if len(v) > 1:
        v = v.rstrip('/')
    if not v:
        return {'rel': REMOTE_DIR, 'path': False, 'show': '~/' + REMOTE_DIR}
    if not DIR_RE.match(v) or v.startswith('-') or '..' in v.split('/') or len(v) > 200:
        raise ValueError('Server folder: use a folder name or a path made of letters, digits and . _ - / ~ '
                         '(no spaces, no "..")')
    if '/' not in v and v != '~':
        if v == '.':
            raise ValueError('Server folder: "." is not a folder name')
        return {'rel': v, 'path': False, 'show': '~/' + v}
    if v == '~' or v.startswith('~/'):
        rel = v[2:] or '.'
    elif v.startswith('~'):
        raise ValueError('Server folder: ~user paths are not supported, write the full path')
    else:
        rel = v
    return {'rel': rel, 'path': True, 'show': v if v.startswith(('/', '~')) else '~/' + v}


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


def remote_command(cfg, d):
    """Lenh chay tren server qua ssh: repomap.pyz da Push -> scan ngay (= menu "1. Run scan"), dung dung port tunnel.
    Viet duoc cho ca sh/bash va csh/tcsh (shell mac dinh cua user)."""
    pyz = d['rel'] + '/repomap.pyz'
    return 'test -f %s || exit %d; exec python3 %s --connect --port %d' % (pyz, EXIT_NO_PYZ, pyz, cfg['srv'])


def tunnel_argv(cfg, d=None):
    """ssh -t -L port:127.0.0.1:port ... '<remote_command>' (-t: tat cua so ssh = repomap tren server nhan SIGHUP
    va dung theo). d=None: chi mo tunnel (-N), repomap da chay san tren server."""
    fwd = ['-L', '%d:127.0.0.1:%d' % (cfg['srv'], cfg['srv']), '-o', 'ExitOnForwardFailure=yes',
           '-o', 'ServerAliveInterval=30']
    if d is None:
        return ['ssh', '-N'] + fwd + ssh_args(cfg)
    return ['ssh', '-t'] + fwd + ssh_args(cfg) + [remote_command(cfg, d)]


def deploy_argv(cfg, src, path=None):
    """path=None: scp -r <tmp>/<ten> user@host:  -> tao ~/<ten> neu chua co, co roi thi chep de vao.
    path: scp <file...> user@host:<path>/  -> thu muc phai co san (khong co: scp bao loi, khong tu tao).
    Ca 2 cach chi nhap password 1 lan."""
    a = ssh_args(cfg)
    if path is None:
        return ['scp', '-r', '-P', a[1]] + a[2:4] + [src, a[4] + ':']
    return ['scp', '-P', a[1]] + a[2:4] + list(src) + ['%s:%s/' % (a[4], path)]


def quote(argv):
    return ' '.join('"%s"' % a.replace('"', '\\"') if (' ' in a or ';' in a or '|' in a) else a for a in argv)


# ---------------------------------------------------------------- cua so console (ssh / scp)

def open_console(title, argv, done_text, folder):
    """Chay argv trong cua so cmd moi (Windows); cua so dung lai khi xong de doc loi. Ma thoat ghi vao rc.txt ngay khi
    lenh xong (truoc khi cho bam phim) de trang thai tren giao dien cap nhat luon. Tra ve Popen."""
    script = os.path.join(folder, 'run.cmd')
    with open(script, 'w', encoding='utf-8', newline='') as f:
        f.write('@echo off\r\nchcp 65001 >nul\r\ntitle %s\r\necho %s\r\necho.\r\n%s\r\nset RC=%%errorlevel%%\r\n'
                '>"%%~dp0rc.txt" echo %%RC%%\r\n'
                'echo.\r\nif "%%RC%%"=="0" (echo %s) else (echo Failed, exit code %%RC%%.)\r\npause\r\nexit /b %%RC%%\r\n'
                % (title, title, quote(argv), done_text))
    return subprocess.Popen(['cmd', '/c', script], creationflags=subprocess.CREATE_NEW_CONSOLE)


def exit_code(folder):
    """Ma thoat da ghi trong rc.txt (None: lenh chua xong)."""
    try:
        with open(os.path.join(folder, 'rc.txt'), encoding='ascii', errors='replace') as f:
            return int(f.read().strip() or 0)
    except (OSError, ValueError):
        return None


def kill_tree(proc):
    if proc and proc.poll() is None:
        subprocess.run(['taskkill', '/F', '/T', '/PID', str(proc.pid)], capture_output=True)


def repomap_info(port, timeout=4):
    """Hoi server repomap qua tunnel: dict (version, workspace, so repo, user, so script dang chay) hoac None.
    Server ban cu chua co /api/ping: van nhan ra qua header Server: repomap/..."""
    try:
        with urlopen('http://127.0.0.1:%d/api/ping' % port, timeout=timeout) as r:
            if not r.headers.get('Server', '').startswith('repomap/'):
                return None
            return json.load(r)
    except ValueError:
        return {'app': 'repomap'}
    except Exception:  # noqa: BLE001 - HTTP loi (404 o ban cu), tunnel chua len, server chua chay
        return {'app': 'repomap'} if repomap_answers(port, timeout) else None


def repomap_answers(port, timeout=4):
    """Server repomap co tra loi qua tunnel khong (header Server: repomap/...)."""
    try:
        with urlopen('http://127.0.0.1:%d/style.css' % port, timeout=timeout) as r:
            return r.headers.get('Server', '').startswith('repomap')
    except Exception:  # noqa: BLE001 - tunnel chua len / server chua chay
        return False


class Jobs:
    """Trang thai tunnel va deploy cho UI (poll /api/local/state).

    tunnel.state: idle | connecting (cua so ssh mo, cho password) | starting (tunnel len, repomap tren server dang
      nap workspace) | up | external (tunnel cua cua so khac dang tra loi, dung chung) | closed | cancelled |
      failed (code) | nopyz (chua Push) | taken (port tren server bi chuong trinh / user khac chiem) | manual"""

    ACTIVE = ('connecting', 'starting', 'up')

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

    def _update(self, proc, **kw):
        """Chi cap nhat neu proc van la tunnel hien tai (Disconnect / Connect moi thi bo qua tin cu)."""
        with self.lock:
            if self.tunnel_proc is proc:
                self.tunnel = dict(self.tunnel, **kw)

    # -------- tunnel
    def connect(self, cfg, open_browser=True):
        d = remote_dir(cfg.get('dir'))
        port = cfg['srv']
        url = 'http://127.0.0.1:%d/' % port
        argv = tunnel_argv(cfg, d)
        with self.lock:
            if self.tunnel_proc and self.tunnel_proc.poll() is None:
                if self.tunnel.get('state') in self.ACTIVE:
                    raise ValueError('Already connected or connecting to %s. Press Disconnect first.'
                                     % self.tunnel.get('host', 'the server'))
                kill_tree(self.tunnel_proc)               # cua so cu chi con dung cho bam phim: dong luon
            self.tunnel_proc = None
        if port_busy(port):
            if repomap_answers(port):                     # tunnel cua cua so khac van song: dung lai, khong mo them
                self.set('tunnel', state='external', url=url, host=cfg['host'])
                if open_browser:
                    webbrowser.open(url)
                return
            raise ValueError('Port %d on this PC is already in use (an old tunnel window, or another program). '
                             'Close it, or change "Server & local port".' % port)
        cmd = quote(argv)
        if not WINDOWS:
            self.set('tunnel', state='manual', cmd=cmd, url=url, host=cfg['host'])
            return
        folder = tempfile.mkdtemp(prefix='repomap-')
        proc = open_console('repomap on %s - keep this window open' % cfg['host'], argv, 'repomap stopped.', folder)
        with self.lock:
            self.tunnel_proc = proc
            self.tunnel = {'state': 'connecting', 'cmd': cmd, 'url': url, 'host': cfg['host'], 'dir': d['show'],
                           'since': time.time()}
        threading.Thread(target=self._watch_tunnel, args=(proc, port, url, folder, open_browser), daemon=True).start()

    def disconnect(self):
        """Cancel (dang noi) / Disconnect (da noi): dong cua so ssh -> repomap tren server dung theo (SIGHUP)."""
        with self.lock:
            proc, state = self.tunnel_proc, self.tunnel.get('state')
            self.tunnel_proc = None
        if proc:
            kill_tree(proc)
        if state in self.ACTIVE or state == 'external':
            self.set('tunnel', **dict(self.tunnel, state='cancelled' if state == 'connecting' else 'closed'))
        return {'ok': True, 'was': state}

    def _watch_tunnel(self, proc, port, url, folder, open_browser):
        opened, end = False, time.time() + WAIT_TUNNEL
        try:
            while proc.poll() is None and exit_code(folder) is None:
                if self.tunnel_proc is not proc:
                    return
                if port_busy(port):
                    if repomap_answers(port):
                        if not opened and open_browser:
                            webbrowser.open(url)
                        opened = True
                        self._update(proc, state='up')
                    elif time.time() < end:
                        self._update(proc, state='starting')
                    time.sleep(3 if opened else 1.5)
                else:
                    time.sleep(0.5)
            code = exit_code(folder)
            if code is None:
                code = proc.returncode
            state = {EXIT_NO_PYZ: 'nopyz', EXIT_PORT_TAKEN: 'taken', EXIT_NO_PYTHON: 'nopython'}.get(code)
            self._update(proc, state=state or ('closed' if opened or code == 0 else 'failed'), code=code)
        finally:
            if proc.poll() is not None:
                shutil.rmtree(folder, ignore_errors=True)

    def check(self, port):
        """Check connection: ai dang tra loi o port nay tren PC (qua tunnel)."""
        t0 = time.time()
        if not port_busy(port):
            return {'ok': False, 'why': 'noport', 'state': self.tunnel.get('state')}
        info = repomap_info(port, timeout=6)
        ms = int((time.time() - t0) * 1000)
        if not info:
            return {'ok': False, 'why': 'noreply', 'ms': ms, 'state': self.tunnel.get('state')}
        return dict(info, ok=True, ms=ms, state=self.tunnel.get('state'))

    # -------- deploy
    def push(self, cfg):
        d = remote_dir(cfg.get('dir'))
        if self.deploy_proc and self.deploy_proc.poll() is None:
            raise ValueError('A copy is already running, see its window.')
        folder = tempfile.mkdtemp(prefix='repomap-')
        target = os.path.join(folder, 'files' if d['path'] else d['rel'])
        os.makedirs(target)
        pyz, ini = os.path.join(target, 'repomap.pyz'), os.path.join(target, 'repomap.ini')
        archive = app_archive()
        if archive:
            shutil.copyfile(archive, pyz)
        else:
            build_pyz(pyz)
        with open(ini, 'w', encoding='utf-8', newline='\n') as f:
            f.write(server_ini(cfg))
        argv = deploy_argv(cfg, [pyz, ini], d['rel']) if d['path'] else deploy_argv(cfg, target)
        info = {'cmd': quote(argv), 'dir': d['show'], 'path': d['path']}
        if not WINDOWS:
            self.set('deploy', state='manual', **info)
            return
        proc = open_console('repomap: copying to %s:%s' % (cfg['host'], d['show']), argv,
                            'Done. Now press Connect on the repomap PC page.', folder)
        self.deploy_proc = proc
        self.set('deploy', state='running', **info)
        threading.Thread(target=self._watch_deploy, args=(proc, folder, info), daemon=True).start()

    def _watch_deploy(self, proc, folder, info):
        while proc.poll() is None and exit_code(folder) is None:
            time.sleep(0.5)
        code = exit_code(folder)
        code = proc.returncode if code is None else code
        self.set('deploy', state='done' if code == 0 else 'failed', code=code, **info)
        proc.wait()
        shutil.rmtree(folder, ignore_errors=True)


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
        st['config'] = {k: body.get(k, '') for k in CFG_KEYS}
        save_state(st)
        return {'ok': True}
    if ep == 'dir':
        return remote_dir(body.get('dir'))
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
    if ep == 'disconnect':
        return jobs.disconnect()
    if ep == 'check':
        return jobs.check(_port(body, 'srv', DEFAULT_PORT, 'Server & local port'))
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

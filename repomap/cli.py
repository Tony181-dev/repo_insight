"""Dong lenh:
  repomap [WORKSPACE]                 menu: 1. Run scan / 2. Configuration / 3. Quick setup / 0. Exit
  repomap --local                     tren PC: Connect (co Deploy) / Saved servers (xem local.py)
  repomap ROOT --connect --port P     do nut Connect tren PC chay qua ssh: scan ngay ROOT (= "1. Run scan"; o
                                      Workspace root tren trang PC, trong = ~), dung dung port P; repomap cua minh
                                      dang chay o P cung workspace thi dung chung
  repomap WORKSPACE --run             bo qua menu, chay ngay (dung trong script)
  repomap WORKSPACE --json REPO       in JSON 1 repo roi thoat
"""
import argparse
import dataclasses
import getpass
import json
import os
import signal
import sys
import threading
import time
import webbrowser
from urllib.request import urlopen

from . import __version__
from .deepscan import deep_scan
from .insight import insight
from .menu import ask, check_workspace, run_menu
from .server import start_server
from .settings import load_settings, port_busy, save_settings, write_template
from .workspace import Workspace


def parse_args(argv=None):
    ap = argparse.ArgumentParser(prog='repomap',
                                 description='Map of a repo-sync workspace (Yocto/BitBake, Android, QNX, Linux).')
    ap.add_argument('root', nargs='?', help='workspace root, the folder that contains .repo/ '
                                            '(default: the last one used)')
    ap.add_argument('--run', action='store_true', help='skip the menu and start scanning right away')
    ap.add_argument('--config', metavar='FILE', help='INI config file (default: ~/.config/repomap/config.ini)')
    ap.add_argument('--init-config', nargs='?', const='', metavar='FILE',
                    help='write a commented config file and exit')
    ap.add_argument('--port', type=int, help='server port (overrides [server] port)')
    ap.add_argument('--host', help='listen address (overrides [server] host); 0.0.0.0 = whole network, be careful')
    ap.add_argument('--no-browser', action='store_true', help='do not open a browser')
    ap.add_argument('--workers', type=int, help='threads used to classify repos')
    ap.add_argument('--local', action='store_true',
                    help='on your PC: open the Connect / Saved servers page (Deploy is in Connect; needs ssh and scp)')
    ap.add_argument('--connect', action='store_true',
                    help='used by the Connect button of the PC page (over ssh): scan ROOT (else the last workspace) '
                         'right away on exactly --port; if your own repomap already runs there on the same '
                         'workspace, use it instead of a second one')
    ap.add_argument('--json', metavar='REPO', help='print insight + deep scan of one repo as JSON and exit')
    ap.add_argument('--version', action='version', version='repomap ' + __version__)
    return ap.parse_args(argv)


def apply_overrides(a, saved):
    """Ban sao cua cau hinh da luu + tham so dong lenh (dong lenh thang). Khong sua `saved`,
    de menu Configuration chi luu nhung gi user nhap, khong luu cac co tam thoi."""
    s = dataclasses.replace(saved)
    for attr in ('host', 'port', 'workers'):
        if getattr(a, attr) is not None:
            setattr(s, attr, getattr(a, attr))
    if a.no_browser:
        s.open_browser = 'no'
    return s


def resolve_settings(a):
    return apply_overrides(a, load_settings(a.config))


def interactive(a):
    return not (a.run or a.json) and sys.stdin.isatty() and sys.stdout.isatty()


def serve(ws, s, connect=False):
    log = lambda *x: print(*x, file=sys.stderr)  # noqa: E731
    srv = start_server(ws, s.host, s.port, s.port_tries)
    port = srv.server_address[1]
    url = 'http://%s:%d/' % ('127.0.0.1' if s.host in ('0.0.0.0', '') else s.host, port)
    log('  config : %s' % (s.source or 'defaults (menu "2. Configuration" saves your settings)'))
    if port != s.port:
        log('  NOTE   : port %d is already in use on this machine, using port %d instead.' % (s.port, port))
        log('           On your PC, set the port to %d (or stop whatever uses %d).' % (port, s.port))
    log('  UI     : %s   (Ctrl+C to stop)' % url)
    if connect:
        log('  Connected from the repomap PC page: it opens http://127.0.0.1:%d/ by itself.' % port)
        log('  Keep this window open while you use it. Closing it (or Disconnect) stops repomap here.')
    else:
        log('  From your PC: run this and keep it open, then browse http://127.0.0.1:%d/' % (s.ssh_local_port or port))
        log('    %s' % s.tunnel_hint(port))
    if s.should_open_browser():
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    stop_on_signals()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        log('\nbye')
    finally:
        stopped = srv.builds.stop_all()            # script chay tu giao dien dung cung repomap
        if stopped:
            log('  stopped %d running script(s): %s' % (len(stopped), ', '.join(
                '%s/%s' % (r or '.', s) for r, s in stopped)))
        srv.server_close()


def stop_on_signals():
    """SIGTERM / SIGHUP (vd phien ssh chay repomap bi dut) xu ly nhu Ctrl+C: dung build roi thoat."""
    def stop(*_):
        raise KeyboardInterrupt
    for name in ('SIGTERM', 'SIGHUP'):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), stop)


def main(argv=None):
    a = parse_args(argv)
    if a.init_config is not None:
        print('written: ' + write_template(a.init_config or None))
        return

    if a.local:
        from .local import run_local
        run_local(a.port, not a.no_browser)
        return

    saved = load_settings(a.config)
    root = a.root
    if a.connect:
        root = connect_workspace(a, saved)
        if attach_running(a.port or saved.port, root):
            return
    elif interactive(a):
        try:
            root = run_menu(saved, root)
        except (KeyboardInterrupt, EOFError):
            print()
            return
        if not root:
            return
    s = apply_overrides(a, saved)
    if a.connect:                          # tunnel tren PC noi dung port nay: khong tu doi sang port khac
        s.port_tries, s.open_browser = 1, 'no'
    root = root or s.workspace
    if not root:
        sys.exit('missing workspace path: repomap /path/to/workspace  (or run it in a terminal to use the menu)')
    root = os.path.abspath(os.path.expanduser(root))
    if not os.path.isdir(root):
        sys.exit('folder not found: ' + root)

    ws = Workspace(root, s.resolved_workers()).load()
    print('repomap %s  %s  -  %d repos (%s, %.1fs)' % (__version__, ws.root, len(ws.repos), ws.mode, ws.took),
          file=sys.stderr)

    if a.json:
        repo = a.json.strip('/')
        if repo not in ws.by_path:
            sys.exit('no such repo: ' + repo)
        json.dump({'insight': insight(ws, repo), 'deep': deep_scan(ws, repo)}, sys.stdout, ensure_ascii=False, indent=1)
        print()
        return

    remember_workspace(a, s, ws.root)
    serve(ws, s, a.connect)


# ---------------------------------------------------------------- --connect (nut Connect tren PC)

def ping(port):
    try:
        with urlopen('http://127.0.0.1:%d/api/ping' % port, timeout=4) as r:
            return json.load(r) if r.headers.get('Server', '').startswith('repomap/') else None
    except Exception:  # noqa: BLE001 - khong phai repomap / ban cu khong co /api/ping
        return None


def _same_dir(a, b):
    return bool(a and b) and os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def attach_running(port, root=None):
    """Port da co repomap cua chinh user nay, cung workspace: khong chay ban thu 2, giu phien ssh (tunnel) cho toi khi
    repomap do dung hoac cua so bi dong. Khac workspace: bao loi, ma 6 (khong am tham cho xem nham workspace).
    Port bi chuong trinh / user khac chiem: bao loi, ma 4. PC hien thong bao theo ma thoat."""
    if not port_busy(port):
        return False
    log = lambda *x: print(*x, file=sys.stderr, flush=True)  # noqa: E731
    info = ping(port)
    if info and info.get('user') == getpass.getuser() and root and not _same_dir(root, info.get('root')):
        log('Your repomap already runs on port %d with workspace %s (pid %s), not %s.'
            % (port, info.get('root'), info.get('pid'), root))
        log('Stop it first (close its window, or kill %s), or use another "Server & local port" on the PC page.'
            % info.get('pid'))
        sys.exit(6)
    if info and info.get('user') == getpass.getuser():
        log('repomap is already running on port %d (workspace %s, pid %s): using it, not starting another one.'
            % (port, info.get('root'), info.get('pid')))
        log('Keep this window open while you use the page; close it (or Disconnect) to disconnect.')
        stop_on_signals()
        try:
            while port_busy(port):
                time.sleep(3)
        except KeyboardInterrupt:
            pass
        return True
    who = ("repomap of user '%s'" % info.get('user')) if info else 'another program'
    log('Port %d on this server is already used by %s.' % (port, who))
    log('On the PC page change "Server & local port", Push again, then Connect.')
    sys.exit(4)


def connect_workspace(a, saved):
    """Workspace cho --connect: tham so (Workspace root tren trang PC, trong = ~) > file cau hinh > lan quet truoc
    (cache); chua co thi hoi ngay trong cua so ssh. Tham so chi ro ma khong co thu muc: bao loi, ma 5."""
    if a.root:
        root = os.path.abspath(os.path.expanduser(a.root))
        if not os.path.isdir(root):
            print('Workspace root not found on this server: %s' % root, file=sys.stderr, flush=True)
            print('Fix "Workspace root" on the PC page (empty = your home folder), then Connect again.',
                  file=sys.stderr, flush=True)
            sys.exit(5)
        return root
    for root in (saved.workspace, last_workspace()):
        if root and os.path.isdir(os.path.expanduser(root)):
            return root
    if not sys.stdin.isatty():
        sys.exit('no workspace yet: run  repomap /path/to/workspace  once on the server')
    print('First time on this server: which workspace should repomap scan?')
    try:
        return ask('Workspace root (the folder that contains .repo/)', '', check_workspace)
    except (KeyboardInterrupt, EOFError):
        sys.exit(1)


def _last_ws_file():
    base = os.environ.get('REPOMAP_CACHE_DIR') or os.path.join(
        os.environ.get('XDG_CACHE_HOME') or os.path.join(os.path.expanduser('~'), '.cache'), 'repomap')
    return os.path.join(base, 'last-workspace')


def last_workspace():
    try:
        with open(_last_ws_file(), encoding='utf-8') as f:
            return f.read().strip()
    except OSError:
        return ''


def remember_workspace(a, s, root):
    """Luu workspace vao file cau hinh (chi truong nay, khong luu cac gia tri tam tu dong lenh), ca vao cache
    (--connect doc lai khi repomap.ini canh repomap.pyz bi Push ghi de)."""
    try:
        os.makedirs(os.path.dirname(_last_ws_file()), exist_ok=True)
        with open(_last_ws_file(), 'w', encoding='utf-8') as f:
            f.write(root + '\n')
    except OSError:
        pass
    if s.workspace == root:
        return
    s.workspace = root
    saved = load_settings(a.config)
    saved.workspace = root
    try:
        s.source = save_settings(saved)
    except OSError as e:
        print('  (could not save config: %s)' % e, file=sys.stderr)


if __name__ == '__main__':
    main()

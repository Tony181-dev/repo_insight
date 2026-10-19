"""Dong lenh:
  repomap [WORKSPACE]                 menu: 1. Run scan / 2. Configuration / 0. Exit
  repomap WORKSPACE --run             bo qua menu, chay ngay (dung trong script)
  repomap WORKSPACE --json REPO       in JSON 1 repo roi thoat
"""
import argparse
import dataclasses
import json
import os
import sys
import threading
import webbrowser

from . import __version__
from .deepscan import deep_scan
from .insight import insight
from .menu import run_menu
from .server import start_server
from .settings import load_settings, save_settings, write_template
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


def serve(ws, s):
    log = lambda *x: print(*x, file=sys.stderr)  # noqa: E731
    srv = start_server(ws, s.host, s.port, s.port_tries)
    port = srv.server_address[1]
    url = 'http://%s:%d/' % ('127.0.0.1' if s.host in ('0.0.0.0', '') else s.host, port)
    log('  config : %s' % (s.source or 'defaults (menu "2. Configuration" saves your settings)'))
    log('  UI     : %s   (Ctrl+C to stop)' % url)
    log('  From your PC: run this and keep it open, then browse http://127.0.0.1:%d/' % (s.ssh_local_port or port))
    log('    %s' % s.tunnel_hint(port))
    if s.should_open_browser():
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        log('\nbye')
    finally:
        srv.server_close()


def main(argv=None):
    a = parse_args(argv)
    if a.init_config is not None:
        print('written: ' + write_template(a.init_config or None))
        return

    saved = load_settings(a.config)
    root = a.root
    if interactive(a):
        try:
            root = run_menu(saved, root)
        except (KeyboardInterrupt, EOFError):
            print()
            return
        if not root:
            return
    s = apply_overrides(a, saved)
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
    serve(ws, s)


def remember_workspace(a, s, root):
    """Luu workspace vao file cau hinh (chi truong nay, khong luu cac gia tri tam tu dong lenh)."""
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

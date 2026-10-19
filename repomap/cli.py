"""Dong lenh: repomap /path/to/workspace [--port N] [--host H] [--config FILE] [--json REPO]"""
import argparse
import json
import os
import sys
import threading
import webbrowser

from . import __version__
from .deepscan import deep_scan
from .insight import insight
from .server import start_server
from .settings import load_settings, write_template
from .workspace import Workspace


def parse_args(argv=None):
    ap = argparse.ArgumentParser(prog='repomap',
                                 description='Ban do workspace repo sync (Yocto/BitBake, Android, QNX, Linux).')
    ap.add_argument('root', nargs='?', help='thu muc goc workspace (noi co .repo/)')
    ap.add_argument('--config', metavar='FILE', help='file cau hinh INI (mac dinh: ~/.config/repomap/config.ini)')
    ap.add_argument('--init-config', nargs='?', const='', metavar='FILE',
                    help='tao file cau hinh mau roi thoat (mac dinh ghi vao ~/.config/repomap/config.ini)')
    ap.add_argument('--port', type=int, help='cong server (ghi de [server] port)')
    ap.add_argument('--host', help='dia chi nghe (ghi de [server] host); 0.0.0.0 = mo cho ca mang, can than')
    ap.add_argument('--no-browser', action='store_true', help='khong tu mo trinh duyet')
    ap.add_argument('--workers', type=int, help='so thread phan loai repo')
    ap.add_argument('--json', metavar='REPO', help='in JSON insight + quet sau cua 1 repo roi thoat')
    ap.add_argument('--version', action='version', version='repomap ' + __version__)
    a = ap.parse_args(argv)
    if a.init_config is None and not a.root:
        ap.error('can duong dan workspace')
    return a


def resolve_settings(a):
    """Gop file cau hinh voi tham so dong lenh (dong lenh thang)."""
    s = load_settings(a.config)
    for attr in ('host', 'port', 'workers'):
        if getattr(a, attr) is not None:
            setattr(s, attr, getattr(a, attr))
    if a.no_browser:
        s.open_browser = 'no'
    return s


def main(argv=None):
    a = parse_args(argv)
    if a.init_config is not None:
        print('da tao: ' + write_template(a.init_config or None))
        return

    s = resolve_settings(a)
    if not os.path.isdir(a.root):
        sys.exit('khong thay thu muc: ' + a.root)
    ws = Workspace(a.root, s.resolved_workers()).load()
    log = lambda *x: print(*x, file=sys.stderr)  # noqa: E731
    log('repomap %s  %s  -  %d repo (%s, %.1fs)' % (__version__, ws.root, len(ws.repos), ws.mode, ws.took))

    if a.json:
        repo = a.json.strip('/')
        if repo not in ws.by_path:
            sys.exit('khong co repo: ' + repo)
        json.dump({'insight': insight(ws, repo), 'deep': deep_scan(ws, repo)}, sys.stdout, ensure_ascii=False, indent=1)
        print()
        return

    srv = start_server(ws, s.host, s.port, s.port_tries)
    port = srv.server_address[1]
    url = 'http://%s:%d/' % ('127.0.0.1' if s.host in ('0.0.0.0', '') else s.host, port)
    log('  cau hinh : %s' % (s.source or 'mac dinh (tao file: repomap --init-config)'))
    log('  giao dien: %s   (Ctrl+C de thoat)' % url)
    log('  tu may khac: %s   roi mo http://127.0.0.1:%d/' % (s.tunnel_hint(port), s.ssh_local_port or port))
    if s.should_open_browser():
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        log('\nbye')
    finally:
        srv.server_close()


if __name__ == '__main__':
    main()

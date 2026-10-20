"""Ai duoc ghi file / chay lenh qua server (build script: luu, chay, dung).

Server mac dinh chi doc. Cac API ghi chi nhan request khi:
  - server nghe 127.0.0.1 (khong mo ra mang)
  - Host la 127.0.0.1 / localhost (chan DNS rebinding), Origin (neu co) cung trang
  - co header X-Repomap: 1 (trang web khac khong gui duoc header nay ma khong qua CORS preflight)
  - tren Linux: ket noi den tu tien trinh cua chinh user dang chay repomap (doc /proc/net/tcp*), nen user
    khac tren may build dung chung khong goi duoc, ke ca qua 127.0.0.1. ssh tunnel tu PC = sshd cua chinh minh.
"""
import os

PROC_NET = ('/proc/net/tcp', '/proc/net/tcp6')


def peer_uid(client_port, server_port, files=PROC_NET):
    """uid cua socket phia client (cong client_port -> server_port); None neu khong doc duoc /proc."""
    readable = False
    for fn in files:
        try:
            with open(fn, encoding='ascii') as f:
                rows = f.read().splitlines()[1:]
        except OSError:
            continue
        readable = True
        for row in rows:
            parts = row.split()
            if len(parts) < 8:
                continue
            try:
                local, remote = int(parts[1].rsplit(':', 1)[1], 16), int(parts[2].rsplit(':', 1)[1], 16)
            except (IndexError, ValueError):
                continue
            if local == client_port and remote == server_port:
                return int(parts[7])
    return -1 if readable else None


def write_denied(handler, files=PROC_NET, header=True):
    """None neu request duoc phep ghi / chay; nguoc lai ly do (tieng Anh, hien tren giao dien).
    header=False: chi hoi truoc "trang nay co duoc sua khong" (GET /api/tree, chua co header X-Repomap)."""
    host, port = handler.server.server_address[:2]
    if host not in ('127.0.0.1', '::1', 'localhost'):
        return 'Editing and running are turned off while the server listens on the network (host %s).' % host
    if not same_page(handler):
        return 'Request refused: it does not come from the repomap page.'
    if header and handler.headers.get('X-Repomap') != '1':
        return 'Request refused: missing X-Repomap header.'
    if hasattr(os, 'getuid'):
        uid = peer_uid(handler.client_address[1], port, files)
        if uid is not None and uid != os.getuid():
            return 'Request refused: the connection does not belong to the user running repomap.'
    return None


def same_page(handler):
    """Host la may local (port tuy y: port tren PC co the khac port server khi qua tunnel), Origin cung Host."""
    host = handler.headers.get('Host', '')
    name = host if host.endswith(']') else host.rsplit(':', 1)[0]
    origin = handler.headers.get('Origin')
    return name in ('127.0.0.1', 'localhost', '[::1]') and (origin is None or origin.split('//', 1)[-1] == host)

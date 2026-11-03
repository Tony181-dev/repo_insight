"""Tien ich nho dung chung: doc file, liet ke thu muc, goi git, tra loi HTTP."""
import io
import os
import subprocess

# thu muc khong bao gio di vao khi walk
NOWALK = {'.git', '.repo', 'node_modules', '__pycache__', 'out', 'sstate-cache', 'downloads',
          '.venv', 'venv', '.tox', '.nox', '.mypy_cache', '.pytest_cache', '.terraform', 'site-packages'}


def read_text(path, limit=512 * 1024):
    """Doc file text (toi da `limit` byte). Tra None neu loi hoac la file nhi phan."""
    try:
        with open(path, 'rb') as f:
            data = f.read(limit)
    except OSError:
        return None
    if b'\0' in data[:8192]:
        return None
    return data.decode('utf-8', 'replace').replace('\r\n', '\n')        # file CRLF: regex '$' van khop


def listdir(path):
    try:
        return sorted(os.listdir(path))
    except OSError:
        return []


def git(repo_dir, *args, timeout=20):
    """Chay git trong repo_dir, tra stdout hoac None neu loi/timeout."""
    try:
        r = subprocess.run(['git', '-c', 'safe.directory=*', '-C', repo_dir] + list(args),
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.decode('utf-8', 'replace') if r.returncode == 0 else None


def send_whole(handler, code, ctype, data):
    """Tra loi HTTP: header + body gui trong 1 lan ghi socket. Ghi 2 lan thi tren Windows thinh thoang trinh duyet
    nhan header roi treo cho body mai (trang trang, thieu file .js)."""
    sock_out, handler.wfile = handler.wfile, io.BytesIO()
    try:
        handler.send_response(code)
        handler.send_header('Content-Type', ctype)
        handler.send_header('Content-Length', str(len(data)))
        handler.send_header('Cache-Control', 'no-store')
        handler.end_headers()
        whole = handler.wfile.getvalue() + data
    finally:
        handler.wfile = sock_out
    handler.wfile.write(whole)


def bb_name(path):
    """'foo_1.2.bb' -> ('foo', '1.2');  'bar.bbappend' -> ('bar', '')."""
    base = os.path.basename(path).rsplit('.', 1)[0]
    pn, _, pv = base.partition('_')
    return pn, pv

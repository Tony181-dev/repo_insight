"""Tien ich nho dung chung: doc file, liet ke thu muc, goi git."""
import os
import subprocess

# thu muc khong bao gio di vao khi walk
NOWALK = {'.git', '.repo', 'node_modules', '__pycache__', 'out', 'sstate-cache', 'downloads'}


def read_text(path, limit=512 * 1024):
    """Doc file text (toi da `limit` byte). Tra None neu loi hoac la file nhi phan."""
    try:
        with open(path, 'rb') as f:
            data = f.read(limit)
    except OSError:
        return None
    return None if b'\0' in data[:8192] else data.decode('utf-8', 'replace')


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


def bb_name(path):
    """'foo_1.2.bb' -> ('foo', '1.2');  'bar.bbappend' -> ('bar', '')."""
    base = os.path.basename(path).rsplit('.', 1)[0]
    pn, _, pv = base.partition('_')
    return pn, pv

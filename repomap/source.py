"""Man hinh "full source" cua 1 module: liet ke thu muc (nap luoi) va tim kiem trong module.

  list_dir(ws, repo, dir)              -> {dirs: [{name, repo?}], files: [{name, size}]}
  search(ws, repo, base, q, 'name')    -> file co ten chua q
  search(ws, repo, base, q, 'text')    -> dong chua q (git grep; repo khong phai git thi tu doc file)
"""
import os
import subprocess
import time

from .fileview import MAX_READ
from .util import read_text

MAX_ENTRIES = 5000
MAX_RESULTS = 300


def _prefix(repo):
    return '' if repo in ('', '.') else repo + '/'


def list_dir(ws, repo, rel=''):
    rel = rel.strip('/')
    d = ws.abs(repo, rel)
    if not os.path.isdir(d):
        return {'error': 'not a folder: ' + (rel or repo)}
    dirs, files = [], []
    try:
        entries = sorted(os.scandir(d), key=lambda e: (e.name.lower(), e.name))
    except OSError as e:
        return {'error': 'cannot open %s: %s' % (rel or repo, e)}
    for e in entries:
        if e.name in ('.git', '.repo'):
            continue
        try:
            if e.is_dir():
                item = {'name': e.name}
                nested = _prefix(repo) + (rel + '/' if rel else '') + e.name
                if nested in ws.by_path:                 # repo khac nam long ben trong
                    item['repo'] = nested
                dirs.append(item)
            else:
                files.append({'name': e.name, 'size': e.stat().st_size})
        except OSError:
            continue
    return {'repo': repo, 'dir': rel, 'dirs': dirs[:MAX_ENTRIES], 'files': files[:MAX_ENTRIES],
            'truncated': len(dirs) > MAX_ENTRIES or len(files) > MAX_ENTRIES}


def _name_search(ws, repo, base, q):
    files = ws.repo_files(repo)[0]
    pre, ql = (base + '/' if base else ''), q.lower()
    hits = [f for f in files if f.startswith(pre) and ql in f[len(pre):].lower()]
    hits.sort(key=lambda f: (ql not in f.rsplit('/', 1)[-1].lower(), len(f), f))   # khop ten file truoc
    return [{'f': f} for f in hits[:MAX_RESULTS]], len(hits) > MAX_RESULTS


def _git_grep(ws, repo, base, q):
    """Tra (ket qua, bi cat) hoac None neu khong dung duoc git."""
    try:
        r = subprocess.run(['git', '-c', 'safe.directory=*', '-C', ws.abs(repo), 'grep', '-n', '-I', '-i', '-F',
                            '--no-color', '-e', q, '--', base or '.'],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode not in (0, 1):                       # 1 = khong co dong nao khop
        return None
    out = []
    for line in r.stdout.decode('utf-8', 'replace').splitlines():
        parts = line.split(':', 2)
        if len(parts) == 3 and parts[1].isdigit():
            out.append({'f': parts[0], 'n': int(parts[1]), 'text': parts[2].strip()[:300]})
            if len(out) > MAX_RESULTS:
                break
    return out[:MAX_RESULTS], len(out) > MAX_RESULTS


def _scan_grep(ws, repo, base, q, max_files=20000):
    pre, ql, out = (base + '/' if base else ''), q.lower(), []
    for f in [f for f in ws.repo_files(repo)[0] if f.startswith(pre)][:max_files]:
        text = read_text(ws.abs(repo, f), MAX_READ)
        if not text or ql not in text.lower():
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if ql in line.lower():
                out.append({'f': f, 'n': i, 'text': line.strip()[:300]})
                if len(out) > MAX_RESULTS:
                    return out[:MAX_RESULTS], True
    return out, False


def search(ws, repo, base, q, mode='name'):
    t0, base, q = time.time(), base.strip('/'), (q or '').strip()
    if len(q) < 2:
        return {'error': 'type at least 2 characters'}
    ws.abs(repo, base)                                   # chan duong dan ra ngoai workspace
    if mode == 'text':
        results, more = _git_grep(ws, repo, base, q) or _scan_grep(ws, repo, base, q)
    else:
        results, more = _name_search(ws, repo, base, q)
    return {'repo': repo, 'base': base, 'q': q, 'mode': mode, 'results': results, 'truncated': more,
            'took': round(time.time() - t0, 2)}

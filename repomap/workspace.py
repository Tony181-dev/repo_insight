"""Workspace: danh sach repo, anh xa duong dan <-> repo, cache ket qua, chi muc Yocto (lazy)."""
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from .classify import classify
from .manifest import discover_repos, parse_manifest
from .util import NOWALK, bb_name, git, listdir


def find_layers(d):
    """Cac layer Yocto (thu muc co conf/layer.conf) trong d, sau toi da 2 cap. Tra duong dan tuong doi."""
    has = lambda *p: os.path.isfile(os.path.join(d, *p, 'conf', 'layer.conf'))  # noqa: E731
    out = ['.'] if has() else []
    for n in listdir(d):
        if n.startswith('.') or not os.path.isdir(os.path.join(d, n)):
            continue
        if has(n):
            out.append(n)
        else:
            out += [n + '/' + m for m in listdir(os.path.join(d, n)) if has(n, m)]
    return out


class Workspace:
    def __init__(self, root, workers=16):
        self.root = os.path.realpath(root)
        self.workers = workers
        self.mode, self.took = '', 0.0
        self.repos, self.by_path, self.by_tail = [], {}, {}
        self._cache, self._lock = {}, threading.Lock()
        self._layers = self._bb_index = None

    # ---------------- tang 0
    def load(self):
        t0 = time.time()
        ps = parse_manifest(self.root)
        self.mode = 'manifest' if ps is not None else 'scan'
        if ps is None:
            ps = [{'name': p, 'path': p, 'groups': ''} for p in discover_repos(self.root)]
        with ThreadPoolExecutor(self.workers) as ex:
            for p, (kind, tags) in zip(ps, ex.map(lambda p: classify(self.abs(p['path'])), ps)):
                p['kind'], p['tags'] = kind, tags
        self.repos = sorted(ps, key=lambda p: p['path'])
        self.by_path = {p['path']: p for p in self.repos}
        self.by_tail = {}
        for p in self.repos:
            self.by_tail.setdefault(p['name'].rsplit('/', 1)[-1], []).append(p)
        self.took = time.time() - t0
        return self

    # ---------------- duong dan
    def abs(self, repo, rel=''):
        """Duong dan tuyet doi, chan thoat ra ngoai workspace."""
        p = os.path.normpath(os.path.join(self.root, repo, rel))
        if p != self.root and not p.startswith(self.root + os.sep):
            raise ValueError('ngoai workspace: ' + rel)
        return p

    def owner(self, path):
        """Duong dan tuyet doi -> (repo, rel) cua repo sau nhat chua no; None neu ngoai workspace."""
        path = os.path.normpath(path)
        if path != self.root and not path.startswith(self.root + os.sep):
            return None
        rel = os.path.relpath(path, self.root).replace(os.sep, '/')
        parts = rel.split('/')
        for i in range(len(parts) - 1, 0, -1):
            rp = '/'.join(parts[:i])
            if rp in self.by_path:
                return rp, '/'.join(parts[i:])
        return ('.' if '.' in self.by_path else ''), rel

    # ---------------- cache
    def cached(self, key, fn, force=False):
        with self._lock:
            if not force and key in self._cache:
                return self._cache[key]
        value = fn()
        with self._lock:
            self._cache[key] = value
        return value

    # ---------------- file trong repo
    def list_files(self, repo):
        """(files, nguon): uu tien `git ls-files`, fallback os.walk (bo qua repo con)."""
        d = self.abs(repo)
        out = git(d, 'ls-files', '-z', timeout=120)
        if out:
            return [f for f in out.split('\0') if f], 'git'
        files = []
        for dp, dns, fns in os.walk(d):
            dns[:] = [x for x in dns if x not in NOWALK and not os.path.exists(os.path.join(dp, x, '.git'))]
            rel = os.path.relpath(dp, d).replace(os.sep, '/')
            files += [fn if rel == '.' else rel + '/' + fn for fn in fns]
            if len(files) > 150000:
                break
        return files, 'walk'

    # ---------------- chi muc Yocto (tinh lan dau can)
    def layers(self):
        if self._layers is None:
            out = []
            for p in self.repos:
                if {'yocto-layer', 'yocto-multi', 'bitbake'} & set(p['tags']):
                    d = self.abs(p['path'])
                    out += [os.path.normpath(os.path.join(d, L)) for L in find_layers(d)]
            self._layers = out
        return self._layers

    def bb_index(self):
        """pn -> [duong dan .bb/.bbappend] tren tat ca layer."""
        if self._bb_index is None:
            idx = {}
            for layer in self.layers():
                for dp, dns, fns in os.walk(layer):
                    dns[:] = [x for x in dns if not x.startswith('.') and x not in NOWALK
                              and not os.path.isfile(os.path.join(dp, x, 'conf', 'layer.conf'))]
                    for fn in fns:
                        if fn.endswith(('.bb', '.bbappend')):
                            idx.setdefault(bb_name(fn)[0], []).append(os.path.join(dp, fn))
            self._bb_index = idx
        return self._bb_index

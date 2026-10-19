"""Tang 1: insight nhanh 1 repo - chi doc thu muc goc va vai file quen thuoc.

Ket qua: {'repo', 'sections': [{title, rows: [[k, v]]}], 'keys': [file chinh], 'dirs', 'nfiles'}
"""
import os
import re
from fnmatch import fnmatch

from .parsers import ftype, parse_file, vars_dict
from .rules import KEYVARS
from .util import git, listdir, read_text
from .workspace import find_layers


def _section(title, rows):
    rows = [[k, v] for k, v in rows if v]
    return {'title': title, 'rows': rows} if rows else None


def _manifest(p):
    return _section('Manifest', [('name', p.get('name')), ('loai', ', '.join(p['tags']) or p['kind']),
                                 ('revision', p.get('revision')), ('upstream', p.get('upstream')),
                                 ('groups', p.get('groups')), ('remote', p.get('remote')), ('url', p.get('url'))])


def _git(d):
    out = git(d, 'log', '-1', '--format=%h%x1f%ad%x1f%an%x1f%s', '--date=short', timeout=10)
    if not out:
        return None
    rows = list(zip(('commit', 'date', 'author', 'subject'), out.strip().split('\x1f')))
    st = git(d, 'status', '--porcelain', '-uno', timeout=20)
    if st is not None:
        rows.append(('modified', str(len(st.splitlines()))))
    return _section('Git', rows)


def _yocto(d, keys):
    secs = []
    for L in find_layers(d)[:30]:
        ld = os.path.join(d, L)
        v = vars_dict(os.path.join(ld, 'conf', 'layer.conf'))
        coll = (v.get('BBFILE_COLLECTIONS') or '').split()
        c = coll[0] if coll else ''
        mach = [n[:-5] for n in listdir(os.path.join(ld, 'conf', 'machine')) if n.endswith('.conf')]
        dist = [n[:-5] for n in listdir(os.path.join(ld, 'conf', 'distro')) if n.endswith('.conf')]
        recs = [n[8:] for n in listdir(ld) if n.startswith('recipes-')]
        secs.append(_section('Layer ' + (os.path.basename(d) if L == '.' else L), [
            ('collection', ' '.join(coll)), ('priority', v.get('BBFILE_PRIORITY_' + c)),
            ('depends', v.get('LAYERDEPENDS_' + c)), ('compat', v.get('LAYERSERIES_COMPAT_' + c)),
            ('machines', ', '.join(mach)), ('distros', ', '.join(dist)), ('recipes-*', ', '.join(recs))]))
        pre = '' if L == '.' else L + '/'
        keys += [pre + 'conf/layer.conf'] + [pre + 'conf/machine/%s.conf' % m for m in mach[:15]] \
            + [pre + 'conf/distro/%s.conf' % m for m in dist[:5]]
    return secs


def _kernel(d, tags):
    v = vars_dict(os.path.join(d, 'Makefile'))
    ver = '.'.join(x for x in (v.get('VERSION'), v.get('PATCHLEVEL'), v.get('SUBLEVEL')) if x)
    archs = [a for a in listdir(os.path.join(d, 'arch')) if os.path.isdir(os.path.join(d, 'arch', a))]
    cnt = {a: len(listdir(os.path.join(d, 'arch', a, 'configs'))) for a in archs}
    rows = [('version', ver + (v.get('EXTRAVERSION') or '')), ('name', v.get('NAME')),
            ('arch (defconfig)', ', '.join('%s(%d)' % (a, cnt[a]) if cnt[a] else a for a in archs))]
    if 'u-boot' in tags:
        rows += [('configs/', '%d defconfig' % len(listdir(os.path.join(d, 'configs')))),
                 ('board vendors', str(len(listdir(os.path.join(d, 'board')))))]
    return _section('Kernel' if 'kernel' in tags else 'U-Boot', rows)


def _keyvars(d, files, keys):
    secs, done = [], set()
    for pat, wanted in KEYVARS:
        for n in files:
            if n in done or len(done) > 10 or not fnmatch(n, pat):
                continue
            v = vars_dict(os.path.join(d, n))
            s = _section(n, [(k, (v.get(k) or '')[:400]) for k in wanted.split()])
            if s:
                done.add(n)
                secs.append(s)
                keys.append(n)
    return secs


def _root_items(d, files):
    secs = []
    for n in ('Android.bp', 'Android.mk', 'CMakeLists.txt'):
        if n in files:
            items = parse_file(n, read_text(os.path.join(d, n)) or '')['items']
            if items:
                secs.append({'title': '%s (%d)' % (n, len(items)), 'rows': [[m['type'], m['name']] for m in items[:40]]})
    return secs


def _qnx(d, files):
    nto = os.path.join(d, 'nto')
    variants = [os.path.relpath(dp, nto).replace(os.sep, '/') for dp, dns, _ in os.walk(nto) if not dns and dp != nto]
    rows = [('variants', ', '.join(variants[:60]))]
    for n in files:
        if n.endswith('.build') and n != 'meson.build':
            rows += [(n, m['name']) for m in parse_file(n, read_text(os.path.join(d, n)) or '')['items'][:3]]
    return _section('QNX', rows)


def _bitbake(d):
    for p in (('lib', 'bb', '__init__.py'), ('bitbake', 'lib', 'bb', '__init__.py')):
        m = re.search(r'__version__\s*=\s*"([^"]+)"', read_text(os.path.join(d, *p)) or '')
        if m:
            return _section('BitBake', [('version', m.group(1))])
    return None


def insight(ws, repo):
    p, d = ws.by_path[repo], ws.abs(repo)
    try:
        ents = sorted(os.scandir(d), key=lambda e: e.name)
    except OSError as e:
        return {'error': 'khong mo duoc %s: %s' % (d, e)}
    dirs = [e.name for e in ents if e.is_dir() and e.name != '.git']
    files = [e.name for e in ents if e.is_file()]
    tags = p['tags']
    keys = [n for n in files if ftype(n) != 'text' or n.lower().startswith('readme')]

    secs = [_manifest(p), _git(d)]
    secs += _yocto(d, keys)
    if 'kernel' in tags or 'u-boot' in tags:
        secs.append(_kernel(d, tags))
    secs += _keyvars(d, files, keys)
    secs += _root_items(d, files)
    if 'qnx' in tags:
        secs.append(_qnx(d, files))
    if 'bitbake' in tags:
        secs.append(_bitbake(d))

    keys = [k for i, k in enumerate(keys) if k not in keys[:i] and os.path.isfile(os.path.join(d, k))]
    return {'repo': repo, 'sections': [s for s in secs if s], 'keys': keys[:80], 'dirs': dirs[:200],
            'nfiles': len(files)}

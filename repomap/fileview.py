"""Tang 3: xem 1 file - bien, item, tham chieu da resolve (include/require/inherit/bbappend/SRC_URI)."""
import os
import re

from .parsers import ftype, parse_file
from .util import bb_name, read_text

MAX_LINES = 4000


def ver_match(append_ver, recipe_ver):
    """Quy tac ten bbappend: 'foo_1.2%.bbappend' khop moi recipe 'foo_1.2*'."""
    if '%' in append_ver:
        return recipe_ver.startswith(append_ver.split('%')[0])
    return append_ver == recipe_ver


def _ancestors(fdir, base, limit=12):
    out, u = [], fdir
    while u.startswith(base) and len(out) < limit:
        out.append(u)
        if u == base:
            break
        u = os.path.dirname(u)
    return out


def _resolve_src(ws, raw):
    """URL git trong SRC_URI -> repo trong manifest (khop theo ten project)."""
    name = re.sub(r'\.git$', '', re.sub(r'^\w+://[^/]+/', '', raw.split(';')[0])).strip('/')
    cands = ws.by_tail.get(name.rsplit('/', 1)[-1], [])
    exact = [p for p in cands if name.endswith(p['name']) or p['name'].endswith(name)]
    best = exact or (cands if len(cands) == 1 else [])
    return {'repo': best[0]['path'], 'file': ''} if best else None


def _expand(ws, raw, fdir, rel):
    pn, pv = bb_name(rel)
    for a, b in (('$(LOCAL_PATH)', fdir), ('${THISDIR}', fdir),
                 ('$(SRC_TARGET_DIR)', os.path.join(ws.root, 'build', 'make', 'target')),
                 ('$(TOP)', ws.root), ('$(ANDROID_BUILD_TOP)', ws.root),
                 ('${BPN}', pn), ('${PN}', pn), ('${PV}', pv)):
        raw = raw.replace(a, b)
    return raw


def _py_candidates(ws, base, fdir, raw):
    """'pkg.mod' / '..pkg.mod' -> cac file .py / __init__.py co the la module do."""
    level = len(raw) - len(raw.lstrip('.'))
    parts = [p for p in raw.lstrip('.').split('.') if p]
    if level:
        root = fdir
        for _ in range(level - 1):
            root = os.path.dirname(root)
        roots = [root]
    else:
        roots = _ancestors(fdir, base) + [os.path.join(base, 'src'), os.path.join(base, 'lib')]
        if parts and parts[0] in ws.py_packages():               # package o repo Python khac trong workspace
            roots += ws.py_packages()[parts[0]]
    out = []
    for r in roots:
        p = os.path.join(r, *parts)
        out += [p + '.py', os.path.join(p, '__init__.py')] if parts else [os.path.join(r, '__init__.py')]
    return out


def _find_in_repo(ws, repo, rel, raw):
    """#include "a/b.h" khong thay theo duong dan -> tim file cung ten trong repo, chon file gan nhat."""
    suffix = '/' + raw.lstrip('./')
    cands = [f for f in ws.file_index(repo).get(os.path.basename(raw), []) if ('/' + f).endswith(suffix)]
    if not cands:
        return []
    here = os.path.dirname(rel).split('/')
    near = max(cands, key=lambda f: (len(os.path.commonprefix([here, f.split('/')])), -len(f)))
    return [ws.abs(repo, near)]


def _candidates(ws, repo, rel, kind, raw):
    base = ws.abs(repo)
    fdir = os.path.dirname(ws.abs(repo, rel))
    if kind == 'import':
        return _py_candidates(ws, base, fdir, raw)
    raw = _expand(ws, raw, fdir, rel)
    if '$' in raw or not raw:
        return []
    ft = ftype(rel)
    if kind == 'role':
        return [os.path.join(x, 'roles', raw, 'tasks', n) for x in _ancestors(fdir, base) for n in ('main.yml', 'main.yaml')]
    if kind == 'module':
        d = os.path.normpath(os.path.join(fdir, raw))
        tf = sorted(n for n in os.listdir(d) if n.endswith('.tf')) if os.path.isdir(d) else []
        return [os.path.join(d, n) for n in (['main.tf'] if 'main.tf' in tf else tf[:1])]
    if kind == 'inherit':
        return [os.path.join(L, c, raw + '.bbclass') for L in ws.layers()
                for c in ('classes', 'classes-recipe', 'classes-global')]
    if kind == 'add_subdirectory':
        return [os.path.join(fdir, raw, 'CMakeLists.txt')]
    if kind == 'subdir':
        return [os.path.join(fdir, raw, 'meson.build')]
    cands = [os.path.join(x, raw) for x in _ancestors(fdir, base)]
    cands += [os.path.join(base, 'include', raw), os.path.join(base, 'scripts', 'dtc', 'include-prefixes', raw),
              os.path.join(ws.root, raw)]
    if ft == 'bb':
        cands += [os.path.join(L, raw) for L in ws.layers()]
    elif ft == 'cmake':
        cands += [c + '.cmake' for c in cands]
    return cands


def resolve_ref(ws, repo, rel, kind, raw):
    """Tra {'repo', 'file'} cua file duoc tham chieu, hoac None."""
    raw = raw.strip('"\'')
    if kind == 'src':
        return _resolve_src(ws, raw)
    cands = [c for c in _candidates(ws, repo, rel, kind, raw) if os.path.isfile(c)]
    if not cands and kind == 'include' and ftype(rel) == 'c':
        cands = _find_in_repo(ws, repo, rel, raw)                # khong co include path -> tim theo ten file
    for c in cands:
        o = ws.owner(c)
        if o:
            return {'repo': o[0], 'file': o[1]}
    return None


def _bbappend_refs(ws, rel, abspath):
    """Recipe -> cac bbappend o moi layer; bbappend -> recipe goc."""
    refs = []
    pn, pv = bb_name(rel)
    is_recipe = rel.endswith('.bb')
    for path in ws.bb_index().get(pn, []):
        if os.path.normpath(path) == abspath:
            continue
        other = bb_name(path)[1]
        if is_recipe and path.endswith('.bbappend') and ver_match(other, pv):
            kind = 'appended-by'
        elif not is_recipe and path.endswith('.bb') and ver_match(pv, other):
            kind = 'appends'
        else:
            continue
        o = ws.owner(path)
        if o:
            refs.append({'k': kind, 'raw': '/'.join(x for x in o if x), 'line': 0, 'to': {'repo': o[0], 'file': o[1]}})
    return refs


def file_view(ws, repo, rel):
    path = ws.abs(repo, rel)
    if not os.path.isfile(path):
        return {'error': 'not a file: ' + rel}
    text = read_text(path, 2 * 1024 * 1024)
    if text is None:
        return {'error': 'binary or unreadable file'}
    res = parse_file(rel, text)
    refs, seen = [], set()
    for r in res['refs']:
        to = resolve_ref(ws, repo, rel, r['k'], r['raw'])
        if to:
            r['to'] = to
        key = (to['repo'], to['file']) if to else None
        if r.pop('opt', False) and (not to or key in seen):     # 'from X import name': chi giu neu name la module
            continue
        seen.add(key)
        refs.append(r)
    res['refs'] = refs
    if rel.endswith(('.bb', '.bbappend')):
        res['refs'] += _bbappend_refs(ws, rel, path)
    lines = text.split('\n')
    res.update({'repo': repo, 'file': rel, 'size': os.path.getsize(path), 'lines': len(lines),
                'text': '\n'.join(lines[:MAX_LINES]), 'trunc': len(lines) > MAX_LINES, 'vars': res['vars'][:2000]})
    return res

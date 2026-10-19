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


def _candidates(ws, repo, rel, kind, raw):
    base = ws.abs(repo)
    fdir = os.path.dirname(ws.abs(repo, rel))
    raw = _expand(ws, raw, fdir, rel)
    if '$' in raw or not raw:
        return []
    ft = ftype(rel)
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
    for c in _candidates(ws, repo, rel, kind, raw):
        if os.path.isfile(c):
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
        return {'error': 'khong phai file: ' + rel}
    text = read_text(path, 2 * 1024 * 1024)
    if text is None:
        return {'error': 'file nhi phan / khong doc duoc'}
    res = parse_file(rel, text)
    for r in res['refs']:
        to = resolve_ref(ws, repo, rel, r['k'], r['raw'])
        if to:
            r['to'] = to
    if rel.endswith(('.bb', '.bbappend')):
        res['refs'] += _bbappend_refs(ws, rel, path)
    lines = text.split('\n')
    res.update({'repo': repo, 'file': rel, 'size': os.path.getsize(path), 'lines': len(lines),
                'text': '\n'.join(lines[:MAX_LINES]), 'trunc': len(lines) > MAX_LINES, 'vars': res['vars'][:2000]})
    return res

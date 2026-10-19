"""Tang 2: quet sau 1 repo - liet ke toan bo file, gom nhom build/config, rut module/variant.

Ket qua: {'repo', 'total', 'src', 'took', 'ext': [[ext, n]], 'top': [[dir, n]],
          'cats': [{id, label, count, items: [{f, l?, n?}]}]}
"""
import re
import time
from collections import Counter

from .parsers import parse_file
from .rules import CATS, categorize
from .util import bb_name, read_text

CAP = 600            # so phan tu toi da moi nhom gui ve UI
MAX_BUILD_FILES = 400  # so Android.bp/.mk toi da doc de rut module


def _item(cid, f):
    n = f.rsplit('/', 1)[-1]
    if cid in ('recipe', 'image', 'bbappend'):
        pn, pv = bb_name(f)
        return {'f': f, 'l': pn + (' · ' + pv if pv else '')}
    if cid in ('machine', 'distro'):
        return {'f': f, 'l': n[:-5]}
    return {'f': f}


def _android_modules(ws, repo, build_files):
    mods = []
    for f in build_files[:MAX_BUILD_FILES]:
        text = read_text(ws.abs(repo, f))
        if text:
            mods += [{'f': f, 'n': m['line'], 'l': '%s: %s' % (m['type'], m['name'])}
                     for m in parse_file(f, text)['items']]
    return mods


def _qnx_variants(files):
    return sorted({f.rsplit('/', 1)[0] for f in files if re.search(r'(^|/)nto/[^/]+/[^/]+/Makefile$', f)})


def _group(cid, label, items, count=None):
    return {'id': cid, 'label': label, 'count': len(items) if count is None else count, 'items': items[:CAP]}


def deep_scan(ws, repo):
    t0 = time.time()
    files, src = ws.repo_files(repo, refresh=True)
    tagstr = ' '.join(ws.by_path[repo]['tags'])

    cats, ext, top = {}, Counter(), Counter()
    for f in files:
        n = f.rsplit('/', 1)[-1]
        ext[n.rsplit('.', 1)[-1].lower() if '.' in n[1:] else n] += 1
        top[f.split('/', 1)[0] + '/' if '/' in f else '(root)'] += 1
        cid = categorize(f, tagstr)
        if cid:
            cats.setdefault(cid, []).append(f)

    groups = []
    mods = _android_modules(ws, repo, cats.get('bp', []) + cats.get('amk', []))
    if mods:
        groups.append(_group('modules', 'Android modules', mods))
    variants = _qnx_variants(files)
    if variants:
        groups.append(_group('qnxvar', 'QNX variants', [{'f': v + '/Makefile', 'l': v} for v in variants]))
    for cid, label, _ in CATS:
        if cats.get(cid):
            groups.append(_group(cid, label, [_item(cid, f) for f in cats[cid][:CAP]], len(cats[cid])))

    return {'repo': repo, 'total': len(files), 'src': src, 'took': round(time.time() - t0, 2),
            'ext': ext.most_common(14), 'top': top.most_common(14), 'cats': groups}

"""Tang 0: lay danh sach repo tu .repo/manifest.xml (hoac quet thu muc .git neu khong co)."""
import os
import sys
import xml.etree.ElementTree as ET

from .util import NOWALK, listdir


def parse_manifest(root):
    """Tra list project dict, hoac None neu workspace khong co .repo/manifest.xml.

    Ho tro: include, local_manifests, project long nhau, extend-project, remove-project.
    """
    rdir = os.path.join(root, '.repo')
    entry = os.path.join(rdir, 'manifest.xml')
    if not os.path.isfile(entry):
        return None
    remotes, default, projects, seen = {}, {}, {}, set()

    def add(el, ppath='', pname=''):
        name = el.get('name')
        if not name:
            return
        path = el.get('path') or name
        if pname:
            name, path = pname + '/' + name, ppath + '/' + path
        p = {'name': name, 'path': path.strip('/'), 'remote': el.get('remote'), 'revision': el.get('revision'),
             'upstream': el.get('upstream'), 'groups': el.get('groups') or ''}
        projects[p['path']] = p
        for c in el:
            if c.tag == 'project':
                add(c, p['path'], name)

    def modify(tag, a):
        if not (a.get('name') or a.get('path')):
            return
        for k, p in list(projects.items()):
            if a.get('name', p['name']) != p['name'] or a.get('path', p['path']) != p['path']:
                continue
            if tag == 'remove-project':
                del projects[k]
                continue
            if a.get('groups'):
                p['groups'] = (p['groups'] + ',' + a['groups']).strip(',')
            for f in ('revision', 'remote'):
                if a.get(f):
                    p[f] = a[f]

    def parse(path):
        real = os.path.realpath(path)
        if real in seen:
            return
        seen.add(real)
        try:
            top = ET.parse(path).getroot()
        except (ET.ParseError, OSError) as e:
            print('  ! %s: %s' % (path, e), file=sys.stderr)
            return
        for el in top:
            t, a = el.tag, el.attrib
            if t == 'remote':
                remotes[a.get('name')] = a
            elif t == 'default':
                default.update(a)
            elif t == 'include':
                for base in (os.path.join(rdir, 'manifests'), os.path.dirname(path)):
                    q = os.path.join(base, a.get('name', ''))
                    if os.path.isfile(q):
                        parse(q)
                        break
            elif t == 'project':
                add(el)
            elif t in ('extend-project', 'remove-project'):
                modify(t, a)

    parse(entry)
    ldir = os.path.join(rdir, 'local_manifests')
    for fn in listdir(ldir):
        if fn.endswith('.xml'):
            parse(os.path.join(ldir, fn))

    for p in projects.values():
        p['remote'] = p['remote'] or default.get('remote')
        rm = remotes.get(p['remote']) or {}
        p['revision'] = p['revision'] or rm.get('revision') or default.get('revision')
        if rm.get('fetch'):
            p['url'] = rm['fetch'].rstrip('/') + '/' + p['name']
    return list(projects.values())


def discover_repos(root, max_depth=6):
    """Fallback khi khong co manifest: tim cac thu muc co .git (khong di vao trong repo con)."""
    found = []
    for dp, dns, fns in os.walk(root):
        rel = os.path.relpath(dp, root).replace(os.sep, '/')
        if '.git' in dns or '.git' in fns:
            found.append(rel)
            if rel != '.':
                dns[:] = []
                continue
        dns[:] = [d for d in dns if d not in NOWALK and not d.startswith(('tmp', 'build-'))]
        if rel.count('/') + 1 >= max_depth:
            dns[:] = []
    return found


def discover_projects(root, max_depth=6):
    """Find independent manifest roots as well as Git roots in a scan folder.

    Stop at each manifest/Git boundary so AOSP's thousands of child repos are
    read from its manifest, rather than traversed a second time.
    """
    projects, manifests = [], 0
    for dp, dns, fns in os.walk(root):
        rel = os.path.relpath(dp, root).replace(os.sep, '/')
        ps = parse_manifest(dp) if '.repo' in dns else None
        if ps is not None:
            manifests += 1
            for p in ps:
                p = dict(p, scan_root=rel)
                if rel != '.':
                    p['path'] = rel + '/' + p['path']
                projects.append(p)
            dns[:] = []
            continue
        if '.git' in dns or '.git' in fns:
            projects.append({'name': rel, 'path': rel, 'groups': '', 'scan_root': rel})
            if rel != '.':
                dns[:] = []
                continue
        dns[:] = [d for d in dns if d not in NOWALK and not d.startswith(('tmp', 'build-'))]
        if rel.count('/') + 1 >= max_depth:
            dns[:] = []
    return ('manifest' if manifests else 'scan'), projects

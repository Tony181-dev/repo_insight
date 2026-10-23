"""Conservative VM/repository-root grouping for the architecture view.

These are source scopes, not evidence that a hypervisor actually runs a VM.
Explicit folder names and manifest boundaries take precedence over build tags:
an Android kernel is part of Android, not a second Linux guest.
"""
import os
import re
from collections import Counter, defaultdict


PLATFORMS = {'android': 'Android', 'aosp': 'Android', 'qnx': 'QNX',
             'linux': 'Linux', 'yocto': 'Linux'}


def path_platform(path):
    for segment in path.replace('\\', '/').split('/'):
        for token in re.split(r'[-_.\s]+', segment.lower()):
            if token in PLATFORMS:
                return PLATFORMS[token]
    return None


def _tag_platform(p):
    tags = set(p['tags'])
    if 'qnx' in tags:
        return 'QNX'
    if tags & {'android', 'android-device'}:
        return 'Android'
    if tags & {'yocto-layer', 'yocto-multi', 'bitbake'}:
        return 'Linux'
    return None


def architecture_scopes(ws):
    roots = defaultdict(list)
    for p in ws.repos:
        root = p.get('scan_root', '.')
        # Git repos underneath a named VM folder belong to that VM together.
        if ws.mode == 'scan':
            parts = p['path'].split('/')
            prefix = next(('/'.join(parts[:i + 1]) for i, s in enumerate(parts)
                           if path_platform(s)), None)
            root = prefix or root
        roots[root].append(p)
    scopes, owners = [], {}
    for root, repos in sorted(roots.items()):
        named = path_platform(root if root != '.' else os.path.basename(ws.root))
        signals = {s for p in repos for s in [(None if 'kernel' in p['tags'] else path_platform(p['path']))
                                              or _tag_platform(p)] if s}
        # A Linux kernel alone also identifies Linux, unless enclosed by an
        # Android root (AOSP ships Linux) or the sole Android platform.
        if any('kernel' in p['tags'] for p in repos) and not named and signals != {'Android'}:
            signals.add('Linux')
        hints = defaultdict(set)
        for p in repos:
            hint = _tag_platform(p)
            if hint:
                parents = p['path'].split('/')[:-1]
                for depth in range(1, len(parents) + 1):
                    hints['/'.join(parents[:depth])].add(hint)
        groups = defaultdict(list)
        for p in repos:
            platform = named or path_platform(p['path']) or _tag_platform(p)
            if not named and len(signals) == 1:
                platform = next(iter(signals))
            if not platform:
                ancestors = p['path'].split('/')[:-1]
                for depth in range(len(ancestors), 0, -1):
                    nearby = hints['/'.join(ancestors[:depth])]
                    if len(nearby) == 1:
                        platform = next(iter(nearby))
                        break
            if not platform and 'kernel' in p['tags']:
                platform = 'Linux'
            groups[platform or ('Shared' if signals else 'Workspace')].append(p)
        for platform, members in sorted(groups.items()):
            sid = root + '::' + platform
            label = platform if platform not in ('Shared', 'Workspace') else (
                'Shared / workspace' if platform == 'Shared' else os.path.basename(ws.root) if root == '.'
                else root.rsplit('/', 1)[-1])
            scopes.append({'id': sid, 'label': label, 'platform': platform, 'root': root,
                           'repos': len(members), 'paths': [p['path'] for p in members]})
            owners.update({p['path']: sid for p in members})
    # Same OS, independent roots: retain names instead of merging both guests.
    labels = Counter(s['label'] for s in scopes)
    for s in scopes:
        if labels[s['label']] > 1:
            s['label'] += ' · ' + s['root']
    return scopes, owners

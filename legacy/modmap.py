#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
modmap - ban do workspace `repo sync` (Yocto / BitBake / Android / QNX / Linux)

  python3 modmap.py /path/to/workspace                 # quet + mo giao dien web
  python3 modmap.py /path/to/workspace --port 9000 --no-browser
  python3 modmap.py /path/to/workspace --dump vendor/foo/bar   # in JSON phan tich 1 repo

- Chi dung thu vien chuan Python 3.8+, khong can mang, khong can build.
- Phan tich TINH: doc manifest, file build/cau hinh; khong chay bitbake/make.
- Server chi nghe 127.0.0.1 (doi bang --host neu can).
"""
import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

VERSION = "0.1.0"

# ---------------------------------------------------------------- utils

SKIP_DIRS = {'.git', '.repo', '.svn', '.hg', 'node_modules', '__pycache__', '.cache',
             '.idea', '.vscode', 'sstate-cache', 'downloads', '.gradle'}
MAX_WALK_DEPTH = 7
MAX_WALK_FILES = 60000
LIST_CAP = 400


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def read_text(path, limit=600_000):
    try:
        with open(path, 'rb') as f:
            data = f.read(limit)
    except OSError:
        return None
    if b'\x00' in data[:8192]:
        return None
    return data.decode('utf-8', 'replace')


def head(path, n=4096):
    try:
        with open(path, 'rb') as f:
            return f.read(n).decode('utf-8', 'replace')
    except OSError:
        return ''


def logical_lines(text):
    """(lineno, line) with backslash continuations joined."""
    buf, start = [], None
    for i, raw in enumerate(text.splitlines(), 1):
        if start is None:
            start = i
        if raw.endswith('\\'):
            buf.append(raw[:-1].strip() if buf else raw[:-1])
            continue
        buf.append(raw.strip() if buf else raw)
        yield start, ' '.join(buf)
        buf, start = [], None
    if buf:
        yield start, ' '.join(buf)


def unquote(v):
    v = v.strip()
    if len(v) >= 2 and v[0] in '"\'':
        q = v[0]
        end = v.rfind(q)
        if end > 0:
            return v[1:end]
        return v[1:]
    return v


def norm_arch(raw):
    if raw is None:
        return None
    s = str(raw).strip().strip('"\'').lower()
    if not s or '$' in s and not re.search(r'aarch64|arm|x86|riscv', s):
        return None
    if re.search(r'aarch64|arm64|armv8|armv9|neoverse|cortex-?a(3[45]|5[3-9]|6[05]|7[2-9]|510|710|715|720)(?!\d)', s):
        return 'aarch64'
    if re.search(r'x86[_-]64|amd64|corei7|haswell|skylake|core2-64|nehalem|westmere|sandybridge|icelake|(^|[^a-z])x64', s):
        return 'x86_64'
    if re.search(r'riscv64|rv64', s):
        return 'riscv64'
    if re.search(r'riscv32|rv32', s):
        return 'riscv32'
    if re.search(r'(^|[^a-z])arm|ntoarm|armv[4-7]|armle|cortex-?a(5|7|8|9|12|15|17)(?!\d)|cortex-?[rm]\d|thumb', s):
        return 'arm (32-bit)'
    if re.search(r'(^|[^a-z0-9])x86($|[^_0-9])|ntox86(?!_)|i[3-6]86|core2-32|(^|[^a-z])atom|ia32', s):
        return 'x86'
    if 'mips' in s:
        return 'mips'
    if re.search(r'ppc|powerpc', s):
        return 'powerpc'
    return None


def os_from_triple(s):
    s = (s or '').lower()
    if 'nto' in s or 'qnx' in s:
        return 'QNX Neutrino'
    if 'android' in s:
        return 'Android'
    if 'linux' in s:
        return 'Linux'
    if 'none' in s or s.endswith('-elf-') or s.endswith('-eabi-'):
        return 'Bare-metal'
    return None


class Cand:
    RANK = {'low': 0, 'medium': 1, 'high': 2}

    def __init__(self):
        self.d = {}

    def add(self, v, src=None, line=None, conf='medium', note=None, xrepo=None, isdir=False):
        if not v:
            return
        v = str(v).strip()
        if not v or len(v) > 120:
            return
        e = self.d.setdefault(v, {'v': v, 'conf': conf, 'src': []})
        if self.RANK[conf] > self.RANK[e['conf']]:
            e['conf'] = conf
        if src and len(e['src']) < 6 and not any(x['file'] == src and x['line'] == line for x in e['src']):
            ent = {'file': src, 'line': line, 'note': note}
            if isdir:
                ent['dir'] = True
            if xrepo:
                ent.update({'xrepo': xrepo, 'xfile': src, 'file': xrepo + '/' + src})
            e['src'].append(ent)

    def out(self, limit=12):
        vals = sorted(self.d.values(), key=lambda e: (-self.RANK[e['conf']], -len(e['src']), e['v']))
        return vals[:limit]


# ---------------------------------------------------------------- manifest

def parse_manifest(root):
    repo_dir = os.path.join(root, '.repo')
    entry = os.path.join(repo_dir, 'manifest.xml')
    if not os.path.isfile(entry):
        return None
    mdir = os.path.join(repo_dir, 'manifests')
    remotes, default = {}, {}
    projects = {}           # name -> dict (insertion ordered)
    files_read = []
    seen = set()

    def resolve(name, cur):
        for base in (mdir, os.path.dirname(cur), repo_dir):
            p = os.path.join(base, name)
            if os.path.isfile(p):
                return p
        return None

    def add_project(el, parent_path=None, parent_name=None):
        name = el.get('name')
        if not name:
            return
        path = el.get('path') or name
        if parent_name:
            name = parent_name.rstrip('/') + '/' + name
            path = parent_path.rstrip('/') + '/' + path
        p = {
            'name': name, 'path': path.strip('/'),
            'remote': el.get('remote'), 'revision': el.get('revision'),
            'upstream': el.get('upstream'), 'groups': el.get('groups') or '',
            'dest_branch': el.get('dest-branch'),
            'links': [],
        }
        for c in el:
            if c.tag in ('linkfile', 'copyfile'):
                p['links'].append({'t': c.tag, 'src': c.get('src'), 'dest': c.get('dest')})
            elif c.tag == 'annotation':
                p.setdefault('annotations', {})[c.get('name')] = c.get('value')
            elif c.tag == 'project':
                add_project(c, p['path'], name)
        projects[name + '\0' + p['path']] = p

    def parse(path):
        real = os.path.realpath(path)
        if real in seen:
            return
        seen.add(real)
        files_read.append(os.path.relpath(path, root))
        try:
            tree = ET.parse(path)
        except ET.ParseError as e:
            print('  ! loi XML %s: %s' % (path, e), file=sys.stderr)
            return
        for el in tree.getroot():
            t = el.tag
            if t == 'remote':
                remotes[el.get('name')] = {'fetch': el.get('fetch'), 'review': el.get('review'),
                                           'revision': el.get('revision'), 'alias': el.get('alias')}
            elif t == 'default':
                default.update({k: v for k, v in el.attrib.items()})
            elif t == 'include':
                p = resolve(el.get('name', ''), path)
                if p:
                    parse(p)
            elif t == 'project':
                add_project(el)
            elif t == 'extend-project':
                for k, p in list(projects.items()):
                    if p['name'] == el.get('name') and (not el.get('path') or p['path'] == el.get('path')):
                        if el.get('groups'):
                            p['groups'] = (p['groups'] + ',' + el.get('groups')).strip(',')
                        if el.get('revision'):
                            p['revision'] = el.get('revision')
                        if el.get('remote'):
                            p['remote'] = el.get('remote')
            elif t == 'remove-project':
                for k, p in list(projects.items()):
                    if p['name'] == el.get('name') or (el.get('path') and p['path'] == el.get('path')):
                        if not el.get('path') or p['path'] == el.get('path'):
                            del projects[k]

    parse(entry)
    local = os.path.join(repo_dir, 'local_manifests')
    if os.path.isdir(local):
        for fn in sorted(os.listdir(local)):
            if fn.endswith('.xml'):
                parse(os.path.join(local, fn))

    out = []
    for p in projects.values():
        rname = p['remote'] or default.get('remote')
        rev = p['revision'] or (remotes.get(rname) or {}).get('revision') or default.get('revision')
        fetch = (remotes.get(rname) or {}).get('fetch')
        url = None
        if fetch:
            if fetch.startswith('..') or fetch == '.':
                url = fetch.rstrip('/') + '/' + p['name']
            else:
                url = fetch.rstrip('/') + '/' + p['name']
        p.update({'remote': rname, 'revision': rev, 'url': url})
        out.append(p)
    return {'files': files_read, 'remotes': remotes, 'default': default, 'projects': out}


def discover_git_repos(root, max_depth=8):
    found = []
    root = os.path.abspath(root)
    for dirpath, dirnames, filenames in os.walk(root):
        rel = os.path.relpath(dirpath, root)
        depth = 0 if rel == '.' else rel.count(os.sep) + 1
        if '.git' in dirnames or '.git' in filenames:
            found.append('.' if rel == '.' else rel.replace(os.sep, '/'))
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and d != 'out'
                       and not d.startswith('bazel-') and not d.startswith('tmp')]
        if depth >= max_depth:
            dirnames[:] = []
    return found


# ---------------------------------------------------------------- quick classify

KIND_ORDER = ['yocto-layer', 'yocto-multi', 'bitbake', 'kernel', 'u-boot', 'android-device',
              'android', 'qnx', 'cmake', 'meson', 'bazel', 'autotools', 'make', 'devicetree',
              'cargo', 'go', 'gradle', 'node', 'python', 'other']


def quick_classify(absdir):
    if not os.path.isdir(absdir):
        return {'kind': 'missing', 'tags': []}
    try:
        entries = {e.name: e.is_dir() for e in os.scandir(absdir)}
    except OSError:
        return {'kind': 'other', 'tags': []}
    names = set(entries)
    dirs = {n for n, d in entries.items() if d}
    tags = set()
    ex = lambda *p: os.path.exists(os.path.join(absdir, *p))

    if ex('conf', 'layer.conf'):
        tags.add('yocto-layer')
    for d in dirs:
        if d.startswith('meta') and ex(d, 'conf', 'layer.conf'):
            tags.add('yocto-multi')
            break
    if ex('bin', 'bitbake') or ex('bitbake', 'bin', 'bitbake'):
        tags.add('bitbake')
    if 'Kconfig' in names and {'arch', 'kernel', 'mm'} <= dirs:
        tags.add('kernel')
    elif 'Kconfig' in names and {'arch', 'board', 'cmd'} <= dirs:
        tags.add('u-boot')
    if 'AndroidProducts.mk' in names or any(n.startswith('BoardConfig') and n.endswith('.mk') for n in names):
        tags.add('android-device')
    if 'Android.bp' in names or 'Android.mk' in names:
        tags.add('android')
    qnx = 'nto' in dirs or any(n.endswith('.use') for n in names)
    if not qnx:
        for mk in ('Makefile', 'common.mk'):
            if mk in names:
                h = head(os.path.join(absdir, mk), 3000)
                if re.search(r'recurse\.mk|qtargets\.mk|qmacros\.mk|QCONFIG|qcc\b', h):
                    qnx = True
                    break
    if not qnx:
        for n in names:
            if n.endswith('.build') and '[virtual=' in head(os.path.join(absdir, n), 6000):
                qnx = True
                break
    if qnx:
        tags.add('qnx')
    if 'CMakeLists.txt' in names:
        tags.add('cmake')
    if 'meson.build' in names:
        tags.add('meson')
    if names & {'WORKSPACE', 'MODULE.bazel', 'WORKSPACE.bazel'}:
        tags.add('bazel')
    if names & {'configure.ac', 'configure.in'}:
        tags.add('autotools')
    if names & {'Makefile', 'makefile', 'GNUmakefile'}:
        tags.add('make')
    if any(n.endswith(('.dts', '.dtsi')) for n in names) or 'dts' in dirs:
        tags.add('devicetree')
    if 'Cargo.toml' in names:
        tags.add('cargo')
    if 'go.mod' in names:
        tags.add('go')
    if names & {'build.gradle', 'build.gradle.kts', 'settings.gradle', 'pom.xml'}:
        tags.add('gradle')
    if 'package.json' in names:
        tags.add('node')
    if names & {'pyproject.toml', 'setup.py', 'setup.cfg'}:
        tags.add('python')
    kind = next((k for k in KIND_ORDER if k in tags), 'other')
    return {'kind': kind, 'tags': [k for k in KIND_ORDER if k in tags]}


# ---------------------------------------------------------------- per-file parsers

def new_res(kind):
    return {'kind': kind, 'facts': [], 'items': [], 'refs': [], 'vars': []}


BB_ASSIGN = re.compile(r'^\s*(?:export\s+)?([A-Za-z0-9_\-\.\$\{\}/:\[\]+~@]+?)\s*(\?\?=|\?=|:=|\+=|=\+|\.=|=\.|=)\s*(.*)$')
BB_FACTS = {
    'SUMMARY', 'DESCRIPTION', 'LICENSE', 'PV', 'PR', 'SRC_URI', 'SRCREV', 'SRCBRANCH', 'BRANCH', 'S', 'B',
    'DEPENDS', 'RDEPENDS', 'PROVIDES', 'RPROVIDES', 'COMPATIBLE_MACHINE', 'COMPATIBLE_HOST',
    'EXTRA_OECMAKE', 'EXTRA_OEMAKE', 'EXTRA_OECONF', 'EXTRA_OEMESON', 'PACKAGECONFIG', 'EXTERNALSRC',
    'IMAGE_INSTALL', 'IMAGE_FEATURES', 'IMAGE_FSTYPES', 'CORE_IMAGE_EXTRA_INSTALL', 'DISTRO_FEATURES',
    'DISTRO', 'DISTRO_NAME', 'DISTRO_VERSION', 'MACHINE', 'MACHINEOVERRIDES', 'SOC_FAMILY', 'DEFAULTTUNE',
    'TUNE_FEATURES', 'MACHINE_FEATURES', 'KERNEL_DEVICETREE', 'KERNEL_IMAGETYPE', 'KBUILD_DEFCONFIG',
    'KMACHINE', 'UBOOT_MACHINE', 'UBOOT_CONFIG', 'SERIAL_CONSOLES', 'BBFILE_COLLECTIONS', 'BBFILE_PATTERN',
    'BBFILE_PRIORITY', 'LAYERDEPENDS', 'LAYERSERIES_COMPAT', 'LAYERVERSION', 'BBFILES', 'BBLAYERS',
    'TARGET_ARCH', 'TCLIBC', 'INHERIT', 'BBMULTICONFIG', 'SDKMACHINE', 'PACKAGE_CLASSES',
}
BB_FACT_PREFIX = ('PREFERRED_PROVIDER', 'PREFERRED_VERSION', 'RDEPENDS', 'LAYERDEPENDS', 'LAYERSERIES_COMPAT',
                  'BBFILE_PRIORITY', 'BBFILE_PATTERN', 'SRCREV', 'MACHINE_ESSENTIAL', 'EXTERNALSRC')


def bb_base(k):
    return re.split(r'[:\[]', k)[0].replace('_append', '').replace('_prepend', '').replace('_remove', '')


def parse_bitbake(text):
    r = new_res('bitbake')
    for ln, line in logical_lines(text):
        s = line.strip()
        if not s or s.startswith('#'):
            continue
        m = re.match(r'^(inherit|inherit_defer)\s+(.+)$', s)
        if m:
            for c in m.group(2).split():
                if '$' not in c:
                    r['refs'].append({'t': 'inherit', 'target': c, 'line': ln})
            continue
        m = re.match(r'^(require|include|include_all)\s+(\S+)', s)
        if m:
            r['refs'].append({'t': m.group(1), 'target': m.group(2), 'line': ln})
            continue
        m = re.match(r'^(?:python\s+|fakeroot\s+)*([A-Za-z0-9_\-:${}]+)\s*\(\s*\)\s*\{', s)
        if m:
            r['items'].append({'t': 'task', 'name': m.group(1), 'line': ln})
            continue
        m = re.match(r'^addtask\s+(\S+)', s)
        if m:
            r['items'].append({'t': 'task', 'name': 'addtask ' + m.group(1), 'line': ln})
            continue
        m = BB_ASSIGN.match(line)
        if not m:
            continue
        k, op, v = m.group(1), m.group(2), unquote(m.group(3))
        v = re.sub(r'\s+', ' ', v).strip()
        if len(r['vars']) < 500:
            r['vars'].append({'k': k, 'op': op, 'v': v[:400], 'line': ln})
        base = bb_base(k)
        if base in BB_FACTS or base.startswith(BB_FACT_PREFIX):
            r['facts'].append({'k': k, 'v': v[:400], 'line': ln, 'op': op})
        if base == 'SRC_URI':
            for ent in v.split():
                if '://' in ent:
                    r['items'].append({'t': 'src', 'name': ent[:200], 'line': ln})
    return r


MK_ASSIGN = re.compile(r'^\s*(?:override\s+|export\s+|private\s+)*([A-Za-z_][A-Za-z0-9_.\-]*)\s*(\?=|:=|::=|\+=|!=|=)\s*(.*)$')
MK_INCLUDE = re.compile(r'^\s*-?s?include\s+(.+)$')
MK_RULE = re.compile(r'^([^\t#=:\s][^=:#]*?)\s*::?(?![=])\s*(.*)$')
MK_INHERIT = re.compile(r'\$\(call\s+inherit-product(?:-if-exists)?\s*,\s*([^)]+)\)')
MK_FACTS = {
    'ARCH', 'SUBARCH', 'CROSS_COMPILE', 'CC', 'CXX', 'LD', 'AR', 'CFLAGS', 'CPPFLAGS', 'LDFLAGS', 'TARGET',
    'TARGET_ARCH', 'TARGET_ARCH_VARIANT', 'TARGET_CPU_VARIANT', 'TARGET_CPU_ABI', 'TARGET_CPU_ABI2',
    'TARGET_2ND_ARCH', 'TARGET_2ND_ARCH_VARIANT', 'TARGET_2ND_CPU_VARIANT', 'TARGET_BOARD_PLATFORM',
    'TARGET_BOOTLOADER_BOARD_NAME', 'TARGET_KERNEL_CONFIG', 'TARGET_KERNEL_SOURCE', 'BOARD_KERNEL_CMDLINE',
    'TARGET_NO_BOOTLOADER', 'PRODUCT_NAME', 'PRODUCT_DEVICE', 'PRODUCT_BRAND', 'PRODUCT_MODEL',
    'PRODUCT_MANUFACTURER', 'PRODUCT_MAKEFILES', 'COMMON_LUNCH_CHOICES', 'PRODUCT_PACKAGES', 'LOCAL_MODULE',
    'LOCAL_MODULE_CLASS', 'LIST', 'CPU', 'VARIANT', 'VARIANT_LIST', 'EXCLUDE_CPU', 'EXCLUDE_VARIANT', 'CPULIST',
    'NAME', 'INSTALLDIR', 'USEFILE', 'LIBS', 'EXTRA_INCVPATH', 'EXTRA_LIBVPATH', 'EXTRA_SRCVPATH', 'PINFO',
    'QNX_TARGET', 'QNX_HOST', 'VERSION', 'PATCHLEVEL', 'SUBLEVEL', 'EXTRAVERSION', 'PLATFORM', 'BOARD', 'SOC',
    'CHIP', 'MACHINE', 'KERNEL_DIR', 'KDIR', 'SDK', 'SYSROOT', 'TOOLCHAIN', 'PREFIX', 'obj-y', 'obj-m',
}


def parse_make(text):
    r = new_res('make')
    local_module = None
    targets = []
    for ln, line in logical_lines(text):
        if line.startswith('\t'):
            continue
        s = line.strip()
        if not s or s.startswith('#'):
            continue
        for m in MK_INHERIT.finditer(s):
            r['refs'].append({'t': 'inherit-product', 'target': m.group(1).strip(), 'line': ln})
        m = MK_INCLUDE.match(s)
        if m:
            tgt = m.group(1).strip()
            b = re.search(r'\$\((BUILD_[A-Z_]+|CLEAR_VARS)\)', tgt)
            if b:
                if b.group(1) != 'CLEAR_VARS':
                    r['items'].append({'t': 'module', 'name': local_module or '?', 'line': ln, 'extra': b.group(1)})
                continue
            for t in tgt.split():
                if re.search(r'recurse\.mk|qtargets\.mk|qmacros\.mk|QCONFIG', t):
                    r['facts'].append({'k': 'QNX build', 'v': t, 'line': ln})
                r['refs'].append({'t': 'include', 'target': t, 'line': ln})
            continue
        if re.match(r'^(ifeq|ifneq|ifdef|ifndef|else|endif|define|endef|undefine|vpath|\$\()', s):
            continue
        m = MK_ASSIGN.match(s)
        if m:
            k, op, v = m.group(1), m.group(2), m.group(3).strip()
            if len(r['vars']) < 500:
                r['vars'].append({'k': k, 'op': op, 'v': v[:400], 'line': ln})
            if k in MK_FACTS:
                r['facts'].append({'k': k, 'v': v[:400], 'line': ln, 'op': op})
            if k == 'LOCAL_MODULE':
                local_module = v
            if k in ('PRODUCT_MAKEFILES',):
                for t in v.split():
                    if t.endswith('.mk'):
                        r['refs'].append({'t': 'product', 'target': t, 'line': ln})
            continue
        m = MK_RULE.match(s)
        if m:
            for t in m.group(1).split():
                if t.startswith('.') or '$' in t or t in targets:
                    continue
                targets.append(t)
                if len(targets) <= 80:
                    r['items'].append({'t': 'target', 'name': t, 'line': ln})
    blob = text[:20000]
    for m in re.finditer(r'qcc\s+-V\s*([A-Za-z0-9_]+)', blob):
        r['facts'].append({'k': 'qcc -V', 'v': m.group(1), 'line': text[:m.start()].count('\n') + 1})
    return r


CM_CMD = re.compile(r'\b(project|add_executable|add_library|add_subdirectory|include|find_package|set|'
                    r'cmake_minimum_required|option|add_custom_target)\s*\(([^()]*(?:\([^()]*\)[^()]*)*)\)', re.I)
CM_FACTS = {'CMAKE_SYSTEM_NAME', 'CMAKE_SYSTEM_PROCESSOR', 'CMAKE_C_COMPILER', 'CMAKE_CXX_COMPILER',
            'CMAKE_C_COMPILER_TARGET', 'CMAKE_CXX_COMPILER_TARGET', 'CMAKE_SYSROOT', 'CMAKE_TOOLCHAIN_FILE',
            'CMAKE_CXX_STANDARD', 'CMAKE_C_STANDARD', 'CMAKE_BUILD_TYPE', 'CMAKE_FIND_ROOT_PATH', 'QNX_PROCESSOR',
            'CMAKE_ANDROID_ARCH_ABI', 'ANDROID_ABI', 'ANDROID_PLATFORM', 'CMAKE_SYSTEM_VERSION', 'QNX_HOST',
            'QNX_TARGET', 'TOOLCHAIN_PREFIX', 'CROSS_COMPILE'}


def strip_hash_comments(text):
    return re.sub(r'(?m)(^|\s)#[^\n]*', lambda m: m.group(1) + ' ' * (len(m.group(0)) - len(m.group(1))), text)


def parse_cmake(text):
    r = new_res('cmake')
    t = strip_hash_comments(text)
    for m in CM_CMD.finditer(t):
        cmd = m.group(1).lower()
        args = m.group(2).split()
        ln = t.count('\n', 0, m.start()) + 1
        if not args and cmd != 'project':
            continue
        a0 = unquote(args[0]) if args else ''
        if cmd == 'project':
            r['facts'].append({'k': 'project', 'v': ' '.join(args)[:200], 'line': ln})
        elif cmd in ('add_executable', 'add_library', 'add_custom_target'):
            kind = {'add_executable': 'exe', 'add_library': 'lib', 'add_custom_target': 'target'}[cmd]
            if len(args) > 1 and args[1].upper() in ('SHARED', 'STATIC', 'MODULE', 'INTERFACE', 'OBJECT'):
                kind = args[1].lower() + ' lib'
            r['items'].append({'t': kind, 'name': a0, 'line': ln})
        elif cmd == 'add_subdirectory':
            r['refs'].append({'t': 'subdir', 'target': a0, 'line': ln})
        elif cmd == 'include':
            r['refs'].append({'t': 'include', 'target': a0, 'line': ln})
        elif cmd == 'find_package':
            r['items'].append({'t': 'package', 'name': a0, 'line': ln})
        elif cmd == 'cmake_minimum_required':
            r['facts'].append({'k': 'cmake_minimum_required', 'v': ' '.join(args), 'line': ln})
        elif cmd == 'option':
            r['vars'].append({'k': a0, 'op': 'option', 'v': ' '.join(args[1:])[:200], 'line': ln})
        elif cmd == 'set':
            v = ' '.join(unquote(x) for x in args[1:] if x.upper() not in ('CACHE', 'STRING', 'PATH', 'FILEPATH', 'BOOL', 'FORCE', 'INTERNAL'))
            v = re.sub(r'"[^"]*"\s*$', '', v).strip() if 'CACHE' in args else v
            if len(r['vars']) < 400:
                r['vars'].append({'k': a0, 'op': 'set', 'v': v[:400], 'line': ln})
            if a0 in CM_FACTS:
                r['facts'].append({'k': a0, 'v': v[:400], 'line': ln})
                if a0 == 'CMAKE_TOOLCHAIN_FILE':
                    r['refs'].append({'t': 'toolchain', 'target': v.split()[0] if v else '', 'line': ln})
    return r


def parse_cmake_presets(text):
    r = new_res('cmake-presets')
    try:
        j = json.loads(text)
    except Exception:
        return r
    for p in j.get('configurePresets', []) or []:
        extra = []
        if p.get('toolchainFile'):
            extra.append('toolchain=' + p['toolchainFile'])
            r['refs'].append({'t': 'toolchain', 'target': p['toolchainFile'], 'line': None})
        cv = p.get('cacheVariables') or {}
        for k in ('CMAKE_SYSTEM_NAME', 'CMAKE_SYSTEM_PROCESSOR', 'CMAKE_TOOLCHAIN_FILE', 'CMAKE_BUILD_TYPE'):
            if k in cv:
                val = cv[k]['value'] if isinstance(cv[k], dict) else cv[k]
                r['facts'].append({'k': k + ' (' + p.get('name', '?') + ')', 'v': str(val), 'line': None})
        r['items'].append({'t': 'preset', 'name': p.get('name', '?'), 'line': None, 'extra': ' '.join(extra)})
    return r


def strip_c_comments(text):
    def rep(m):
        return re.sub(r'[^\n]', ' ', m.group(0))
    return re.sub(r'/\*.*?\*/|//[^\n]*', rep, text, flags=re.S)


def parse_bp(text):
    r = new_res('android-bp')
    t = strip_c_comments(text)
    i, n, depth, line = 0, len(t), 0, 1
    for m in re.finditer(r'(?m)^([A-Za-z_][A-Za-z0-9_]*)\s*\{', t):
        start = m.end()
        d, j = 1, start
        while j < n and d:
            c = t[j]
            if c == '{':
                d += 1
            elif c == '}':
                d -= 1
            j += 1
        block = t[start:j]
        ln = t.count('\n', 0, m.start()) + 1
        nm = re.search(r'\bname\s*:\s*"([^"]+)"', block)
        archs = sorted(set(re.findall(r'\b(arm64|arm|x86_64|x86|riscv64)\s*:\s*\{', block)))
        r['items'].append({'t': m.group(1), 'name': nm.group(1) if nm else '(' + m.group(1) + ')', 'line': ln,
                           'extra': ('arch: ' + ','.join(archs)) if archs else ''})
        if archs:
            r['facts'].append({'k': 'arch-specific', 'v': ','.join(archs), 'line': ln})
    return r


def parse_dts(text):
    r = new_res('devicetree')
    t = strip_c_comments(text)
    for m in re.finditer(r'#include\s+[<"]([^>"]+)[>"]|/include/\s+"([^"]+)"', t):
        r['refs'].append({'t': 'include', 'target': m.group(1) or m.group(2), 'line': t.count('\n', 0, m.start()) + 1})
    m = re.search(r'(?m)^\s*/\s*\{', t)
    if m:
        d, j, n = 1, m.end(), len(t)
        seg_start = j
        while j < n and d:
            c = t[j]
            if c == '{':
                d += 1
            elif c == '}':
                d -= 1
            elif d == 1 and t.startswith('model', j) and re.match(r'model\s*=', t[j:j + 20]):
                mm = re.match(r'model\s*=\s*"([^"]*)"', t[j:j + 300])
                if mm:
                    r['facts'].append({'k': 'model', 'v': mm.group(1), 'line': t.count('\n', 0, j) + 1})
            elif d == 1 and t.startswith('compatible', j) and (j == 0 or not t[j - 1].isalnum()):
                mm = re.match(r'compatible\s*=\s*([^;]+);', t[j:j + 400])
                if mm:
                    vals = re.findall(r'"([^"]+)"', mm.group(1))
                    if vals:
                        r['facts'].append({'k': 'compatible', 'v': ', '.join(vals), 'line': t.count('\n', 0, j) + 1})
            j += 1
    cores = Counter(re.findall(r'"(arm,cortex-[a-z0-9]+|arm,neoverse-[a-z0-9]+|riscv|intel,[a-z0-9-]+)"', t))
    for c, cnt in cores.most_common(4):
        if c.startswith(('arm,cortex-a', 'arm,neoverse', 'riscv')):
            ln = t.count('\n', 0, t.find('"' + c + '"')) + 1
            r['facts'].append({'k': 'cpu core', 'v': c + (' x%d' % cnt if cnt > 1 else ''), 'line': ln})
    return r


def parse_defconfig(text):
    r = new_res('defconfig')
    n = 0
    for ln, line in enumerate(text.splitlines(), 1):
        m = re.match(r'^(CONFIG_[A-Z0-9_]+)=(.*)$', line.strip())
        if not m:
            continue
        n += 1
        k, v = m.group(1), m.group(2)
        if len(r['vars']) < 400:
            r['vars'].append({'k': k, 'op': '=', 'v': v, 'line': ln})
        if re.match(r'CONFIG_(ARCH_[A-Z0-9_]+|SOC_[A-Z0-9_]+|MACH_[A-Z0-9_]+|ARM64|ARM|X86_64|RISCV|64BIT|LOCALVERSION|TARGET_[A-Z0-9_]+|SYS_SOC|SYS_BOARD|SYS_VENDOR|SYS_CONFIG_NAME|DEFAULT_DEVICE_TREE)$', k):
            if v != 'n':
                r['facts'].append({'k': k, 'v': v, 'line': ln})
    r['facts'].insert(0, {'k': 'số option', 'v': str(n), 'line': None})
    return r


def parse_qnx_build(text):
    r = new_res('qnx-buildfile')
    for ln, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        for m in re.finditer(r'\[\s*virtual\s*=\s*([^,\]\s]+)', s):
            r['facts'].append({'k': 'virtual (arch)', 'v': m.group(1), 'line': ln})
        for m in re.finditer(r'\[\s*image\s*=\s*([^\]\s]+)', s):
            r['facts'].append({'k': 'image', 'v': m.group(1), 'line': ln})
        m = re.match(r'^(startup-[A-Za-z0-9_\-]+)', s)
        if m:
            r['facts'].append({'k': 'startup (board)', 'v': m.group(1), 'line': ln})
        m = re.match(r'^(PATH|LD_LIBRARY_PATH)\s*=', s)
        if s and not s.startswith(('#', '[', '{', '}')) and not m and re.match(r'^[A-Za-z0-9_./\-]+(\s|$)', s):
            if len(r['items']) < 120:
                r['items'].append({'t': 'entry', 'name': s.split()[0][:120], 'line': ln})
    return r


def parse_meson(text):
    r = new_res('meson')
    t = strip_hash_comments(text)
    for m in re.finditer(r"\b(project|executable|library|shared_library|static_library|subdir|dependency)\s*\(\s*'([^']+)'", t):
        ln = t.count('\n', 0, m.start()) + 1
        c = m.group(1)
        if c == 'project':
            r['facts'].append({'k': 'project', 'v': m.group(2), 'line': ln})
        elif c == 'subdir':
            r['refs'].append({'t': 'subdir', 'target': m.group(2), 'line': ln})
        elif c == 'dependency':
            r['items'].append({'t': 'dependency', 'name': m.group(2), 'line': ln})
        else:
            r['items'].append({'t': c, 'name': m.group(2), 'line': ln})
    sect = None
    for ln, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if s.startswith('['):
            sect = s.strip('[]')
            continue
        if sect in ('host_machine', 'target_machine', 'binaries'):
            m = re.match(r'(\w+)\s*=\s*(.+)', s)
            if m:
                r['facts'].append({'k': sect + '.' + m.group(1), 'v': unquote(m.group(2)), 'line': ln})
    return r


def parse_autotools(text):
    r = new_res('autotools')
    for ln, line in enumerate(text.splitlines(), 1):
        m = re.search(r'AC_INIT\(\s*\[?([^\],)]+)\]?\s*,\s*\[?([^\],)]*)', line)
        if m:
            r['facts'].append({'k': 'AC_INIT', 'v': m.group(1).strip() + ' ' + m.group(2).strip(), 'line': ln})
        if 'AC_CANONICAL_HOST' in line:
            r['facts'].append({'k': 'cross-compile', 'v': 'AC_CANONICAL_HOST (dùng --host=)', 'line': ln})
        m = re.search(r'(SUBDIRS)\s*=\s*(.+)', line)
        if m:
            r['facts'].append({'k': 'SUBDIRS', 'v': m.group(2).strip(), 'line': ln})
        m = re.search(r'\b(\w+)_(PROGRAMS|LTLIBRARIES|LIBRARIES)\s*[+]?=\s*(.+)', line)
        if m:
            for x in m.group(3).split():
                r['items'].append({'t': m.group(2).lower(), 'name': x, 'line': ln})
    return r


def parse_generic(text, name):
    r = new_res('manifest')
    if name == 'package.json':
        try:
            j = json.loads(text)
            r['facts'].append({'k': 'name', 'v': '%s %s' % (j.get('name', ''), j.get('version', '')), 'line': None})
            for k in (j.get('scripts') or {}):
                r['items'].append({'t': 'script', 'name': 'npm run ' + k, 'line': None})
        except Exception:
            pass
        return r
    for ln, line in enumerate(text.splitlines(), 1):
        m = re.match(r'^\s*(name|version|module|edition|requires-python)\s*=?\s*(.+)$', line)
        if m and len(r['facts']) < 8:
            r['facts'].append({'k': m.group(1), 'v': unquote(m.group(2)), 'line': ln})
    return r


def parse_kconfig(text):
    r = new_res('kconfig')
    for ln, line in enumerate(text.splitlines(), 1):
        m = re.match(r'^\s*(menuconfig|config)\s+(\w+)', line)
        if m and len(r['items']) < 200:
            r['items'].append({'t': m.group(1), 'name': m.group(2), 'line': ln})
        m = re.match(r'^\s*source\s+"?([^"\s]+)', line)
        if m:
            r['refs'].append({'t': 'source', 'target': m.group(1), 'line': ln})
    return r


def parse_file(rel, text, tags=()):
    name = os.path.basename(rel)
    low = name.lower()
    p = '/' + rel
    is_bb_ctx = any(x in p for x in ('/recipes-', '/conf/', '/classes', '/images/')) or \
        any(t in tags for t in ('yocto-layer', 'yocto-multi', 'bitbake'))
    if low.endswith(('.bb', '.bbappend', '.bbclass')) or (low.endswith('.inc') and is_bb_ctx) or \
            (low.endswith(('.conf', '.conf.sample')) and is_bb_ctx):
        return parse_bitbake(text)
    if name == 'CMakeLists.txt' or low.endswith('.cmake'):
        return parse_cmake(text)
    if name in ('CMakePresets.json', 'CMakeUserPresets.json'):
        return parse_cmake_presets(text)
    if name == 'Android.bp':
        return parse_bp(text)
    if low.endswith(('.dts', '.dtsi', '.dtso')):
        return parse_dts(text)
    if 'defconfig' in low or (low.endswith('_config') and '/configs/' in p):
        return parse_defconfig(text)
    if low.endswith('.build') and ('[virtual=' in text or '[image=' in text or 'procnto' in text):
        return parse_qnx_build(text)
    if name in ('Makefile', 'makefile', 'GNUmakefile', 'Kbuild') or low.endswith('.mk') or name == 'Makefile.am' and False:
        return parse_make(text)
    if name in ('meson.build', 'meson_options.txt', 'meson.options') or (low.endswith(('.ini', '.txt')) and '[host_machine]' in text):
        return parse_meson(text)
    if name in ('configure.ac', 'configure.in', 'Makefile.am'):
        return parse_autotools(text)
    if name in ('Cargo.toml', 'package.json', 'pyproject.toml', 'go.mod', 'setup.cfg'):
        return parse_generic(text, name)
    if name.startswith('Kconfig'):
        return parse_kconfig(text)
    return new_res('text')


# ---------------------------------------------------------------- categories

CATS = [
    # key, label, group
    ('yocto-layer', 'layer.conf', 'Yocto'),
    ('yocto-machine', 'MACHINE (conf/machine)', 'Yocto'),
    ('yocto-distro', 'DISTRO (conf/distro)', 'Yocto'),
    ('yocto-image', 'Image recipes', 'Yocto'),
    ('yocto-recipe', 'Recipes .bb / .bbappend', 'Yocto'),
    ('yocto-inc', 'BitBake .inc', 'Yocto'),
    ('yocto-class', 'Classes .bbclass', 'Yocto'),
    ('yocto-buildconf', 'local/bblayers conf', 'Yocto'),
    ('yocto-wks', 'WIC .wks', 'Yocto'),
    ('android-product', 'AndroidProducts.mk', 'Android'),
    ('android-board', 'BoardConfig*.mk', 'Android'),
    ('android-productmk', 'Product / device .mk', 'Android'),
    ('android-bp', 'Android.bp', 'Android'),
    ('android-mk', 'Android.mk', 'Android'),
    ('qnx-buildfile', 'QNX buildfile (.build)', 'QNX'),
    ('qnx-make', 'QNX common.mk / Makefile', 'QNX'),
    ('kconfig-defconfig', 'defconfig', 'Kernel'),
    ('kconfig', 'Kconfig', 'Kernel'),
    ('devicetree', 'Device tree .dts', 'Kernel'),
    ('devicetree-inc', 'Device tree .dtsi', 'Kernel'),
    ('cmake-toolchain', 'CMake toolchain', 'CMake'),
    ('cmake', 'CMakeLists / .cmake', 'CMake'),
    ('meson', 'Meson', 'Meson'),
    ('autotools', 'Autotools', 'Autotools'),
    ('bazel', 'Bazel', 'Bazel'),
    ('make', 'Makefile / .mk', 'Make'),
    ('other-build', 'Build khác', 'Khác'),
    ('ci', 'CI / Docker', 'Khác'),
    ('docs', 'README', 'Khác'),
]
CAT_META = {k: {'label': l, 'group': g} for k, l, g in CATS}
CAT_ORDER = {k: i for i, (k, _, _) in enumerate(CATS)}

PARSE_BUDGET = {
    'yocto-layer': 12, 'yocto-machine': 40, 'yocto-distro': 12, 'yocto-image': 30, 'yocto-recipe': 80,
    'yocto-inc': 20, 'yocto-buildconf': 6, 'android-product': 10, 'android-board': 10,
    'android-productmk': 20, 'android-bp': 20, 'android-mk': 20, 'qnx-buildfile': 12, 'qnx-make': 30,
    'kconfig-defconfig': 40, 'devicetree': 50, 'cmake-toolchain': 12, 'cmake': 20, 'meson': 8,
    'autotools': 4, 'make': 20, 'other-build': 8,
}


def categorize(rel, tags):
    name = rel.rsplit('/', 1)[-1]
    low = name.lower()
    p = '/' + rel
    if p.endswith('/conf/layer.conf'):
        return 'yocto-layer'
    if '/conf/machine/' in p and low.endswith('.conf'):
        return 'yocto-machine'
    if '/conf/distro/' in p and low.endswith('.conf'):
        return 'yocto-distro'
    if low.endswith('.bb'):
        return 'yocto-image' if '/images/' in p else 'yocto-recipe'
    if low.endswith('.bbappend'):
        return 'yocto-recipe'
    if low.endswith('.bbclass'):
        return 'yocto-class'
    if low in ('local.conf.sample', 'bblayers.conf.sample', 'local.conf', 'bblayers.conf', 'site.conf',
               'conf-notes.txt', 'templateconf.cfg'):
        return 'yocto-buildconf'
    if low.endswith(('.wks', '.wks.in')):
        return 'yocto-wks'
    if low.endswith('.inc') and any(x in p for x in ('/recipes-', '/conf/', '/classes')):
        return 'yocto-inc'
    if name == 'AndroidProducts.mk':
        return 'android-product'
    if name.startswith('BoardConfig') and low.endswith('.mk'):
        return 'android-board'
    if name == 'Android.bp':
        return 'android-bp'
    if name == 'Android.mk':
        return 'android-mk'
    if 'android-device' in tags and low.endswith('.mk') and name not in ('Makefile',):
        return 'android-productmk'
    if low.endswith('.build') and 'qnx' in tags:
        return 'qnx-buildfile'
    if 'qnx' in tags and (name == 'common.mk' or name == 'Makefile'):
        return 'qnx-make'
    if 'defconfig' in low or (low.endswith('_config') and '/configs/' in p):
        return 'kconfig-defconfig'
    if name == 'Kconfig' or name.startswith('Kconfig.'):
        return 'kconfig'
    if low.endswith(('.dts', '.dtso')):
        return 'devicetree'
    if low.endswith('.dtsi'):
        return 'devicetree-inc'
    if low.endswith('.cmake') and re.search(r'toolchain|cross|qnx|aarch64|arm|android|x86|riscv', low):
        return 'cmake-toolchain'
    if name in ('CMakeLists.txt', 'CMakePresets.json') or low.endswith('.cmake'):
        return 'cmake'
    if name in ('meson.build', 'meson_options.txt', 'meson.options') or (low.endswith('.ini') and 'cross' in low):
        return 'meson'
    if name in ('configure.ac', 'configure.in', 'Makefile.am'):
        return 'autotools'
    if name in ('BUILD', 'BUILD.bazel', 'WORKSPACE', 'MODULE.bazel', 'WORKSPACE.bazel', '.bazelrc'):
        return 'bazel'
    if name in ('Makefile', 'makefile', 'GNUmakefile', 'Kbuild') or low.endswith('.mk'):
        return 'make'
    if name in ('Cargo.toml', 'package.json', 'pyproject.toml', 'setup.py', 'setup.cfg', 'go.mod',
                'build.gradle', 'build.gradle.kts', 'pom.xml', 'build.sh', 'build.py', 'SConstruct'):
        return 'other-build'
    if name in ('Dockerfile', 'Jenkinsfile', '.gitlab-ci.yml') or '/.github/workflows/' in p:
        return 'ci'
    if low.startswith('readme') and '/' not in rel:
        return 'docs'
    return None


QNX_CPUS = {'aarch64', 'arm', 'x86_64', 'x86', 'ppc', 'mips', 'sh', 'armle-v7', 'aarch64le'}


# ---------------------------------------------------------------- workspace

class Workspace:
    def __init__(self, root, workers=16):
        self.root = os.path.abspath(root)
        self.real_root = os.path.realpath(self.root)
        self.workers = workers
        self.manifest = None
        self.mode = 'scan'
        self.repos = []            # list of dict
        self.by_path = {}
        self.project_paths = set()
        self.analysis = {}
        self.parse_cache = {}
        self.file_index = {}       # repo -> {basename: [rel]}
        self.lock = threading.Lock()
        self.index = {'state': 'idle', 'done': 0, 'total': 0, 'layers': [], 'machines': [], 'images': [],
                      'distros': [], 'products': [], 'qnx': [], 'xref_count': 0, 'took': None}
        self.xref = defaultdict(list)
        self.layer_roots = []      # (repo, layer_dir_rel_in_repo)
        self.bbclasses = {}
        self.pkg_images = defaultdict(list)    # package -> [image]
        self.pkg_products = defaultdict(list)  # android module -> [product]
        self.scan_took = 0

    # ---- discovery
    def load(self):
        t0 = time.time()
        m = parse_manifest(self.root)
        if m and m['projects']:
            self.mode = 'manifest'
            self.manifest = {k: m[k] for k in ('files', 'remotes', 'default')}
            for p in m['projects']:
                self.repos.append(p)
        else:
            for path in discover_git_repos(self.root):
                self.repos.append({'name': path, 'path': path, 'remote': None, 'revision': None, 'groups': '',
                                   'url': None, 'links': []})
        self.repos.sort(key=lambda r: r['path'])
        self.by_path = {r['path']: r for r in self.repos}
        self.project_paths = set(self.by_path)
        log('  %d repo (%s) ...' % (len(self.repos), 'manifest' if self.mode == 'manifest' else 'quet .git'))

        def cls(r):
            c = quick_classify(os.path.join(self.root, r['path']))
            r.update(c)
        with ThreadPoolExecutor(self.workers) as ex:
            list(ex.map(cls, self.repos))
        self.scan_took = time.time() - t0
        cnt = Counter(r['kind'] for r in self.repos)
        log('  phan loai xong %.1fs: %s' % (self.scan_took, ', '.join('%s=%d' % kv for kv in cnt.most_common())))

    def abs(self, repo, rel=''):
        return os.path.normpath(os.path.join(self.root, repo, rel))

    def safe(self, path):
        real = os.path.realpath(path)
        return real == self.real_root or real.startswith(self.real_root + os.sep)

    def owner_of(self, abspath):
        rel = os.path.relpath(abspath, self.root).replace(os.sep, '/')
        if rel.startswith('..'):
            return None, None
        parts = rel.split('/')
        for i in range(len(parts) - 1, 0, -1):
            cand = '/'.join(parts[:i])
            if cand in self.by_path:
                return cand, '/'.join(parts[i:])
        if '.' in self.by_path:
            return '.', rel
        return None, None

    # ---- index (background)
    def build_index(self):
        t0 = time.time()
        idx = self.index
        idx['state'] = 'running'
        yrepos = [r for r in self.repos if set(r.get('tags', [])) & {'yocto-layer', 'yocto-multi'}]
        arepos = [r for r in self.repos if 'android-device' in r.get('tags', [])]
        qrepos = [r for r in self.repos if 'qnx' in r.get('tags', [])]
        idx['total'] = len(yrepos) + len(arepos) + len(qrepos)
        # 1) layer roots
        layers = []
        for r in yrepos:
            base = self.abs(r['path'])
            for dirpath, dirnames, filenames in os.walk(base):
                rel = os.path.relpath(dirpath, base)
                depth = 0 if rel == '.' else rel.count(os.sep) + 1
                if os.path.isfile(os.path.join(dirpath, 'conf', 'layer.conf')):
                    layers.append((r['path'], '' if rel == '.' else rel.replace(os.sep, '/')))
                dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(('recipes', 'classes', 'conf', 'files', 'lib', 'scripts', 'doc'))]
                if depth >= 3:
                    dirnames[:] = []
        self.layer_roots = layers
        # 2) classes first (for inherit resolution)
        for repo, ldir in layers:
            cdir = self.abs(repo, os.path.join(ldir, 'classes'))
            for sub in ('classes', 'classes-recipe', 'classes-global'):
                d = self.abs(repo, os.path.join(ldir, sub))
                if os.path.isdir(d):
                    for fn in os.listdir(d):
                        if fn.endswith('.bbclass'):
                            self.bbclasses.setdefault(fn[:-8], (repo, (ldir + '/' if ldir else '') + sub + '/' + fn))
        # 3) per layer details
        name_map = defaultdict(list)
        for r in self.repos:
            nm = r['name'].rstrip('/')
            if nm.endswith('.git'):
                nm = nm[:-4]
            name_map[nm.rsplit('/', 1)[-1].lower()].append(r)

        def do_layer(item):
            repo, ldir = item
            base = self.abs(repo, ldir)
            lc = read_text(os.path.join(base, 'conf', 'layer.conf')) or ''
            res = parse_bitbake(lc)
            info = {'repo': repo, 'dir': ldir, 'name': os.path.basename(ldir) or os.path.basename(repo),
                    'collection': None, 'priority': None, 'depends': '', 'compat': '', 'machines': [], 'images': 0,
                    'recipes': 0}
            for f in res['facts']:
                b = bb_base(f['k'])
                if b == 'BBFILE_COLLECTIONS':
                    info['collection'] = f['v'].split()[0] if f['v'] else None
                elif b.startswith('BBFILE_PRIORITY'):
                    info['priority'] = f['v']
                elif b.startswith('LAYERDEPENDS'):
                    info['depends'] = (info['depends'] + ' ' + f['v']).strip()
                elif b.startswith('LAYERSERIES_COMPAT'):
                    info['compat'] = f['v']
            machines, images, distros, xrefs = [], [], [], []
            mdir = os.path.join(base, 'conf', 'machine')
            if os.path.isdir(mdir):
                for fn in sorted(os.listdir(mdir)):
                    if fn.endswith('.conf'):
                        mrel = (ldir + '/' if ldir else '') + 'conf/machine/' + fn
                        machines.append(self.machine_info(repo, mrel, fn[:-5]))
            ddir = os.path.join(base, 'conf', 'distro')
            if os.path.isdir(ddir):
                for fn in sorted(os.listdir(ddir)):
                    if fn.endswith('.conf'):
                        distros.append({'name': fn[:-5], 'repo': repo,
                                        'file': (ldir + '/' if ldir else '') + 'conf/distro/' + fn, 'layer': info['name']})
            nrec = 0
            for dirpath, dirnames, filenames in os.walk(base):
                rel = os.path.relpath(dirpath, base)
                depth = 0 if rel == '.' else rel.count(os.sep) + 1
                if depth == 1 and os.path.isfile(os.path.join(dirpath, 'conf', 'layer.conf')):
                    dirnames[:] = []
                    continue
                dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and d != 'files']
                if depth >= 6:
                    dirnames[:] = []
                for fn in filenames:
                    if not fn.endswith(('.bb', '.bbappend', '.inc')):
                        continue
                    frel_layer = (rel.replace(os.sep, '/') + '/' if rel != '.' else '') + fn
                    frel = (ldir + '/' if ldir else '') + frel_layer
                    if fn.endswith('.bb'):
                        nrec += 1
                        if '/images/' in '/' + frel_layer + '/' or rel.endswith('images'):
                            images.append({'name': fn[:-3], 'repo': repo, 'file': frel, 'layer': info['name']})
                    txt = read_text(os.path.join(dirpath, fn), 200_000)
                    if not txt or ('SRC_URI' not in txt and 'EXTERNALSRC' not in txt):
                        continue
                    for ln, line in logical_lines(txt):
                        m = re.match(r'^\s*(SRC_URI|EXTERNALSRC)([:_\w\-${}]*)?\s*(\?\?=|\?=|:=|\+=|=\+|\.=|=\.|=)\s*(.*)$', line)
                        if not m:
                            continue
                        val = unquote(m.group(4))
                        if m.group(1) == 'EXTERNALSRC':
                            xrefs.append({'kind': 'externalsrc', 'value': val, 'repo': repo, 'file': frel, 'line': ln})
                            continue
                        for ent in val.split():
                            if '://' not in ent:
                                continue
                            scheme = ent.split('://', 1)[0]
                            urlpart = ent.split(';', 1)[0]
                            params = ent.split(';')[1:]
                            is_git = scheme in ('git', 'gitsm', 'ssh', 'repo') or any(p.startswith('protocol=') for p in params) or urlpart.endswith('.git')
                            if not is_git:
                                continue
                            xrefs.append({'kind': scheme, 'value': urlpart, 'params': ';'.join(params)[:120],
                                          'repo': repo, 'file': frel, 'line': ln})
            info['machines'] = [m['name'] for m in machines]
            info['images'] = len(images)
            info['recipes'] = nrec
            return info, machines, images, distros, xrefs

        results = []
        with ThreadPoolExecutor(self.workers) as ex:
            for res in ex.map(do_layer, layers):
                results.append(res)
                idx['done'] += 0  # per layer; repo progress below
        idx['done'] = len(yrepos)
        xcount = 0
        for info, machines, images, distros, xrefs in results:
            idx['layers'].append(info)
            idx['machines'].extend(machines)
            idx['images'].extend(images)
            idx['distros'].extend(distros)
            for x in xrefs:
                for target, conf in self.match_xref(x, name_map):
                    self.xref[target].append({**x, 'conf': conf, 'layer': info['name']})
                    xcount += 1
        idx['xref_count'] = xcount
        for im in idx['images']:
            res = self.parse_one(im['repo'], im['file'], {'yocto-layer'})
            if not res:
                continue
            for f in res['facts']:
                if bb_base(f['k']) in ('IMAGE_INSTALL', 'CORE_IMAGE_EXTRA_INSTALL'):
                    for tok in f['v'].split():
                        if '$' not in tok and im['name'] not in self.pkg_images[tok]:
                            self.pkg_images[tok].append(im['name'])
        # Android products
        for r in arepos:
            idx['products'].extend(self.android_products(r))
            idx['done'] += 1
        for r in qrepos:
            idx['qnx'].extend(self.qnx_images(r))
            idx['done'] += 1
        idx['took'] = round(time.time() - t0, 1)
        idx['state'] = 'done'
        log('  index xong %.1fs: %d layer, %d machine, %d image, %d product, %d lien ket recipe->repo'
              % (idx['took'], len(idx['layers']), len(idx['machines']), len(idx['images']), len(idx['products']), xcount))

    def match_xref(self, x, name_map):
        if x['kind'] == 'externalsrc':
            v = re.sub(r'\$\{[^}]*\}', '', x['value']).strip('/')
            comps = [c for c in v.split('/') if c and c != '..' and c != '.']
            best, bs = [], 0
            for r in self.repos:
                rc = r['path'].split('/')
                s = 0
                while s < min(len(rc), len(comps)) and rc[-1 - s] == comps[-1 - s]:
                    s += 1
                if s > bs:
                    best, bs = [r['path']], s
                elif s == bs and s:
                    best.append(r['path'])
            return [(p, 'medium') for p in best[:3]] if bs else []
        url = x['value']
        path = url.split('://', 1)[-1]
        comps = [c for c in path.split('/') if c]
        if not comps:
            return []
        last = comps[-1]
        if last.endswith('.git'):
            last = last[:-4]
            comps[-1] = last
        cands = name_map.get(last.lower(), [])
        if not cands:
            return []
        scored = []
        for r in cands:
            nc = r['name'].rstrip('/')
            nc = (nc[:-4] if nc.endswith('.git') else nc).split('/')
            s = 0
            while s < min(len(nc), len(comps)) and nc[-1 - s].lower() == comps[-1 - s].lower():
                s += 1
            scored.append((s, r))
        top = max(s for s, _ in scored)
        best = [r for s, r in scored if s == top]
        conf = 'high' if top >= 2 or len(best) == 1 and len(cands) == 1 else 'medium'
        if len(best) > 1:
            conf = 'low'
        return [(r['path'], conf) for r in best[:4]]

    def resolve_bb(self, target, from_repo, from_file):
        if '${' in target:
            target = target.replace('${COREBASE}/', '').replace('${LAYERDIR}/', '')
            if '${' in target:
                return None
        cands = []
        if from_file is not None:
            cands.append(self.abs(from_repo, os.path.join(os.path.dirname(from_file), target)))
        for repo, ldir in self.layer_roots:
            cands.append(self.abs(repo, os.path.join(ldir, target)))
        for c in cands:
            if os.path.isfile(c):
                return c
        return None

    def machine_info(self, repo, mrel, name):
        info = {'name': name, 'repo': repo, 'file': mrel, 'arch': None, 'tune': None, 'soc': None,
                'kernel': None, 'bootloader': None, 'dtb': None, 'layer': None, 'overrides': ''}
        seen = set()

        def walk(abspath, depth):
            if depth > 4 or abspath in seen:
                return
            seen.add(abspath)
            txt = read_text(abspath, 200_000)
            if not txt:
                return
            res = parse_bitbake(txt)
            for f in res['facts']:
                b = bb_base(f['k'])
                if b == 'DEFAULTTUNE' and not info['tune']:
                    info['tune'] = f['v']
                    info['arch'] = info['arch'] or norm_arch(f['v'])
                elif b == 'SOC_FAMILY' and not info.get('soc_family'):
                    info['soc_family'] = f['v']
                elif b == 'MACHINEOVERRIDES':
                    info['overrides'] = (info['overrides'] + ':' + f['v']).strip(':')
                    vv = [x for x in re.split(r'[:\s]+', f['v']) if x and '$' not in x]
                    if vv and not info.get('ovr_soc'):
                        info['ovr_soc'] = ':'.join(vv[:3])
                elif b.startswith('PREFERRED_PROVIDER') and 'virtual/bootloader' in f['k'] and not info['bootloader']:
                    info['bootloader'] = f['v']
                elif b.startswith('PREFERRED_PROVIDER') and 'virtual/kernel' in f['k'] and not info['kernel']:
                    info['kernel'] = f['v']
                elif b == 'KERNEL_DEVICETREE' and not info['dtb']:
                    info['dtb'] = f['v'][:160]
                elif b == 'TARGET_ARCH' and not info['arch']:
                    info['arch'] = norm_arch(f['v'])
            for ref in res['refs']:
                if ref['t'] in ('require', 'include'):
                    if not info['arch']:
                        a = norm_arch(ref['target']) if 'tune' in ref['target'] or 'arch-' in ref['target'] else None
                        if a:
                            info['arch'] = a
                    p = self.resolve_bb(ref['target'], None, None) or \
                        (lambda c: c if os.path.isfile(c) else None)(os.path.join(os.path.dirname(abspath), ref['target']))
                    if p:
                        walk(p, depth + 1)
        walk(self.abs(repo, mrel), 0)
        info['soc'] = info.pop('soc_family', None) or info.pop('ovr_soc', None)
        info.pop('ovr_soc', None)
        return info

    def android_products(self, r):
        out = []
        base = self.abs(r['path'])
        boards = []
        prods = []
        mks = []
        for dirpath, dirnames, filenames in os.walk(base):
            rel = os.path.relpath(dirpath, base)
            depth = 0 if rel == '.' else rel.count(os.sep) + 1
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            if depth >= 3:
                dirnames[:] = []
            for fn in filenames:
                frel = (rel.replace(os.sep, '/') + '/' if rel != '.' else '') + fn
                if fn.startswith('BoardConfig') and fn.endswith('.mk'):
                    boards.append(frel)
                elif fn == 'AndroidProducts.mk':
                    prods.append(frel)
                if fn.endswith('.mk'):
                    mks.append(frel)
        bfacts = {}
        for b in boards:
            res = parse_make(read_text(os.path.join(base, b)) or '')
            for f in res['facts']:
                if f['k'] in ('TARGET_ARCH', 'TARGET_BOARD_PLATFORM', 'TARGET_CPU_VARIANT', 'TARGET_ARCH_VARIANT',
                              'TARGET_BOOTLOADER_BOARD_NAME') and f['k'] not in bfacts:
                    bfacts[f['k']] = f['v']
        choices = []
        for p in prods:
            res = parse_make(read_text(os.path.join(base, p)) or '')
            for f in res['facts']:
                if f['k'] == 'COMMON_LUNCH_CHOICES':
                    choices += [c for c in f['v'].split() if c and c != '\\']
            if not choices:
                for ref in res['refs']:
                    if ref['t'] == 'product':
                        choices.append(os.path.basename(ref['target'])[:-3])
        pkgs = set()
        for mk in mks[:60]:
            res = parse_make(read_text(os.path.join(base, mk)) or '')
            for f in res['facts']:
                if f['k'] == 'PRODUCT_PACKAGES':
                    pkgs.update(t for t in f['v'].split() if t != '\\' and '$' not in t)
        for c in (choices or [os.path.basename(r['path'])]):
            for pk in pkgs:
                if c not in self.pkg_products[pk]:
                    self.pkg_products[pk].append(c)
            out.append({'name': c, 'repo': r['path'], 'file': (prods or boards or [''])[0],
                        'arch': norm_arch(bfacts.get('TARGET_ARCH')) or bfacts.get('TARGET_ARCH'),
                        'platform': bfacts.get('TARGET_BOARD_PLATFORM') or bfacts.get('TARGET_BOOTLOADER_BOARD_NAME'),
                        'cpu': bfacts.get('TARGET_CPU_VARIANT') or bfacts.get('TARGET_ARCH_VARIANT')})
        return out

    def qnx_images(self, r):
        out = []
        base = self.abs(r['path'])
        for dirpath, dirnames, filenames in os.walk(base):
            rel = os.path.relpath(dirpath, base)
            depth = 0 if rel == '.' else rel.count(os.sep) + 1
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            if depth >= 4:
                dirnames[:] = []
            for fn in filenames:
                if fn.endswith('.build'):
                    txt = read_text(os.path.join(dirpath, fn), 300_000) or ''
                    if '[virtual=' not in txt and '[image=' not in txt:
                        continue
                    res = parse_qnx_build(txt)
                    arch = next((f['v'] for f in res['facts'] if f['k'].startswith('virtual')), None)
                    board = next((f['v'] for f in res['facts'] if f['k'].startswith('startup')), None)
                    out.append({'name': fn, 'repo': r['path'],
                                'file': (rel.replace(os.sep, '/') + '/' if rel != '.' else '') + fn,
                                'arch': norm_arch(arch) or arch, 'raw_arch': arch, 'board': board})
        return out

    # ---- deep analysis of one repo
    def analyze(self, path, force=False):
        with self.lock:
            if not force and path in self.analysis:
                return self.analysis[path]
        r = self.by_path.get(path)
        if r is None:
            raise KeyError(path)
        t0 = time.time()
        base = self.abs(path)
        tags = set(r.get('tags', []))
        files = defaultdict(list)
        total = 0
        truncated = False
        qnx_arch_dirs = set()
        kernel_arch_dirs = []
        fidx = defaultdict(list)
        top_entries = []
        if os.path.isdir(base):
            try:
                top_entries = sorted(e.name + ('/' if e.is_dir() else '') for e in os.scandir(base) if e.name != '.git')
            except OSError:
                pass
            for dirpath, dirnames, filenames in os.walk(base):
                rel = os.path.relpath(dirpath, base)
                rel = '' if rel == '.' else rel.replace(os.sep, '/')
                depth = 0 if not rel else rel.count('/') + 1
                keep = []
                for d in sorted(dirnames):
                    if d in SKIP_DIRS or d.startswith('bazel-'):
                        continue
                    if depth == 0 and d in ('out', 'tmp'):
                        continue
                    sub = (path + '/' if path != '.' else '') + (rel + '/' if rel else '') + d
                    if sub in self.project_paths:
                        continue
                    keep.append(d)
                dirnames[:] = keep if depth < MAX_WALK_DEPTH else []
                bn = os.path.basename(dirpath)
                if bn == 'nto':
                    for d in dirnames:
                        if d in QNX_CPUS:
                            qnx_arch_dirs.add((d, rel + '/' + d))
                if rel == 'arch' and ('kernel' in tags or 'u-boot' in tags):
                    kernel_arch_dirs = list(dirnames)
                for fn in filenames:
                    total += 1
                    frel = (rel + '/' if rel else '') + fn
                    cat = categorize(frel, tags)
                    if cat:
                        files[cat].append(frel)
                    lowfn = fn.lower()
                    if lowfn.endswith(('.dtsi', '.dts', '.h', '.inc', '.cmake', '.mk', '.bbclass')) or fn in ('CMakeLists.txt', 'Makefile', 'meson.build'):
                        if len(fidx[fn]) < 50:
                            fidx[fn].append(frel)
                if total >= MAX_WALK_FILES:
                    truncated = True
                    break
        for k in files:
            files[k].sort(key=lambda p: (p.count('/'), p))
        self.file_index[path] = fidx

        # parse a bounded set
        parsed = {}
        for cat, lst in files.items():
            budget = PARSE_BUDGET.get(cat, 0)
            if cat == 'devicetree' and len(lst) > 120:
                budget = 8
            if cat == 'kconfig-defconfig' and len(lst) > 120:
                budget = 12
            if cat == 'make' and ('kernel' in tags or 'u-boot' in tags):
                lst = [p for p in lst if p.count('/') <= 2]
            for frel in lst[:budget]:
                res = self.parse_one(path, frel, tags)
                if res:
                    parsed[frel] = res

        eco = self.ecosystem(r, files, parsed)
        profile = self.derive_profile(r, tags, files, parsed, qnx_arch_dirs, kernel_arch_dirs, eco)
        hints = self.build_hints(r, tags, files, parsed, profile, qnx_arch_dirs, eco)
        git = self.git_info(base)
        layer_info = [l for l in self.index['layers'] if l['repo'] == path]
        machines = [m for m in self.index['machines'] if m['repo'] == path]
        images = [m for m in self.index['images'] if m['repo'] == path]
        products = [m for m in self.index['products'] if m['repo'] == path]
        qnx_imgs = [m for m in self.index['qnx'] if m['repo'] == path]
        used_by = self.xref.get(path, [])
        result = {
            'repo': {k: r.get(k) for k in ('name', 'path', 'remote', 'revision', 'upstream', 'groups', 'url', 'kind',
                                           'tags', 'links', 'annotations', 'dest_branch')},
            'exists': os.path.isdir(base),
            'git': git,
            'top': top_entries[:200],
            'total_files': total,
            'truncated': truncated,
            'files': {k: {'count': len(v), 'list': v[:LIST_CAP], 'label': CAT_META[k]['label'],
                          'group': CAT_META[k]['group']}
                      for k, v in sorted(files.items(), key=lambda kv: CAT_ORDER.get(kv[0], 99))},
            'parsed_files': len(parsed),
            'profile': profile,
            'hints': hints,
            'yocto': {'layers': layer_info, 'machines': machines, 'images': images, 'used_by': used_by,
                      'recipes': eco['recipes'], 'machines_using': eco['machines'], 'images_using': eco['images']},
            'android': {'products': products, 'products_using': eco['products'], 'modules': eco['modules'][:200]},
            'qnx': {'images': qnx_imgs, 'arch_dirs': sorted({a for a, _ in qnx_arch_dirs})},
            'index_state': self.index['state'],
            'took': round(time.time() - t0, 2),
        }
        with self.lock:
            self.analysis[path] = result
        return result

    def parse_one(self, repo, frel, tags=None):
        key = (repo, frel)
        absf = self.abs(repo, frel)
        try:
            mt = os.path.getmtime(absf)
        except OSError:
            return None
        with self.lock:
            c = self.parse_cache.get(key)
        if c and c[0] == mt:
            return c[1]
        txt = read_text(absf)
        if txt is None:
            return None
        if tags is None:
            r = self.by_path.get(repo) or {}
            tags = set(r.get('tags', []))
        res = parse_file(frel, txt, tags)
        with self.lock:
            self.parse_cache[key] = (mt, res)
        return res

    def ecosystem(self, r, files, parsed):
        path = r['path']
        recipes, seen = [], set()
        for x in self.xref.get(path, []):
            nm = re.sub(r'(_[^_/]*)?\.(bb|bbappend|inc)$', '', os.path.basename(x['file']))
            if (nm, x['file']) in seen:
                continue
            seen.add((nm, x['file']))
            res = self.parse_one(x['repo'], x['file'], {'yocto-layer'}) or {'facts': []}
            fx = {}
            for f in res['facts']:
                b = bb_base(f['k'])
                if b in ('COMPATIBLE_MACHINE', 'KBUILD_DEFCONFIG', 'UBOOT_MACHINE', 'UBOOT_CONFIG', 'KMACHINE', 'S',
                         'EXTRA_OECMAKE', 'EXTRA_OEMAKE', 'EXTRA_OECONF', 'DEPENDS'):
                    if b == 'UBOOT_CONFIG' and '[' in f['k']:
                        fx.setdefault('UBOOT_CONFIG_FLAG', f['v'])
                    else:
                        fx.setdefault(b, f['v'])
            recipes.append({'name': nm, 'repo': x['repo'], 'file': x['file'], 'line': x['line'], 'layer': x['layer'],
                            'conf': x['conf'], 'facts': fx})
        names = {rc['name'] for rc in recipes}
        machines = []
        for m in self.index['machines']:
            why = []
            if m.get('kernel') and m['kernel'] in names:
                why.append('virtual/kernel = ' + m['kernel'])
            if m.get('bootloader') and m['bootloader'] in names:
                why.append('virtual/bootloader = ' + m['bootloader'])
            for rc in recipes:
                cm = rc['facts'].get('COMPATIBLE_MACHINE')
                if cm and '$' not in cm:
                    try:
                        hay = m['name'] + ':' + (m.get('overrides') or '') + ':' + (m.get('soc') or '')
                        if re.search(cm, hay):
                            why.append('COMPATIBLE_MACHINE ' + rc['name'])
                    except re.error:
                        pass
            if why:
                machines.append({**m, 'why': ', '.join(dict.fromkeys(why))})
        images = []
        for nm in sorted(names):
            for im in self.pkg_images.get(nm, []):
                if im not in images:
                    images.append(im)
        modules = []
        for cat in ('android-bp', 'android-mk'):
            for f in files.get(cat, []):
                res = parsed.get(f)
                if not res:
                    continue
                for it in res['items']:
                    if it['name'] and not it['name'].startswith('(') and it['name'] != '?' and it['name'] not in modules:
                        modules.append(it['name'])
        prods = []
        for mod in modules:
            for pn in self.pkg_products.get(mod, []):
                for pr in self.index['products']:
                    if pr['name'] == pn and not any(x['name'] == pn for x in prods):
                        prods.append({**pr, 'via': mod})
        if 'android-device' in r.get('tags', []):
            for pr in self.index['products']:
                if pr['repo'] == path and not any(x['name'] == pr['name'] for x in prods):
                    prods.append({**pr, 'via': 'device'})
        return {'recipes': recipes, 'machines': machines, 'images': images, 'modules': modules, 'products': prods}

    def derive_profile(self, r, tags, files, parsed, qnx_arch_dirs, kernel_arch_dirs, eco):
        osc, arch, chip, bs, machine = Cand(), Cand(), Cand(), Cand(), Cand()
        first = lambda cat: (files.get(cat) or [None])[0]
        if 'yocto-layer' in tags or 'yocto-multi' in tags:
            osc.add('Linux (Yocto / OpenEmbedded)', first('yocto-layer'), None, 'high')
            bs.add('BitBake (Yocto)', first('yocto-layer'), None, 'high')
        if 'bitbake' in tags:
            bs.add('BitBake (công cụ)', None, None, 'high')
        if 'kernel' in tags:
            osc.add('Linux kernel', 'Kconfig', None, 'high')
            bs.add('Kbuild (make)', 'Makefile', None, 'high')
        if 'u-boot' in tags:
            osc.add('U-Boot (bootloader)', 'Kconfig', None, 'high')
            bs.add('Kbuild (make)', 'Makefile', None, 'high')
        if 'android' in tags or 'android-device' in tags or files.get('android-bp') or files.get('android-mk'):
            src = first('android-board') or first('android-product') or first('android-bp') or first('android-mk')
            osc.add('Android', src, None, 'high' if ('android' in tags or 'android-device' in tags) else 'medium')
            if files.get('android-bp'):
                bs.add('Soong (Android.bp)', first('android-bp'), None, 'high')
            if files.get('android-mk'):
                bs.add('Android.mk (Make)', first('android-mk'), None, 'high')
            if 'android-device' in tags:
                bs.add('Android product (lunch)', first('android-product') or first('android-board'), None, 'high')
        if 'qnx' in tags:
            osc.add('QNX Neutrino', first('qnx-make') or first('qnx-buildfile'), None, 'high')
            bs.add('QNX recursive make (qcc)', first('qnx-make'), None, 'high')
        for d, rel in sorted(qnx_arch_dirs):
            arch.add(norm_arch(d) or d, rel.lstrip('/') + '/', None, 'high', 'thư mục nto/' + d, isdir=True)
        if len(kernel_arch_dirs) and len(kernel_arch_dirs) <= 3:
            for d in kernel_arch_dirs:
                arch.add(norm_arch(d) or d, 'arch/' + d + '/', None, 'low', 'thư mục arch/', isdir=True)
        elif len(kernel_arch_dirs) > 3:
            arch.add('đa kiến trúc (%d trong arch/)' % len(kernel_arch_dirs), 'arch/', None, 'low', 'tùy defconfig', isdir=True)
        for cat, label in (('cmake', 'CMake'), ('meson', 'Meson'), ('autotools', 'Autotools'), ('bazel', 'Bazel')):
            if files.get(cat):
                bs.add(label, first(cat), None, 'high' if cat in tags else 'medium')
        if files.get('make') and not bs.d:
            bs.add('Make', first('make'), None, 'high' if 'make' in tags else 'medium')
        for t, label in (('cargo', 'Cargo (Rust)'), ('go', 'Go modules'), ('gradle', 'Gradle/Maven'),
                         ('node', 'npm/Node.js'), ('python', 'Python packaging')):
            if t in tags:
                bs.add(label, None, None, 'high')

        dts_count = len(files.get('devicetree', []))
        for frel, res in parsed.items():
            k = res['kind']
            for f in res['facts']:
                key, v, ln = f['k'], f['v'], f.get('line')
                b = bb_base(key) if k == 'bitbake' else key
                if k == 'make':
                    if key in ('ARCH', 'TARGET_ARCH', 'SUBARCH'):
                        conf = 'high' if key == 'TARGET_ARCH' else 'medium'
                        arch.add(norm_arch(v), frel, ln, conf, key)
                    elif key == 'TARGET_2ND_ARCH':
                        arch.add(norm_arch(v), frel, ln, 'low', '2nd arch (32-bit userspace)')
                    elif key == 'CROSS_COMPILE':
                        arch.add(norm_arch(v), frel, ln, 'medium', 'CROSS_COMPILE')
                        osc.add(os_from_triple(v), frel, ln, 'medium', 'CROSS_COMPILE')
                    elif key in ('CC', 'CXX'):
                        if 'qcc' in v.lower() or 'q++' in v.lower():
                            osc.add('QNX Neutrino', frel, ln, 'high', key)
                        a = norm_arch(v) if re.search(r'aarch64|arm|x86|riscv|nto', v.lower()) else None
                        arch.add(a, frel, ln, 'medium', key)
                    elif key in ('TARGET_BOARD_PLATFORM', 'TARGET_BOOTLOADER_BOARD_NAME', 'SOC', 'CHIP', 'PLATFORM', 'BOARD'):
                        chip.add(v.split()[0] if v else v, frel, ln, 'high' if key == 'TARGET_BOARD_PLATFORM' else 'medium', key)
                    elif key in ('TARGET_CPU_VARIANT', 'TARGET_ARCH_VARIANT'):
                        chip.add('CPU: ' + v, frel, ln, 'medium', key)
                    elif key in ('CPULIST', 'CPU') and 'qnx' in tags:
                        for x in v.split():
                            arch.add(norm_arch(x) or x, frel, ln, 'medium', key)
                    elif key == 'qcc -V':
                        osc.add('QNX Neutrino', frel, ln, 'high', 'qcc -V')
                        arch.add(norm_arch(v), frel, ln, 'high', 'qcc -V ' + v)
                    elif key == 'QNX build':
                        osc.add('QNX Neutrino', frel, ln, 'high', v)
                elif k == 'cmake':
                    if key == 'CMAKE_SYSTEM_NAME':
                        vv = v.split()[0] if v else ''
                        osm = {'qnx': 'QNX Neutrino', 'linux': 'Linux', 'android': 'Android', 'generic': 'Bare-metal',
                               'windows': 'Windows', 'darwin': 'macOS'}.get(vv.lower(), vv)
                        osc.add(osm, frel, ln, 'high', key)
                    elif key in ('CMAKE_SYSTEM_PROCESSOR', 'QNX_PROCESSOR', 'CMAKE_ANDROID_ARCH_ABI', 'ANDROID_ABI'):
                        arch.add(norm_arch(v) or v.split()[0] if v else None, frel, ln, 'high', key)
                    elif key in ('CMAKE_C_COMPILER', 'CMAKE_CXX_COMPILER', 'CMAKE_C_COMPILER_TARGET', 'TOOLCHAIN_PREFIX', 'CROSS_COMPILE'):
                        a = norm_arch(v) if re.search(r'aarch64|arm|x86|riscv|nto', v.lower()) else None
                        arch.add(a, frel, ln, 'medium', key)
                        if 'qcc' in v.lower() or 'nto' in v.lower():
                            osc.add('QNX Neutrino', frel, ln, 'high', key)
                        o = os_from_triple(v)
                        if o and o != 'QNX Neutrino':
                            osc.add(o, frel, ln, 'medium', key)
                elif k == 'cmake-presets':
                    if key.startswith('CMAKE_SYSTEM_NAME'):
                        osc.add(v, frel, ln, 'medium', key)
                    if key.startswith('CMAKE_SYSTEM_PROCESSOR'):
                        arch.add(norm_arch(v) or v, frel, ln, 'medium', key)
                elif k == 'bitbake':
                    if b == 'DEFAULTTUNE':
                        arch.add(norm_arch(v), frel, ln, 'high', 'DEFAULTTUNE=' + v)
                        chip.add('tune: ' + v, frel, ln, 'medium', 'DEFAULTTUNE')
                    elif b == 'SOC_FAMILY':
                        chip.add(v, frel, ln, 'high', 'SOC_FAMILY')
                    elif b == 'MACHINEOVERRIDES':
                        vv = [x for x in re.split(r'[:\s]+', v) if x and '$' not in x]
                        if vv:
                            chip.add(':'.join(vv[:3]), frel, ln, 'medium', 'MACHINEOVERRIDES')
                    elif b == 'COMPATIBLE_MACHINE':
                        pass
                    elif b == 'KERNEL_DEVICETREE':
                        for dtb in v.split()[:3]:
                            chip.add('dtb: ' + os.path.basename(dtb), frel, ln, 'low', 'KERNEL_DEVICETREE')
                    elif b == 'TARGET_ARCH':
                        arch.add(norm_arch(v), frel, ln, 'high', 'TARGET_ARCH')
                    elif b == 'MACHINE':
                        machine.add(v, frel, ln, 'medium', 'MACHINE')
                    elif b in ('EXTRA_OEMAKE', 'EXTRA_OECMAKE'):
                        m2 = re.search(r'ARCH=(\S+)', v)
                        if m2:
                            arch.add(norm_arch(m2.group(1)), frel, ln, 'low', b)
                elif k == 'devicetree':
                    if dts_count > 40:
                        continue
                    if key == 'compatible':
                        socs = [x for x in v.split(', ')]
                        soc = socs[-1] if socs else None
                        chip.add(soc, frel, ln, 'high' if dts_count <= 20 else 'medium', 'root compatible')
                    elif key == 'model':
                        chip.add('board: ' + v, frel, ln, 'low', 'model')
                    elif key == 'cpu core':
                        core = v.split(' x')[0]
                        chip.add('CPU: ' + core.replace('arm,', '') + (' x' + v.split(' x')[1] if ' x' in v else ''), frel, ln, 'medium', 'cpus')
                        arch.add(norm_arch(core.replace('arm,', '')), frel, ln, 'low', 'cpu core')
                elif k == 'defconfig':
                    dconf = 'medium' if ('kernel' in tags or 'u-boot' in tags) else 'high'
                    if key in ('CONFIG_ARM64',) and v == 'y':
                        arch.add('aarch64', frel, ln, dconf, key)
                    elif key == 'CONFIG_X86_64' and v == 'y':
                        arch.add('x86_64', frel, ln, dconf, key)
                    elif key == 'CONFIG_RISCV' and v == 'y':
                        arch.add('riscv64', frel, ln, 'medium', key)
                    elif key.startswith(('CONFIG_ARCH_', 'CONFIG_SOC_', 'CONFIG_MACH_')) and v == 'y':
                        nm = key.split('_', 2)[2] if key.count('_') >= 2 else key
                        if nm not in ('HAS_TICK_BROADCAST', 'SUSPEND_POSSIBLE', 'MULTIPLATFORM', 'FORCE_MAX_ORDER', 'MMAP_RND_BITS'):
                            chip.add(nm.lower(), frel, ln, 'low', key)
                    elif key in ('CONFIG_SYS_SOC', 'CONFIG_SYS_BOARD', 'CONFIG_DEFAULT_DEVICE_TREE', 'CONFIG_SYS_CONFIG_NAME'):
                        chip.add(unquote(v), frel, ln, 'medium', key)
                    if '/configs/' in '/' + frel:
                        m2 = re.search(r'(?:^|/)arch/([^/]+)/configs/', frel)
                        if m2:
                            arch.add(norm_arch(m2.group(1)) or m2.group(1), frel, None, 'low', 'arch/' + m2.group(1) + '/configs')
                elif k == 'qnx-buildfile':
                    if key.startswith('virtual'):
                        arch.add(norm_arch(v) or v, frel, ln, 'high', 'virtual=' + v)
                        osc.add('QNX Neutrino', frel, ln, 'high', 'buildfile')
                    elif key.startswith('startup'):
                        chip.add('board: ' + v.replace('startup-', ''), frel, ln, 'medium', v)
                elif k == 'meson':
                    if key.endswith('.cpu_family') or key.endswith('.cpu'):
                        arch.add(norm_arch(v) or v, frel, ln, 'high', key)
                    elif key.endswith('.system'):
                        osc.add(v, frel, ln, 'high', key)
                elif k == 'android-bp' and key == 'arch-specific':
                    for a in v.split(','):
                        arch.add(norm_arch(a), frel, ln, 'low', 'Android.bp arch block')
        if dts_count > 40:
            vend = Counter(f.split('/')[-2] if '/' in f else '.' for f in files.get('devicetree', []))
            for vd, cnt in vend.most_common(6):
                chip.add('dts/%s ×%d' % (vd, cnt), None, None, 'low')
        for mfile in files.get('yocto-machine', [])[:60]:
            machine.add(os.path.basename(mfile)[:-5], mfile, None, 'high', 'conf/machine')
        for m in self.index['machines']:
            if m['repo'] == r['path']:
                if m.get('arch'):
                    arch.add(m['arch'], m['file'], None, 'high', 'MACHINE ' + m['name'] + (' / ' + m['tune'] if m.get('tune') else ''))
                if m.get('soc'):
                    chip.add(m['soc'], m['file'], None, 'high', 'MACHINE ' + m['name'])
        # ecosystem: recipe -> MACHINE / image, android module -> product
        for x in eco['recipes'][:10]:
            osc.add('Linux (Yocto / OpenEmbedded)', x['file'], x['line'], 'medium', 'recipe ' + x['name'] + ' lấy repo này', xrepo=x['repo'])
        for m in eco['machines'][:12]:
            why = 'MACHINE ' + m['name'] + ' (' + m['why'] + ')'
            if m.get('arch'):
                arch.add(m['arch'], m['file'], None, 'high', why, xrepo=m['repo'])
            if m.get('soc'):
                chip.add(m['soc'], m['file'], None, 'high', why, xrepo=m['repo'])
            machine.add(m['name'], m['file'], None, 'high', m['why'], xrepo=m['repo'])
        for pr in eco['products'][:8]:
            why = 'product ' + pr['name']
            osc.add('Android', pr['file'], None, 'high', why, xrepo=pr['repo'])
            if pr.get('arch'):
                arch.add(pr['arch'], pr['file'], None, 'high', why, xrepo=pr['repo'])
            if pr.get('platform'):
                chip.add(pr['platform'], pr['file'], None, 'high', why, xrepo=pr['repo'])
        return {'os': osc.out(), 'arch': arch.out(), 'chip': chip.out(14), 'build': bs.out(), 'machine': machine.out(40)}

    def build_hints(self, r, tags, files, parsed, profile, qnx_arch_dirs, eco):
        H = []
        path = r['path']
        first = lambda cat: (files.get(cat) or [None])[0]

        def facts_of(cat, key):
            out = []
            for f in files.get(cat, []):
                res = parsed.get(f)
                if res:
                    for x in res['facts']:
                        if x['k'] == key:
                            out.append(x['v'])
            return out

        if eco['recipes']:
            recs = []
            for x in eco['recipes']:
                if x['name'] not in recs:
                    recs.append(x['name'])
            mach = eco['machines'][0]['name'] if eco['machines'] else '<machine>'
            cmds = ['source <poky>/oe-init-build-env build', 'MACHINE=%s bitbake %s' % (mach, recs[0])]
            cmds += ['bitbake %s' % nm for nm in recs[1:4]]
            if eco['images']:
                cmds.append('MACHINE=%s bitbake %s   # image có chứa %s' % (mach, eco['images'][0], recs[0]))
            cmds.append('devtool modify %s   # sửa code repo này ngay trong build Yocto' % recs[0])
            note = 'Recipe: ' + ', '.join(x['layer'] + ':' + os.path.basename(x['file']) for x in eco['recipes'][:5])
            if eco['machines']:
                note += '  |  MACHINE: ' + ', '.join(m['name'] for m in eco['machines'][:6])
            if eco['images']:
                note += '  |  image: ' + ', '.join(eco['images'][:6])
            H.append({'title': 'Được build bởi Yocto recipe', 'conf': 'high' if any(x['conf'] == 'high' for x in eco['recipes']) else 'medium',
                      'cmds': cmds, 'note': note})
        if 'yocto-layer' in tags or 'yocto-multi' in tags:
            machines = [m['v'] for m in profile['machine']][:6]
            imgs = [os.path.basename(f)[:-3] for f in files.get('yocto-image', [])][:6]
            H.append({'title': 'Yocto layer', 'conf': 'high',
                      'cmds': ['source <poky>/oe-init-build-env build',
                               'bitbake-layers add-layer %s' % ('$WS/' + path),
                               'MACHINE=%s bitbake %s' % (machines[0] if machines else '<machine>', imgs[0] if imgs else 'core-image-minimal')],
                      'note': ('MACHINE: ' + ', '.join(machines) if machines else 'Không có conf/machine trong repo này') +
                              ('  |  image: ' + ', '.join(imgs) if imgs else '')})
        if 'android-device' in tags or 'android' in tags or files.get('android-bp'):
            choices = []
            for v in facts_of('android-product', 'COMMON_LUNCH_CHOICES'):
                choices += [c for c in v.split() if c != '\\']
            for pr in eco['products']:
                if pr['name'] not in choices:
                    choices.append(pr['name'])
            mods = []
            for cat in ('android-bp', 'android-mk'):
                for f in files.get(cat, [])[:10]:
                    res = parsed.get(f)
                    if res:
                        for it in res['items']:
                            if it['t'] in ('module',) or (cat == 'android-bp' and not it['name'].startswith('(')):
                                if it['name'] not in mods and it['name'] != '?':
                                    mods.append(it['name'])
            cmds = ['cd $WS && source build/envsetup.sh',
                    'lunch %s' % (choices[0] if choices else '<product>-<release>-userdebug')]
            if mods:
                cmds.append('m %s' % ' '.join(mods[:3]))
                cmds.append('mm   # chỉ build module trong thư mục hiện tại (cd %s)' % path)
            else:
                cmds.append('m')
            H.append({'title': 'Android (Soong/Make)', 'conf': 'high' if choices or mods else 'medium', 'cmds': cmds,
                      'note': ('lunch: ' + ', '.join(choices[:6]) + '  |  ' if choices else '') +
                              ('module: ' + ', '.join(mods[:8]) if mods else '')})
        if 'qnx' in tags:
            archs = sorted({a for a, _ in qnx_arch_dirs})
            H.append({'title': 'QNX (recursive make)', 'conf': 'high',
                      'cmds': ['source <qnx_sdp>/qnxsdp-env.sh', 'cd %s' % path,
                               'make' + (' CPULIST=%s' % archs[0] if archs else ''), 'make install   # cài theo INSTALLDIR'],
                      'note': ('biến thể CPU: ' + ', '.join(archs)) if archs else 'qcc chọn kiến trúc qua -V (vd gcc_ntoaarch64le)'})
        if 'kernel' in tags or 'u-boot' in tags:
            ar = next((a['v'] for a in profile['arch'] if a['conf'] == 'high'), None) or \
                next((a['v'] for a in profile['arch'] if a['conf'] != 'low'), None)
            karch = {'aarch64': 'arm64', 'arm (32-bit)': 'arm', 'x86_64': 'x86', 'x86': 'x86', 'riscv64': 'riscv'}.get(ar or '', '<arch>')
            dl = files.get('kconfig-defconfig', [])
            dl = sorted(dl, key=lambda f: (0 if '/arch/%s/' % karch in '/' + f or f.startswith('configs/') else 1,
                                           1 if os.path.basename(f) in ('defconfig', 'multi_v7_defconfig') else 0, f))
            bns = [os.path.basename(f) for f in dl]
            defs = [b if bns.count(b) == 1 else f for b, f in zip(bns, dl)]
            for rc in eco['recipes']:
                fx = rc['facts']
                want = fx.get('KBUILD_DEFCONFIG') or fx.get('UBOOT_MACHINE') or (fx.get('UBOOT_CONFIG_FLAG') or '').split(',')[0]
                if want and '$' not in want:
                    defs = [want] + [d for d in defs if d != want]
                    break
            cc = {'arm64': 'aarch64-linux-gnu-', 'arm': 'arm-linux-gnueabihf-', 'riscv': 'riscv64-linux-gnu-'}.get(karch, '<prefix>-')
            H.append({'title': 'Kbuild', 'conf': 'medium',
                      'cmds': ['make ARCH=%s CROSS_COMPILE=%s %s' % (karch, cc, defs[0] if defs else 'defconfig'),
                               'make ARCH=%s CROSS_COMPILE=%s -j$(nproc)' % (karch, cc)],
                      'note': ('%d defconfig, vd: %s' % (len(defs), ', '.join(defs[:6]))) if defs else ''})
        if files.get('cmake') or files.get('cmake-toolchain'):
            tcs = files.get('cmake-toolchain', [])
            pre = [x for x in files.get('cmake', []) if x.endswith('Presets.json')]
            cmd = 'cmake -S . -B build'
            if tcs:
                cmd += ' -DCMAKE_TOOLCHAIN_FILE=%s' % tcs[0]
            H.append({'title': 'CMake', 'conf': 'high' if 'cmake' in tags else 'medium',
                      'cmds': ([('cmake --preset <tên>   # có %s' % pre[0])] if pre else []) + [cmd, 'cmake --build build -j'],
                      'note': ('toolchain: ' + ', '.join(tcs[:5])) if tcs else ''})
        if files.get('meson'):
            cross = [f for f in files['meson'] if f.endswith('.ini') or f.endswith('.txt') and 'cross' in f]
            H.append({'title': 'Meson', 'conf': 'high' if 'meson' in tags else 'medium',
                      'cmds': ['meson setup build' + (' --cross-file %s' % cross[0] if cross else ''), 'ninja -C build'], 'note': ''})
        if files.get('autotools'):
            H.append({'title': 'Autotools', 'conf': 'medium',
                      'cmds': ['autoreconf -fi   # nếu chưa có ./configure', './configure --host=<triple>', 'make -j'], 'note': ''})
        if files.get('make') and not H:
            tg = []
            f0 = first('make')
            res = parsed.get(f0) if f0 else None
            if res:
                tg = [i['name'] for i in res['items'] if i['t'] == 'target'][:8]
            H.append({'title': 'Make', 'conf': 'medium', 'cmds': ['make' + (' ' + tg[0] if tg else '')],
                      'note': ('target: ' + ', '.join(tg)) if tg else ''})
        for t, cmd in (('cargo', 'cargo build'), ('go', 'go build ./...'), ('node', 'npm install && npm run build'),
                       ('python', 'pip install -e .'), ('gradle', './gradlew build')):
            if t in tags:
                H.append({'title': t, 'conf': 'high', 'cmds': [cmd], 'note': ''})
        return H

    def git_info(self, base):
        if not os.path.isdir(base):
            return None
        try:
            out = subprocess.run(['git', '-C', base, 'log', '-1', '--format=%H%x1f%an%x1f%ad%x1f%s', '--date=short'],
                                 capture_output=True, text=True, timeout=4)
            if out.returncode == 0 and out.stdout.strip():
                h, a, d, s = (out.stdout.strip().split('\x1f') + ['', '', '', ''])[:4]
                br = subprocess.run(['git', '-C', base, 'rev-parse', '--abbrev-ref', 'HEAD'],
                                    capture_output=True, text=True, timeout=3).stdout.strip()
                return {'head': h, 'author': a, 'date': d, 'subject': s, 'branch': br}
        except Exception:
            pass
        return None

    # ---- single file view + ref resolution
    def file_view(self, repo, frel):
        absf = self.abs(repo, frel)
        if not self.safe(absf) or not os.path.isfile(absf):
            raise FileNotFoundError(frel)
        size = os.path.getsize(absf)
        text = read_text(absf)
        res = self.parse_one(repo, frel) if text is not None else None
        refs = []
        if res:
            for ref in res['refs'][:200]:
                tgt = self.resolve_ref(repo, frel, res['kind'], ref)
                refs.append({**ref, 'resolved': tgt})
        return {'repo': repo, 'file': frel, 'size': size, 'binary': text is None,
                'truncated': text is not None and size > len(text.encode('utf-8', 'replace')) + 16,
                'text': text if text is not None else '', 'parsed': res, 'refs': refs}

    def to_pair(self, absp):
        if not absp or not os.path.isfile(absp) or not self.safe(absp):
            return None
        repo, rel = self.owner_of(absp)
        if repo is None:
            return None
        return {'repo': repo, 'file': rel}

    def resolve_ref(self, repo, frel, kind, ref):
        tgt = (ref.get('target') or '').strip()
        if not tgt:
            return None
        d = os.path.dirname(frel)
        if kind == 'bitbake':
            if ref['t'] == 'inherit':
                c = self.bbclasses.get(tgt)
                return {'repo': c[0], 'file': c[1]} if c else None
            return self.to_pair(self.resolve_bb(tgt, repo, frel))
        if kind == 'make':
            t = tgt.replace('$(LOCAL_DIR)', '/'.join(x for x in (repo if repo != '.' else '', d) if x))
            t = t.replace('$(SRC_TARGET_DIR)', 'build/make/target').replace('$(LOCAL_PATH)', '/'.join(x for x in (repo if repo != '.' else '', d) if x))
            if '$' in t:
                return None
            for c in (self.abs(repo, os.path.join(d, t)), self.abs(repo, t), os.path.join(self.root, t)):
                p = self.to_pair(c)
                if p:
                    return p
            return None
        if kind in ('cmake', 'cmake-presets'):
            t = tgt.replace('${CMAKE_CURRENT_SOURCE_DIR}', '').replace('${CMAKE_SOURCE_DIR}', '').replace('${CMAKE_CURRENT_LIST_DIR}', '').replace('${PROJECT_SOURCE_DIR}', '').replace('${sourceDir}', '').lstrip('/')
            if '$' in t:
                return None
            cands = []
            if ref['t'] == 'subdir':
                cands = [os.path.join(d, t, 'CMakeLists.txt')]
            else:
                cands = [os.path.join(d, t), os.path.join(d, t + '.cmake'), os.path.join(d, 'cmake', t + '.cmake'),
                         os.path.join('cmake', t + '.cmake'), t]
            for c in cands:
                p = self.to_pair(self.abs(repo, c))
                if p:
                    return p
            return None
        if kind == 'meson':
            return self.to_pair(self.abs(repo, os.path.join(d, tgt, 'meson.build')))
        if kind == 'devicetree':
            for c in (os.path.join(d, tgt), os.path.join('include', tgt)):
                p = self.to_pair(self.abs(repo, c))
                if p:
                    return p
            b = os.path.basename(tgt)
            hits = (self.file_index.get(repo) or {}).get(b) or []
            if hits:
                best = sorted(hits, key=lambda h: (0 if os.path.dirname(h).split('/')[:3] == d.split('/')[:3] else 1, len(h)))[0]
                return {'repo': repo, 'file': best}
            return None
        if kind == 'kconfig':
            for c in (os.path.join(d, tgt), tgt):
                p = self.to_pair(self.abs(repo, c))
                if p:
                    return p
        return None


# ---------------------------------------------------------------- http

class Handler(BaseHTTPRequestHandler):
    ws = None
    server_version = 'modmap/' + VERSION

    def log_message(self, *a):
        pass

    def send_bytes(self, data, ctype, code=200):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, obj, code=200):
        self.send_bytes(json.dumps(obj, ensure_ascii=False).encode('utf-8'), 'application/json; charset=utf-8', code)

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        ws = self.ws
        try:
            if u.path in ('/', '/index.html'):
                self.send_bytes(HTML.encode('utf-8'), 'text/html; charset=utf-8')
            elif u.path == '/api/workspace':
                self.send_json({
                    'root': ws.root, 'mode': ws.mode, 'manifest': ws.manifest, 'version': VERSION,
                    'scan_took': round(ws.scan_took, 2),
                    'repos': [{k: r.get(k) for k in ('name', 'path', 'kind', 'tags', 'revision', 'groups', 'remote')}
                              for r in ws.repos],
                })
            elif u.path == '/api/index':
                self.send_json(ws.index)
            elif u.path == '/api/repo':
                path = q.get('path', '')
                if path not in ws.by_path:
                    return self.send_json({'error': 'không có repo: ' + path}, 404)
                self.send_json(ws.analyze(path, force=q.get('force') == '1'))
            elif u.path == '/api/file':
                repo, f = q.get('repo', ''), q.get('file', '')
                if repo not in ws.by_path:
                    return self.send_json({'error': 'không có repo: ' + repo}, 404)
                self.send_json(ws.file_view(repo, f))
            else:
                self.send_json({'error': 'not found'}, 404)
        except FileNotFoundError as e:
            self.send_json({'error': 'không đọc được file: %s' % e}, 404)
        except Exception as e:
            traceback.print_exc()
            self.send_json({'error': '%s: %s' % (type(e).__name__, e)}, 500)


def main():
    ap = argparse.ArgumentParser(description='modmap - ban do workspace repo sync (Yocto/Android/QNX/Linux)')
    ap.add_argument('root', help='thu muc goc workspace (noi co .repo/)')
    ap.add_argument('--port', type=int, default=8765)
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--no-browser', action='store_true')
    ap.add_argument('--workers', type=int, default=min(32, (os.cpu_count() or 4) * 2))
    ap.add_argument('--dump', metavar='REPO_PATH', help='in JSON phan tich 1 repo roi thoat')
    a = ap.parse_args()
    if not os.path.isdir(a.root):
        sys.exit('khong thay thu muc: ' + a.root)
    log('modmap %s  -  %s' % (VERSION, os.path.abspath(a.root)))
    ws = Workspace(a.root, a.workers)
    ws.load()
    if a.dump:
        ws.build_index()
        json.dump(ws.analyze(a.dump.strip('/')), sys.stdout, ensure_ascii=False, indent=2)
        print()
        return
    threading.Thread(target=ws.build_index, daemon=True).start()
    Handler.ws = ws
    srv = None
    for port in range(a.port, a.port + 30):
        try:
            srv = ThreadingHTTPServer((a.host, port), Handler)
            break
        except OSError:
            continue
    if srv is None:
        sys.exit('khong mo duoc cong %d..%d' % (a.port, a.port + 29))
    url = 'http://%s:%d/' % ('127.0.0.1' if a.host in ('0.0.0.0', '') else a.host, srv.server_address[1])
    log('  giao dien: %s   (Ctrl+C de thoat)' % url)
    if not a.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print('\nbye')


HTML = r'''<!doctype html>
<html lang="vi">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>modmap</title>
<style>
:root{
  --bg:#0b0f15; --bg2:#0f141c; --panel:#121924; --panel2:#17202d; --line:#223044; --line2:#2c3c53;
  --text:#d9e2ec; --mute:#8b9bb0; --dim:#5d6d82; --acc:#7cc4ff; --acc2:#3b82f6; --sel:#1b2a3e; --hover:#151f2c;
  --shadow:0 10px 30px rgba(0,0,0,.35);
  --c-yocto:#f5a524; --c-android:#3ddc84; --c-qnx:#a78bfa; --c-kernel:#ff7a7a; --c-uboot:#f472b6;
  --c-cmake:#2dd4bf; --c-make:#9fb0c4; --c-meson:#60a5fa; --c-bazel:#86efac; --c-autotools:#fb923c;
  --c-dt:#e879f9; --c-lang:#38bdf8; --c-other:#64748b; --c-missing:#3a4555;
  --mono: ui-monospace,"JetBrains Mono","Cascadia Code","SF Mono",Menlo,Consolas,monospace;
  --sans: Inter,"Segoe UI",system-ui,-apple-system,Roboto,"Noto Sans",sans-serif;
  color-scheme: dark;
}
:root[data-theme=light]{
  --bg:#f4f6f9; --bg2:#eef1f5; --panel:#ffffff; --panel2:#f6f8fb; --line:#e1e7ef; --line2:#cfd8e4;
  --text:#1b2533; --mute:#5c6b7f; --dim:#94a2b5; --acc:#1d6fd6; --acc2:#1d6fd6; --sel:#e3eefc; --hover:#eef3f9;
  --shadow:0 10px 30px rgba(20,40,70,.12);
  --c-yocto:#c97a00; --c-android:#139a4f; --c-qnx:#7c5ce0; --c-kernel:#d64545; --c-uboot:#d63d8d;
  --c-cmake:#0f9d8c; --c-make:#5f6f84; --c-meson:#2f6fd8; --c-bazel:#2f9e55; --c-autotools:#d8681b;
  --c-dt:#b33fc9; --c-lang:#0f86c1; --c-other:#7b8798; --c-missing:#b9c3d0;
  color-scheme: light;
}
*{box-sizing:border-box}
html,body{margin:0;height:100%;background:var(--bg);color:var(--text);font:13px/1.45 var(--sans)}
button{font:inherit;color:inherit}
a{color:var(--acc);text-decoration:none}
.mono{font-family:var(--mono)}
::-webkit-scrollbar{width:10px;height:10px}::-webkit-scrollbar-thumb{background:var(--line2);border-radius:8px;border:2px solid var(--bg)}::-webkit-scrollbar-track{background:transparent}

/* top bar */
.top{height:52px;display:flex;align-items:center;gap:14px;padding:0 14px;border-bottom:1px solid var(--line);background:var(--bg2);position:relative;z-index:5}
.brand{display:flex;align-items:center;gap:8px;font-weight:700;letter-spacing:.2px;font-size:15px;cursor:pointer;white-space:nowrap}
.brand svg{width:22px;height:22px}
.rootp{font-family:var(--mono);font-size:12px;color:var(--mute);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:32vw}
.search{flex:1;display:flex;justify-content:center}
.search input{width:min(520px,100%);height:32px;border-radius:8px;border:1px solid var(--line2);background:var(--panel);color:var(--text);padding:0 12px 0 32px;font:13px var(--mono);outline:none;
  background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='14' height='14' fill='none' stroke='%238b9bb0' stroke-width='2'%3E%3Ccircle cx='6' cy='6' r='4.5'/%3E%3Cpath d='M9.5 9.5L13 13'/%3E%3C/svg%3E");background-repeat:no-repeat;background-position:11px 9px}
.search input:focus{border-color:var(--acc)}
.idx{font-size:12px;color:var(--mute);white-space:nowrap;display:flex;align-items:center;gap:6px}
.pulse{width:8px;height:8px;border-radius:50%;background:var(--c-yocto);animation:pl 1s infinite alternate}
.ok{width:8px;height:8px;border-radius:50%;background:var(--c-android)}
@keyframes pl{to{opacity:.25}}
.btn{border:1px solid var(--line2);background:var(--panel);border-radius:7px;height:28px;padding:0 10px;cursor:pointer;display:inline-flex;align-items:center;gap:6px;font-size:12px;color:var(--text)}
.btn:hover{border-color:var(--acc)}
.btn.ghost{border-color:transparent;background:transparent;color:var(--mute)}
.btn.ghost:hover{color:var(--text);background:var(--hover)}

/* layout */
.body{display:grid;grid-template-columns:340px 1fr;height:calc(100% - 52px)}
.side{border-right:1px solid var(--line);display:flex;flex-direction:column;min-height:0;background:var(--bg2)}
.chips{display:flex;flex-wrap:wrap;gap:5px;padding:10px 10px 6px}
.chip{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--line);border-radius:999px;padding:2px 9px 2px 7px;font-size:11.5px;cursor:pointer;background:var(--panel);color:var(--mute);user-select:none}
.chip:hover{border-color:var(--line2);color:var(--text)}
.chip.on{border-color:var(--k);color:var(--text);background:color-mix(in srgb,var(--k) 14%,var(--panel))}
.chip b{font-weight:600;color:var(--text)}
.dot{width:8px;height:8px;border-radius:2px;background:var(--k);flex:none;display:inline-block}
.treehead{display:flex;align-items:center;justify-content:space-between;padding:4px 10px 6px;color:var(--dim);font-size:11.5px;border-bottom:1px solid var(--line)}
.tree{flex:1;overflow:auto;position:relative;min-height:0}
.spacer{width:1px}
#rows{position:absolute;top:0;left:0;right:0}
.row{position:absolute;left:0;right:0;height:26px;display:flex;align-items:center;gap:6px;padding-right:10px;cursor:pointer;white-space:nowrap;color:var(--text)}
.row:hover{background:var(--hover)}
.row.sel{background:var(--sel);box-shadow:inset 2px 0 0 var(--acc)}
.row .car{width:14px;height:14px;flex:none;display:grid;place-items:center;color:var(--dim);transition:transform .15s}
.row .car.open{transform:rotate(90deg)}
.row .nm{overflow:hidden;text-overflow:ellipsis;font-family:var(--mono);font-size:12.5px}
.row.folder .nm{color:var(--mute)}
.row .nm mark{background:color-mix(in srgb,var(--acc) 30%,transparent);color:inherit;border-radius:2px}
.row .cnt{margin-left:auto;font-size:11px;color:var(--dim);display:flex;align-items:center;gap:6px}
.mini{display:flex;width:44px;height:5px;border-radius:3px;overflow:hidden;background:var(--line)}
.mini i{display:block;height:100%;background:var(--k)}
.row .kt{margin-left:auto;font-size:10.5px;color:var(--dim)}
.fico{width:14px;height:14px;flex:none;color:var(--dim)}

/* main */
main{overflow:auto;min-width:0;position:relative}
.wrap{padding:22px 26px 60px;max-width:1500px}
h1{font-size:20px;margin:0;font-weight:650;letter-spacing:-.2px}
h2{font-size:12px;letter-spacing:.9px;text-transform:uppercase;color:var(--mute);margin:28px 0 10px;font-weight:600;display:flex;align-items:center;gap:8px}
h2 .n{color:var(--dim);font-weight:500;letter-spacing:0;text-transform:none}
.crumbs{font-family:var(--mono);font-size:12px;color:var(--dim);margin-bottom:6px}
.crumbs a{color:var(--mute);cursor:pointer}
.crumbs a:hover{color:var(--acc)}
.titlerow{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.badge{display:inline-flex;align-items:center;gap:6px;border-radius:6px;padding:2px 8px;font-size:11.5px;font-weight:600;color:var(--k);background:color-mix(in srgb,var(--k) 14%,transparent);border:1px solid color-mix(in srgb,var(--k) 35%,transparent)}
.tagl{font-size:11px;color:var(--dim);border:1px solid var(--line);border-radius:5px;padding:1px 6px}
.meta{display:flex;flex-wrap:wrap;gap:6px 18px;margin-top:10px;color:var(--mute);font-size:12px}
.meta span b{color:var(--dim);font-weight:500;margin-right:5px}
.meta .mono{color:var(--text)}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:12px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:12px 14px;min-width:0}
.card .lb{font-size:11px;letter-spacing:.8px;text-transform:uppercase;color:var(--dim);display:flex;align-items:center;gap:7px;margin-bottom:8px}
.card .lb svg{width:15px;height:15px;color:var(--mute)}
.val{display:flex;align-items:baseline;gap:8px;padding:3px 0;flex-wrap:wrap}
.val .v{font-weight:600;font-size:14px;word-break:break-word}
.val.second .v{font-weight:500;font-size:12.5px;color:var(--mute)}
.conf{width:7px;height:7px;border-radius:50%;flex:none;transform:translateY(-1px)}
.conf.high{background:var(--c-android)}.conf.medium{background:var(--c-yocto)}.conf.low{background:var(--dim)}
.src{font-family:var(--mono);font-size:11px;color:var(--dim);cursor:pointer;border-bottom:1px dotted var(--dim)}
.src:hover{color:var(--acc);border-color:var(--acc)}
.empty{color:var(--dim);font-size:12.5px;font-style:italic}
.hint{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:12px 14px;margin-bottom:10px}
.hint .hh{display:flex;align-items:center;gap:8px;font-weight:600;margin-bottom:8px}
.code{font-family:var(--mono);font-size:12.5px;background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:8px 10px;position:relative;white-space:pre-wrap;word-break:break-all}
.code .cm{color:var(--dim)}
.code .cp{position:absolute;top:6px;right:6px;opacity:0;transition:opacity .15s}
.code:hover .cp{opacity:1}
.hint .nt{color:var(--mute);font-size:12px;margin-top:7px}
.cols2{display:grid;grid-template-columns:repeat(auto-fit,minmax(420px,1fr));gap:12px;align-items:start}
table.t{width:100%;border-collapse:collapse;font-size:12.5px}
table.t th{text-align:left;font-weight:500;color:var(--dim);font-size:11px;letter-spacing:.5px;text-transform:uppercase;padding:6px 8px;border-bottom:1px solid var(--line)}
table.t td{padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}
table.t tr.cl{cursor:pointer}
table.t tr.cl:hover td{background:var(--hover)}
table.t td.mono{font-size:12px}
.tbl{background:var(--panel);border:1px solid var(--line);border-radius:12px;overflow:hidden}
.tbl .cap{padding:10px 12px;font-weight:600;display:flex;align-items:center;gap:8px;border-bottom:1px solid var(--line)}
.tbl .scroll{max-height:360px;overflow:auto}
.pill{display:inline-block;font-family:var(--mono);font-size:11.5px;border:1px solid var(--line);border-radius:6px;padding:1px 6px;margin:2px 3px 2px 0;color:var(--mute);background:var(--bg2);cursor:default}
.pill.cl{cursor:pointer}.pill.cl:hover{border-color:var(--acc);color:var(--text)}
.note{border-left:3px solid var(--c-yocto);background:color-mix(in srgb,var(--c-yocto) 8%,transparent);padding:8px 12px;border-radius:0 8px 8px 0;color:var(--mute);font-size:12.5px;margin-top:12px}

/* graph */
.gwrap{background:var(--panel);border:1px solid var(--line);border-radius:14px;overflow:hidden}
.gbar{display:flex;align-items:center;gap:8px;padding:8px 10px;border-bottom:1px solid var(--line);color:var(--mute);font-size:12px}
.gbar .sp{flex:1}
.gcanvas{height:560px;overflow:auto;position:relative;
  background-image:radial-gradient(circle at 1px 1px,var(--line) 1px,transparent 0);background-size:22px 22px}
.gcanvas.tall{height:78vh}
svg.g{display:block}
.lk{fill:none;stroke:var(--line2);stroke-width:1.3;transition:d .32s ease}
.nd{cursor:pointer;transition:transform .32s ease,opacity .25s;transform-box:fill-box}
.nd rect.bx{fill:var(--panel2);stroke:var(--line2);stroke-width:1}
.nd:hover rect.bx{stroke:var(--k,var(--acc))}
.nd.sel rect.bx{stroke:var(--acc);stroke-width:1.8}
.nd text{font:12px var(--mono);fill:var(--text);dominant-baseline:middle}
.nd text.sub{fill:var(--dim);font-size:11px}
.nd circle.kd{fill:var(--k,var(--mute))}
.nd.root rect.bx{fill:color-mix(in srgb,var(--k,var(--acc)) 22%,var(--panel));stroke:var(--k,var(--acc));stroke-width:1.6}
.nd.root text{font-weight:700;font-size:13px}
.nd.cat rect.bx{fill:color-mix(in srgb,var(--k) 13%,var(--panel));stroke:color-mix(in srgb,var(--k) 55%,transparent)}
.nd.group text.lbl{fill:var(--mute);font:600 10.5px var(--sans);letter-spacing:.8px;text-transform:uppercase}
.nd.group rect.bx{fill:transparent;stroke:transparent}
.nd.fact rect.bx,.nd.item rect.bx,.nd.ref rect.bx{fill:transparent;stroke:transparent}
.nd.fact:hover rect.bx,.nd.item:hover rect.bx,.nd.ref:hover rect.bx{fill:var(--hover)}
.nd.fact text.lbl{fill:var(--text)}
.nd.fact text.lbl tspan.k{fill:var(--acc)}
.nd.ref text.lbl,.nd.dim text{fill:var(--dim)}
.nd.more rect.bx{fill:transparent;stroke-dasharray:4 3}
.nd .exp circle{fill:var(--panel);stroke:var(--line2)}
.nd .exp text{font:600 12px var(--sans);fill:var(--mute);text-anchor:middle;dominant-baseline:central}
.nd.loading .exp circle{stroke:var(--acc);stroke-dasharray:3 3;animation:spin 1s linear infinite;transform-box:fill-box;transform-origin:center}
@keyframes spin{to{transform:rotate(360deg)}}

/* overview */
.hero{display:flex;align-items:flex-end;gap:24px;flex-wrap:wrap}
.big{font-size:34px;font-weight:700;letter-spacing:-1px;line-height:1}
.big small{font-size:13px;color:var(--mute);font-weight:500;letter-spacing:0;margin-left:6px}
.dist{display:flex;height:14px;border-radius:7px;overflow:hidden;margin:14px 0 10px;background:var(--line)}
.dist i{display:block;height:100%;background:var(--k);cursor:pointer;transition:filter .15s}
.dist i:hover{filter:brightness(1.2)}
.legend{display:flex;flex-wrap:wrap;gap:6px}
.areas{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:10px}
.area{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:12px;cursor:pointer;transition:border-color .15s,transform .15s}
.area:hover{border-color:var(--acc);transform:translateY(-1px)}
.area .an{font-family:var(--mono);font-weight:600;font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.area .ac{color:var(--dim);font-size:12px;margin:2px 0 8px}
.area .mini{width:100%;height:6px}
.area .ak{margin-top:8px;display:flex;gap:6px;flex-wrap:wrap;font-size:11px;color:var(--mute)}

/* drawer */
.drawer{position:fixed;top:52px;right:0;bottom:0;width:min(760px,58vw);background:var(--panel);border-left:1px solid var(--line2);box-shadow:var(--shadow);transform:translateX(102%);transition:transform .25s ease;display:flex;flex-direction:column;z-index:10}
.drawer.on{transform:none}
.dh{padding:12px 14px;border-bottom:1px solid var(--line);display:flex;align-items:flex-start;gap:10px}
.dh .ttl{flex:1;min-width:0}
.dh .fn{font-family:var(--mono);font-weight:600;font-size:13.5px;word-break:break-all}
.dh .rp{font-family:var(--mono);font-size:11.5px;color:var(--dim);margin-top:2px;word-break:break-all}
.db{flex:1;overflow:auto;min-height:0}
.ins{padding:12px 14px;border-bottom:1px solid var(--line);background:var(--panel2)}
.ins h3{margin:6px 0 6px;font-size:11px;letter-spacing:.8px;text-transform:uppercase;color:var(--dim);font-weight:600}
.kv{display:grid;grid-template-columns:minmax(120px,max-content) 1fr auto;gap:3px 12px;font-size:12px}
.kv .k{font-family:var(--mono);color:var(--acc);white-space:nowrap}
.kv .v{font-family:var(--mono);word-break:break-word}
.kv .l{font-family:var(--mono);color:var(--dim);cursor:pointer}
.kv .l:hover{color:var(--acc)}
.src-view{font:12px/1.55 var(--mono);padding:6px 0 40px;counter-reset:ln}
.ln{display:flex;white-space:pre;min-height:18px}
.ln .no{width:52px;flex:none;text-align:right;padding-right:12px;color:var(--dim);user-select:none}
.ln .tx{white-space:pre-wrap;word-break:break-all;padding-right:12px}
.ln.hl{background:color-mix(in srgb,var(--acc) 16%,transparent);box-shadow:inset 3px 0 0 var(--acc)}
.tx .cm{color:var(--dim);font-style:italic}.tx .st{color:#c3e88d}.tx .vr{color:var(--c-qnx)}.tx .ky{color:var(--acc)}
:root[data-theme=light] .tx .st{color:#2f7d32}
.loader{padding:30px;color:var(--dim)}
.toast{position:fixed;bottom:18px;left:50%;transform:translateX(-50%) translateY(40px);background:var(--text);color:var(--bg);padding:7px 14px;border-radius:8px;font-size:12px;opacity:0;transition:.2s;z-index:20}
.toast.on{opacity:1;transform:translateX(-50%)}
@media (max-width:900px){.body{grid-template-columns:1fr}.side{display:none}.drawer{width:100vw}.rootp{display:none}}
</style>
</head>
<body>
<header class="top">
  <div class="brand" data-act="home" title="Tổng quan">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><circle cx="5" cy="12" r="2.4" fill="var(--acc)" stroke="none"/><circle cx="19" cy="5" r="2"/><circle cx="19" cy="12" r="2"/><circle cx="19" cy="19" r="2"/><path d="M7.4 12H17M7 11c4-6 6-6 10-6M7 13c4 6 6 6 10 6"/></svg>
    modmap
  </div>
  <div class="rootp" id="rootp"></div>
  <div class="search"><input id="q" placeholder="Tìm repo theo đường dẫn / tên   ( / )" autocomplete="off" spellcheck="false"></div>
  <div class="idx" id="idx"></div>
  <button class="btn" id="theme" title="Đổi giao diện sáng/tối"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="8"/><path d="M12 4a8 8 0 0 1 0 16z" fill="currentColor"/></svg></button>
</header>
<div class="body">
  <aside class="side">
    <div class="chips" id="chips"></div>
    <div class="treehead"><span id="tcount"></span><span><button class="btn ghost" data-act="collapse-all" style="height:22px">Thu gọn</button></span></div>
    <div class="tree" id="tree"><div class="spacer" id="spacer"></div><div id="rows"></div></div>
  </aside>
  <main id="main"><div class="loader">Đang đọc workspace…</div></main>
</div>
<div class="drawer" id="drawer">
  <div class="dh"><div class="ttl"><div class="fn" id="dfn"></div><div class="rp" id="drp"></div></div>
    <button class="btn ghost" data-act="drawer-repo" title="Mở repo chứa file">Repo</button>
    <button class="btn ghost" data-act="close-drawer" title="Đóng (Esc)">✕</button></div>
  <div class="db" id="dbody"></div>
</div>
<div class="toast" id="toast"></div>
<script>
'use strict';
const KIND = {
  'yocto-layer':{l:'Yocto layer',c:'yocto'}, 'yocto-multi':{l:'Yocto (nhiều layer)',c:'yocto'}, 'bitbake':{l:'BitBake',c:'yocto'},
  'kernel':{l:'Linux kernel',c:'kernel'}, 'u-boot':{l:'U-Boot',c:'uboot'},
  'android-device':{l:'Android device',c:'android'}, 'android':{l:'Android',c:'android'},
  'qnx':{l:'QNX',c:'qnx'}, 'cmake':{l:'CMake',c:'cmake'}, 'meson':{l:'Meson',c:'meson'}, 'bazel':{l:'Bazel',c:'bazel'},
  'autotools':{l:'Autotools',c:'autotools'}, 'make':{l:'Make',c:'make'}, 'devicetree':{l:'Device tree',c:'dt'},
  'cargo':{l:'Rust',c:'lang'}, 'go':{l:'Go',c:'lang'}, 'gradle':{l:'Gradle/Maven',c:'lang'}, 'node':{l:'Node.js',c:'lang'},
  'python':{l:'Python',c:'lang'}, 'other':{l:'Khác',c:'other'}, 'missing':{l:'Chưa sync',c:'missing'}
};
const GROUPC = {Yocto:'yocto',Android:'android',QNX:'qnx',Kernel:'kernel',CMake:'cmake',Make:'make',Meson:'meson',Autotools:'autotools',Bazel:'bazel','Khác':'other'};
const kc = k => 'var(--c-'+((KIND[k]||KIND.other).c)+')';
const gc = g => 'var(--c-'+(GROUPC[g]||'other')+')';
const kl = k => (KIND[k]||{l:k}).l;
const $ = (s,el=document)=>el.querySelector(s);
const esc = s => String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const bn = p => String(p||'').split('/').pop();
const dn = p => { const i=String(p||'').lastIndexOf('/'); return i<0?'':p.slice(0,i); };
async function api(p){ const r=await fetch(p); let j; try{ j=await r.json(); }catch(e){ throw new Error('HTTP '+r.status); } if(!r.ok) throw new Error(j.error||('HTTP '+r.status)); return j; }
function toast(m){ const t=$('#toast'); t.textContent=m; t.classList.add('on'); clearTimeout(t._t); t._t=setTimeout(()=>t.classList.remove('on'),1600); }
function lsGet(k){ try{ return localStorage.getItem(k); }catch(e){ return null; } }
function lsSet(k,v){ try{ localStorage.setItem(k,v); }catch(e){} }

const S = { ws:null, root:null, byPath:new Map(), flat:[], expanded:new Set(), q:'', kinds:new Set(), sel:null, index:null,
            repoCache:new Map(), fileCache:new Map(), current:null, graph:null, drawer:null };

/* ---------------- theme ---------------- */
(function(){ const t=lsGet('modmap-theme'); if(t) document.documentElement.dataset.theme=t; })();
$('#theme').onclick=()=>{ const cur=document.documentElement.dataset.theme || (matchMedia('(prefers-color-scheme: light)').matches?'light':'dark');
  const nx = cur==='light'?'dark':'light'; document.documentElement.dataset.theme=nx; lsSet('modmap-theme',nx); };
if(!document.documentElement.dataset.theme && matchMedia('(prefers-color-scheme: light)').matches) document.documentElement.dataset.theme='light';

/* ---------------- tree ---------------- */
function buildTree(repos){
  const root={name:'',path:'',kids:new Map(),repo:null};
  for(const r of repos){
    const parts = r.path==='.'?['.']:r.path.split('/'); let n=root, acc='';
    for(const p of parts){ acc = acc?acc+'/'+p:p; let c=n.kids.get(p); if(!c){ c={name:p,path:acc,kids:new Map(),repo:null}; n.kids.set(p,c);} n=c; }
    n.repo=r;
  }
  const fin = n => {
    const arr=[...n.kids.values()]; arr.forEach(fin);
    n.children = arr.map(c=>{ while(!c.repo && c.children.length===1){ const g=c.children[0]; g.name=c.name+'/'+g.name; c=g; } return c; })
      .sort((a,b)=> (b.children.length>0)-(a.children.length>0) || a.name.localeCompare(b.name));
    n.count=(n.repo?1:0)+n.children.reduce((s,c)=>s+c.count,0);
    n.kinds={}; if(n.repo) n.kinds[n.repo.kind]=1;
    for(const c of n.children){ c.parent=n; for(const k in c.kinds) n.kinds[k]=(n.kinds[k]||0)+c.kinds[k]; }
    delete n.kids;
  };
  fin(root);
  const idx=new Map(); const walk=n=>{ idx.set(n.path,n); n.children.forEach(walk); }; walk(root);
  S.nodeByPath=idx;
  return root;
}
function minibar(kinds,total){
  const ent=Object.entries(kinds).sort((a,b)=>b[1]-a[1]);
  return '<span class="mini">'+ent.map(([k,v])=>`<i style="--k:${kc(k)};width:${(v/total*100).toFixed(2)}%"></i>`).join('')+'</span>';
}
function markMatch(n,f,ks){
  let self=false;
  if(n.repo) self = (!f || n.path.toLowerCase().includes(f) || (n.repo.name||'').toLowerCase().includes(f)) && (!ks.size || ks.has(n.repo.kind));
  let any=self; for(const c of n.children) if(markMatch(c,f,ks)) any=true;
  n._self=self; n._m=any; return any;
}
function flatten(){
  const out=[]; const f=S.q.toLowerCase().trim(); const filtering = !!f || S.kinds.size>0;
  if(filtering) markMatch(S.root,f,S.kinds);
  const walk=(n,d)=>{ for(const c of n.children){ if(filtering && !c._m) continue; out.push({n:c,d});
      const open = filtering ? (c.children.length>0 && c.children.some(x=>x._m)) : S.expanded.has(c.path);
      if(c.children.length && open) walk(c,d+1); } };
  walk(S.root,0); S.flat=out;
  let shown=0; for(const r of out) if(r.n.repo && (!filtering||r.n._self)) shown++;
  $('#tcount').textContent = filtering ? `${shown} repo khớp` : `${S.ws.repos.length} repo`;
}
const ROWH=26;
const FOLDER='<svg class="fico" viewBox="0 0 16 16" fill="currentColor"><path d="M1.5 3.5A1.5 1.5 0 0 1 3 2h3.2l1.5 1.6H13a1.5 1.5 0 0 1 1.5 1.5v6.4A1.5 1.5 0 0 1 13 13H3a1.5 1.5 0 0 1-1.5-1.5z" opacity=".55"/></svg>';
const CARET='<svg viewBox="0 0 16 16" width="10" height="10" fill="currentColor"><path d="M5 3l6 5-6 5z"/></svg>';
function hlText(s,f){ if(!f) return esc(s); const i=s.toLowerCase().indexOf(f); if(i<0) return esc(s); return esc(s.slice(0,i))+'<mark>'+esc(s.slice(i,i+f.length))+'</mark>'+esc(s.slice(i+f.length)); }
function renderTree(){
  const el=$('#tree'); const top=el.scrollTop, hgt=el.clientHeight||600; const f=S.q.toLowerCase().trim(); const filtering=!!f||S.kinds.size>0;
  const a=Math.max(0,Math.floor(top/ROWH)-12), b=Math.min(S.flat.length,Math.ceil((top+hgt)/ROWH)+12);
  $('#spacer').style.height=(S.flat.length*ROWH)+'px';
  let html='';
  for(let i=a;i<b;i++){
    const {n,d}=S.flat[i]; const hasK=n.children.length>0; const open = filtering ? true : S.expanded.has(n.path);
    const isRepo=!!n.repo; const dim = filtering && isRepo && !n._self;
    html+=`<div class="row ${isRepo?'repo':'folder'} ${S.sel===n.path?'sel':''}" style="top:${i*ROWH}px;padding-left:${6+d*14}px;${dim?'opacity:.45':''}" data-act="node" data-path="${esc(n.path)}" title="${esc(n.path)}">`;
    html+= hasK ? `<span class="car ${open?'open':''}" data-act="toggle" data-path="${esc(n.path)}">${CARET}</span>` : '<span class="car"></span>';
    html+= isRepo ? `<span class="dot" style="--k:${kc(n.repo.kind)}"></span>` : FOLDER;
    html+=`<span class="nm">${hlText(n.name,f)}</span>`;
    if(hasK) html+=`<span class="cnt">${n.count}${minibar(n.kinds,n.count)}</span>`;
    else if(isRepo) html+=`<span class="kt">${esc(kl(n.repo.kind))}</span>`;
    html+='</div>';
  }
  $('#rows').innerHTML=html;
}
function refreshTree(){ flatten(); renderTree(); }
function reveal(path, scroll=true){
  let n=S.nodeByPath.get(path); if(!n){ // collapsed chain: find nearest
    for(const [p,node] of S.nodeByPath) if(path.startsWith(p+'/') || p===path){ if(!n || p.length>n.path.length) n=node; }
  }
  if(!n) return;
  for(let p=n.parent;p;p=p.parent) if(p.path) S.expanded.add(p.path);
  refreshTree();
  if(scroll){ const i=S.flat.findIndex(r=>r.n===n); if(i>=0){ const el=$('#tree'); const y=i*ROWH; if(y<el.scrollTop+40||y>el.scrollTop+el.clientHeight-60) el.scrollTop=Math.max(0,y-el.clientHeight/3); renderTree(); } }
}
function renderChips(){
  const cnt={}; for(const r of S.ws.repos) cnt[r.kind]=(cnt[r.kind]||0)+1;
  $('#chips').innerHTML = Object.keys(KIND).filter(k=>cnt[k]).sort((a,b)=>cnt[b]-cnt[a]).map(k=>
    `<span class="chip ${S.kinds.has(k)?'on':''}" style="--k:${kc(k)}" data-act="filter-kind" data-kind="${k}"><span class="dot"></span>${esc(kl(k))} <b>${cnt[k]}</b></span>`).join('');
}

/* ---------------- routing ---------------- */
function go(hash){ if(location.hash!==hash) location.hash=hash; else route(); }
function route(){
  const h=decodeURIComponent(location.hash.slice(1));
  if(h.startsWith('/repo/')) return showRepo(h.slice(6));
  S.sel=null; renderTree(); showOverview();
}
window.addEventListener('hashchange',route);

/* ---------------- overview ---------------- */
function showOverview(){
  S.current=null; const ws=S.ws; const m=$('#main');
  const cnt={}; for(const r of ws.repos) cnt[r.kind]=(cnt[r.kind]||0)+1;
  const total=ws.repos.length; const ks=Object.keys(cnt).sort((a,b)=>cnt[b]-cnt[a]);
  const remotes = ws.manifest ? Object.entries(ws.manifest.remotes||{}) : [];
  let html=`<div class="wrap">
   <div class="crumbs">${esc(ws.root)}</div>
   <div class="hero"><div><h1>Tổng quan workspace</h1>
     <div class="meta"><span><b>nguồn</b>${ws.mode==='manifest'?'manifest repo ('+esc((ws.manifest.files||[]).length)+' file)':'quét thư mục .git'}</span>
     ${ws.manifest&&ws.manifest.default&&ws.manifest.default.revision?`<span><b>revision mặc định</b><span class="mono">${esc(ws.manifest.default.revision)}</span></span>`:''}
     ${remotes.length?`<span><b>remote</b><span class="mono">${remotes.map(([k,v])=>esc(k)).join(', ')}</span></span>`:''}
     <span><b>phân loại</b>${ws.scan_took}s</span></div></div>
     <div style="margin-left:auto;text-align:right"><div class="big">${total}<small>repo</small></div></div></div>
   <div class="dist">${ks.map(k=>`<i title="${esc(kl(k))}: ${cnt[k]}" style="--k:${kc(k)};width:${(cnt[k]/total*100).toFixed(3)}%" data-act="filter-kind" data-kind="${k}"></i>`).join('')}</div>
   <div class="legend">${ks.map(k=>`<span class="chip ${S.kinds.has(k)?'on':''}" style="--k:${kc(k)}" data-act="filter-kind" data-kind="${k}"><span class="dot"></span>${esc(kl(k))} <b>${cnt[k]}</b></span>`).join('')}</div>
   <div id="platforms"></div>
   <h2>Khu vực <span class="n">${S.root.children.length} nhánh gốc</span></h2>
   <div class="areas">${S.root.children.map(n=>`<div class="area" data-act="${n.repo&&!n.children.length?'open-repo':'reveal'}" data-path="${esc(n.path)}">
       <div class="an" title="${esc(n.path)}">${esc(n.name)}</div><div class="ac">${n.count} repo</div>${minibar(n.kinds,n.count)}
       <div class="ak">${Object.entries(n.kinds).sort((a,b)=>b[1]-a[1]).slice(0,3).map(([k,v])=>`<span><span class="dot" style="--k:${kc(k)}"></span> ${esc(kl(k))} ${v}</span>`).join('')}</div></div>`).join('')}</div>
  </div>`;
  m.innerHTML=html; m.scrollTop=0; renderPlatforms();
}
function renderPlatforms(){
  const el=$('#platforms'); if(!el) return; const ix=S.index;
  if(!ix || ix.state!=='done'){ el.innerHTML=`<h2>Nền tảng</h2><div class="empty">Đang index layer Yocto, product Android, image QNX…</div>`; return; }
  const sec=[];
  if(ix.machines.length) sec.push(tbl('Yocto MACHINE', ix.machines.length, ['machine','kiến trúc','SoC / tune','layer','kernel'],
    ix.machines.map(m=>({act:'open-file',repo:m.repo,file:m.file,cells:[`<b class="mono">${esc(m.name)}</b>`, esc(m.arch||'?'), esc([m.soc,m.tune].filter(Boolean).join(' · ')||'—'), `<span class="mono">${esc(layerName(m))}</span>`, `<span class="mono">${esc(m.kernel||'')}</span>`]})),'yocto'));
  if(ix.products.length) sec.push(tbl('Android product', ix.products.length, ['lunch / product','kiến trúc','platform','CPU','repo'],
    ix.products.map(p=>({act:'open-repo',path:p.repo,cells:[`<b class="mono">${esc(p.name)}</b>`, esc(p.arch||'?'), esc(p.platform||'—'), esc(p.cpu||''), `<span class="mono">${esc(p.repo)}</span>`]})),'android'));
  if(ix.qnx.length) sec.push(tbl('QNX image (buildfile)', ix.qnx.length, ['buildfile','kiến trúc','board','repo'],
    ix.qnx.map(q=>({act:'open-file',repo:q.repo,file:q.file,cells:[`<b class="mono">${esc(q.name)}</b>`, esc(q.arch||'?'), esc(q.board||'—'), `<span class="mono">${esc(q.repo)}</span>`]})),'qnx'));
  if(ix.layers.length) sec.push(tbl('Yocto layer', ix.layers.length, ['layer','collection','ưu tiên','recipe','tương thích'],
    ix.layers.map(l=>({act:'open-repo',path:l.repo,cells:[`<b class="mono">${esc(l.name)}</b><div class="mono" style="color:var(--dim);font-size:11px">${esc(l.repo)}${l.dir?'/'+esc(l.dir):''}</div>`, `<span class="mono">${esc(l.collection||'')}</span>`, esc(l.priority||''), l.recipes, esc(l.compat||'')]})),'yocto'));
  if(ix.images.length) sec.push(tbl('Yocto image', ix.images.length, ['image','layer'],
    ix.images.map(i=>({act:'open-file',repo:i.repo,file:i.file,cells:[`<b class="mono">${esc(i.name)}</b>`, `<span class="mono">${esc(i.layer)}</span>`]})),'yocto'));
  el.innerHTML = `<h2>Nền tảng <span class="n">index ${ix.took}s · ${ix.xref_count} liên kết recipe → repo</span></h2>` +
    (sec.length?`<div class="cols2">${sec.join('')}</div>`:'<div class="empty">Không tìm thấy layer Yocto, product Android hay buildfile QNX.</div>');
}
function layerName(m){ const l=(S.index.layers||[]).find(x=>x.repo===m.repo && m.file.startsWith(x.dir?x.dir+'/':'')); return l?l.name:m.repo; }
function tbl(title,n,heads,rows,c){
  return `<div class="tbl"><div class="cap"><span class="dot" style="--k:var(--c-${c})"></span>${esc(title)} <span class="n" style="color:var(--dim);font-weight:400">${n}</span></div><div class="scroll"><table class="t"><tr>${heads.map(h=>`<th>${esc(h)}</th>`).join('')}</tr>`+
   rows.map(r=>`<tr class="cl" data-act="${r.act}" data-path="${esc(r.path||'')}" data-repo="${esc(r.repo||'')}" data-file="${esc(r.file||'')}">${r.cells.map(c=>`<td>${c}</td>`).join('')}</tr>`).join('')+`</table></div></div>`;
}

/* ---------------- repo view ---------------- */
const ICON={
 os:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="3" y="4" width="18" height="13" rx="2"/><path d="M8 21h8M12 17v4"/></svg>',
 arch:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M4 7h16M4 12h16M4 17h16"/><circle cx="8" cy="7" r="1.6" fill="currentColor"/><circle cx="15" cy="12" r="1.6" fill="currentColor"/><circle cx="10" cy="17" r="1.6" fill="currentColor"/></svg>',
 chip:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="6" y="6" width="12" height="12" rx="2"/><rect x="9.5" y="9.5" width="5" height="5"/><path d="M9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4"/></svg>',
 build:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 5.4-5.4l-2.6 2.6-2.4-.6-.6-2.4z"/></svg>',
 mach:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="3" y="5" width="18" height="10" rx="2"/><path d="M7 19h10M7 10h.01M11 10h6"/></svg>'
};
async function getRepo(path,force){ if(force) S.repoCache.delete(path); if(!S.repoCache.has(path)) S.repoCache.set(path, api('/api/repo?path='+encodeURIComponent(path)+(force?'&force=1':''))); try{ return await S.repoCache.get(path); }catch(e){ S.repoCache.delete(path); throw e; } }
function getFile(repo,file){ const k=repo+'\n'+file; if(!S.fileCache.has(k)) S.fileCache.set(k, api('/api/file?repo='+encodeURIComponent(repo)+'&file='+encodeURIComponent(file)).catch(e=>{S.fileCache.delete(k); throw e;})); return S.fileCache.get(k); }

async function showRepo(path, force){
  S.sel=path; reveal(path); const m=$('#main');
  m.innerHTML=`<div class="wrap"><div class="crumbs">${esc(path)}</div><div class="loader">Đang phân tích repo…</div></div>`;
  let a; try{ a=await getRepo(path, force); }catch(e){ m.innerHTML=`<div class="wrap"><div class="crumbs">${esc(path)}</div><div class="note">Lỗi: ${esc(e.message)}</div></div>`; return; }
  if(S.sel!==path) return;
  S.current=a; renderRepo(a);
}
function crumbs(path){
  const parts=path.split('/'); let acc=''; return '<a data-act="home">workspace</a> / '+parts.map((p,i)=>{ acc=acc?acc+'/'+p:p; return i<parts.length-1?`<a data-act="reveal" data-path="${esc(acc)}">${esc(p)}</a>`:esc(p); }).join(' / ');
}
function candBlock(list, empty){
  if(!list||!list.length) return `<div class="empty">${empty}</div>`;
  return list.slice(0,6).map((c,i)=>`<div class="val ${i?'second':''}"><span class="conf ${c.conf}" title="độ tin cậy: ${c.conf}"></span><span class="v">${esc(c.v)}</span>${
    groupSrc(c.src).slice(0,2).map(s=>s.xrepo?`<span class="src" data-act="open-file" data-repo="${esc(s.xrepo)}" data-file="${esc(s.xfile)}" data-line="${s.line||''}" title="${esc(s.note||'')}">${esc(bn(s.xfile))}${s.lineTxt}</span>`
      : s.dir?`<span class="src" style="cursor:default;border-bottom-style:none" title="${esc(s.note||'')}">${esc(s.file)}</span>`
      : s.file?`<span class="src" data-act="open-file" data-repo="${esc(S.current.repo.path)}" data-file="${esc(s.file)}" data-line="${s.line||''}" title="${esc(s.note||'')}">${esc(bn(s.file))}${s.lineTxt}</span>`:'').join(' ')}</div>`).join('')
   + (list.length>6?`<div class="empty">+${list.length-6} khả năng khác</div>`:'');
}
function groupSrc(src){
  const m=new Map();
  for(const s of src){ const k=(s.xrepo||'')+'|'+s.file; if(!m.has(k)) m.set(k,{...s,lines:[]}); if(s.line) m.get(k).lines.push(s.line); }
  return [...m.values()].map(s=>({...s,line:s.lines[0]||null,lineTxt:s.lines.length?':'+s.lines.slice(0,3).join(','):''}));
}
function renderRepo(a){
  const r=a.repo; const m=$('#main'); const P=a.profile;
  const git=a.git;
  let html=`<div class="wrap">
   <div class="crumbs">${crumbs(r.path)}</div>
   <div class="titlerow"><h1 class="mono">${esc(bn(r.path))}</h1><span class="badge" style="--k:${kc(r.kind)}">${esc(kl(r.kind))}</span>
     ${(r.tags||[]).filter(t=>t!==r.kind).map(t=>`<span class="tagl">${esc(kl(t))}</span>`).join('')}
     <span style="flex:1"></span><button class="btn" data-act="rescan">↻ Quét lại</button></div>
   <div class="meta">
     <span><b>project</b><span class="mono">${esc(r.name)}</span></span>
     ${r.revision?`<span><b>revision</b><span class="mono">${esc(r.revision)}</span></span>`:''}
     ${r.remote?`<span><b>remote</b><span class="mono">${esc(r.remote)}</span></span>`:''}
     ${r.url?`<span><b>url</b><span class="mono">${esc(r.url)}</span></span>`:''}
     ${r.groups?`<span><b>groups</b><span class="mono">${esc(r.groups)}</span></span>`:''}
     ${git?`<span><b>HEAD</b><span class="mono">${esc((git.head||'').slice(0,10))}</span> · ${esc(git.date)} · ${esc(git.subject)}</span>`:''}
     <span><b>file</b>${a.total_files.toLocaleString()}${a.truncated?'+':''} · đọc ${a.parsed_files} file build · ${a.took}s</span>
   </div>
   ${a.exists?'':'<div class="note">Thư mục chưa tồn tại trên đĩa (chưa sync hoặc bị xóa).</div>'}
   <h2>Hồ sơ module</h2>
   <div class="cards">
     <div class="card"><div class="lb">${ICON.os} Hệ điều hành đích</div>${candBlock(P.os,'Chưa xác định')}</div>
     <div class="card"><div class="lb">${ICON.arch} Kiến trúc</div>${candBlock(P.arch,'Chưa xác định — xem file build bên dưới')}</div>
     <div class="card"><div class="lb">${ICON.chip} Chip / SoC / board</div>${candBlock(P.chip,'Chưa thấy thông tin chip')}</div>
     <div class="card"><div class="lb">${ICON.build} Hệ thống build</div>${candBlock(P.build,'Chưa nhận ra')}</div>
   </div>
   ${P.machine&&P.machine.length?`<div class="card" style="margin-top:12px"><div class="lb">${ICON.mach} MACHINE (Yocto) <span class="n" style="text-transform:none;letter-spacing:0">${P.machine.length}</span></div>${P.machine.map(x=>`<span class="pill cl" data-act="open-file" data-repo="${esc(r.path)}" data-file="${esc((x.src[0]||{}).file||'')}">${esc(x.v)}</span>`).join('')}</div>`:''}
   <h2>Cách build <span class="n">suy luận tĩnh, chưa chạy thử</span></h2>
   ${a.hints.length?a.hints.map(hn=>`<div class="hint"><div class="hh"><span class="conf ${hn.conf}"></span>${esc(hn.title)}</div>
      <div class="code">${hn.cmds.map(c=>{ const i=c.indexOf('   #'); return i>0?esc(c.slice(0,i))+'<span class="cm">'+esc(c.slice(i))+'</span>':esc(c); }).join('\n')}<button class="btn cp" data-act="copy" data-text="${esc(hn.cmds.map(c=>c.split('   #')[0]).join('\n'))}">Copy</button></div>
      ${hn.note?`<div class="nt">${esc(hn.note)}</div>`:''}</div>`).join(''):'<div class="empty">Không đủ dữ liệu để gợi ý.</div>'}
   ${yoctoBlock(a)}
   <h2>Bản đồ module <span class="n">bấm để mở sâu · bấm file để xem nội dung</span></h2>
   <div class="gwrap"><div class="gbar"><span id="gstat"></span><span class="sp"></span>
     <button class="btn ghost" data-act="g-expand">Mở mọi nhóm</button><button class="btn ghost" data-act="g-collapse">Thu gọn</button><button class="btn ghost" data-act="g-tall">⤢</button></div>
     <div class="gcanvas" id="gcanvas"></div></div>
   <h2>Thư mục gốc của repo <span class="n">${a.top.length}</span></h2>
   <div>${a.top.map(t=>`<span class="pill ${t.endsWith('/')?'':'cl'}" ${t.endsWith('/')?'':`data-act="open-file" data-repo="${esc(r.path)}" data-file="${esc(t)}"`}>${esc(t)}</span>`).join('')||'<span class="empty">trống</span>'}</div>
   ${a.index_state!=='done'?'<div class="note">Đang index Yocto/Android/QNX ở nền — liên kết recipe → repo sẽ tự cập nhật khi xong.</div>':''}
  </div>`;
  m.innerHTML=html; m.scrollTop=0;
  S.graph = new Graph($('#gcanvas'), repoGraph(a), {onSelect:graphSelect});
}
function yoctoBlock(a){
  const y=a.yocto||{}; const out=[];
  if(y.used_by&&y.used_by.length){
    out.push(tbl('Recipe Yocto lấy repo này', y.used_by.length, ['recipe','layer','SRC_URI','tin cậy'],
      y.used_by.map(x=>({act:'open-file',repo:x.repo,file:x.file,cells:[`<b class="mono">${esc(bn(x.file))}</b><div class="mono" style="color:var(--dim);font-size:11px">${esc(x.repo)}/${esc(dn(x.file))}</div>`, `<span class="mono">${esc(x.layer)}</span>`, `<span class="mono" style="font-size:11px">${esc(x.value)}${x.params?';'+esc(x.params):''}</span>`, `<span class="conf ${x.conf}"></span> ${esc(x.conf)}`]})),'yocto'));
  }
  if(y.layers&&y.layers.length) out.push(tbl('Layer trong repo', y.layers.length, ['layer','collection','ưu tiên','phụ thuộc','recipe'],
      y.layers.map(l=>({act:'open-file',repo:l.repo,file:(l.dir?l.dir+'/':'')+'conf/layer.conf',cells:[`<b class="mono">${esc(l.name)}</b>`,`<span class="mono">${esc(l.collection||'')}</span>`,esc(l.priority||''),`<span class="mono" style="font-size:11px">${esc(l.depends)}</span>`,l.recipes]})),'yocto'));
  if(y.machines&&y.machines.length) out.push(tbl('MACHINE', y.machines.length, ['machine','kiến trúc','SoC / tune','dtb'],
      y.machines.map(x=>({act:'open-file',repo:x.repo,file:x.file,cells:[`<b class="mono">${esc(x.name)}</b>`,esc(x.arch||'?'),esc([x.soc,x.tune].filter(Boolean).join(' · ')||'—'),`<span class="mono" style="font-size:11px">${esc(x.dtb||'')}</span>`]})),'yocto'));
  if(y.images&&y.images.length) out.push(tbl('Image', y.images.length, ['image','file'], y.images.map(x=>({act:'open-file',repo:x.repo,file:x.file,cells:[`<b class="mono">${esc(x.name)}</b>`,`<span class="mono">${esc(x.file)}</span>`]})),'yocto'));
  const ap=(a.android||{}).products||[];
  if(ap.length) out.push(tbl('Android product', ap.length, ['lunch','kiến trúc','platform','CPU'], ap.map(p=>({act:'open-file',repo:p.repo,file:p.file,cells:[`<b class="mono">${esc(p.name)}</b>`,esc(p.arch||'?'),esc(p.platform||'—'),esc(p.cpu||'')]})),'android'));
  const qi=(a.qnx||{}).images||[];
  if(qi.length) out.push(tbl('QNX image', qi.length, ['buildfile','kiến trúc','board'], qi.map(q=>({act:'open-file',repo:q.repo,file:q.file,cells:[`<b class="mono">${esc(q.file)}</b>`,esc(q.arch||'?'),esc(q.board||'—')]})),'qnx'));
  if(!out.length) return '';
  return `<h2>Liên kết hệ sinh thái</h2><div class="cols2">${out.join('')}</div>`;
}

/* ---------------- graph ---------------- */
let UID=0; const uid=()=> 'n'+(++UID);
const mctx=document.createElement('canvas').getContext('2d');
function tw(s,font){ mctx.font=font; return mctx.measureText(s).width; }
const MONO12='12px '+getComputedStyle(document.documentElement).getPropertyValue('--mono');
function fit(s,max,font){ s=String(s); if(tw(s,font)<=max) return s; let lo=0,hi=s.length; while(lo<hi){ const mid=(lo+hi+1)>>1; if(tw(s.slice(0,mid)+'…',font)<=max) lo=mid; else hi=mid-1; } return s.slice(0,lo)+'…'; }

class Graph{
  constructor(host, root, opts){
    this.host=host; this.root=root; this.opts=opts||{}; this.els=new Map(); this.lks=new Map(); this.sel=null;
    host.innerHTML='<svg class="g" xmlns="http://www.w3.org/2000/svg"><g class="links"></g><g class="nodes"></g></svg>';
    this.svg=host.querySelector('svg'); this.gl=this.svg.querySelector('.links'); this.gn=this.svg.querySelector('.nodes');
    this.link(root,null); this.render();
    this.svg.addEventListener('click',e=>{ const g=e.target.closest('.nd'); if(!g) return; const n=g._n; if(n) this.click(n, e.target.closest('.exp')); });
  }
  link(n,p){ n.parent=p; if(!n.id) n.id=uid(); (n.children||[]).forEach(c=>this.link(c,n)); }
  layout(){
    const COLW=[0]; let y=0; const vis=[], links=[]; const ROW=28;
    const widths={};
    const rec=(n,d)=>{ n.d=d; vis.push(n); const w=this.nodeW(n); widths[d]=Math.max(widths[d]||0,w);
      if(n.open && n.children && n.children.length){ for(const c of n.children){ links.push(c); rec(c,d+1); } n.y=(n.children[0].y+n.children[n.children.length-1].y)/2; }
      else { n.y=y; y+=ROW; } };
    rec(this.root,0);
    const xs=[0]; const ds=Object.keys(widths).map(Number).sort((a,b)=>a-b);
    for(const d of ds) xs[d+1]=xs[d]+Math.min(widths[d],330)+70;
    for(const n of vis) n.x=xs[n.d];
    const maxX=Math.max(...vis.map(n=>n.x+this.nodeW(n)))+40;
    return {vis,links,w:maxX+40,h:y+30};
  }
  label(n){
    if(n.kind==='fact') return n.k!=null ? n.k+' = '+n.v : n.label;
    return n.label;
  }
  nodeW(n){
    if(n._w!=null) return n._w;
    const font = n.kind==='root' ? '700 13px '+MONO12.split(' ').slice(1).join(' ') : MONO12;
    const lab=fit(this.label(n), n.kind==='fact'||n.kind==='item'||n.kind==='ref'?380:250, font);
    n._lab=lab; const sub=n.sub!=null?String(n.sub):''; n._sub=sub?fit(sub,180,'11px monospace'):'';
    let w=tw(lab,font)+ (n.kind==='group'?16:(n.dot===false?18:34)) + ((n.children&&n.children.length)||n.load?18:0);
    n._lw=w; n._w=w + (n._sub?tw(n._sub,'11px monospace')+12:0);
    return n._w;
  }
  expandable(n){ return !!((n.children&&n.children.length) || (n.load && !n.loaded)); }
  makeNode(n){
    const g=document.createElementNS('http://www.w3.org/2000/svg','g'); g._n=n; this.drawNode(g,n); return g;
  }
  drawNode(g,n){
    const cls=['nd',n.kind]; if(n.dim) cls.push('dim'); if(n.loading) cls.push('loading'); if(this.sel===n) cls.push('sel');
    g.setAttribute('class',cls.join(' ')); if(n.color) g.style.setProperty('--k',n.color);
    const lw=n._lw; const H=n.kind==='root'?26:22; const exp=this.expandable(n);
    let s=`<rect class="bx" x="0" y="${-H/2}" width="${lw}" height="${H}" rx="${H/2}"/>`;
    let tx=12;
    if(n.kind!=='group' && n.dot!==false){ s+=`<circle class="kd" cx="12" cy="0" r="${n.kind==='root'?5:4}"/>`; tx=22; }
    if(n.kind==='fact' && n.k!=null){
      const full=n._lab; const ki=full.indexOf(' = ');
      s+= ki>0 ? `<text class="lbl" x="${tx}" y="1"><tspan class="k">${esc(full.slice(0,ki))}</tspan> = ${esc(full.slice(ki+3))}</text>` : `<text class="lbl" x="${tx}" y="1">${esc(full)}</text>`;
    } else s+=`<text class="lbl" x="${tx}" y="1">${esc(n._lab)}</text>`;
    if(n._sub) s+=`<text class="sub" x="${lw+8}" y="1">${esc(n._sub)}</text>`;
    if(exp||n.loading){ const open=n.open&&n.children&&n.children.length; s+=`<g class="exp" transform="translate(${lw-11},0)"><circle r="7"/><text y="0.5">${n.loading?'':(open?'−':'+')}</text></g>`; }
    g.innerHTML=s; g.setAttribute('data-tip',n.tip||'');
    const t=document.createElementNS('http://www.w3.org/2000/svg','title'); t.textContent=n.tip||this.label(n)+(n.sub?'\n'+n.sub:''); g.appendChild(t);
  }
  render(){
    const L=this.layout(); const PX=18, PY=12;
    this.svg.setAttribute('width',L.w); this.svg.setAttribute('height',Math.max(L.h,100));
    const seen=new Set();
    for(const n of L.vis){
      seen.add(n.id); const x=n.x+PX, y=n.y+PY+14; let g=this.els.get(n.id); const fresh=!g;
      if(fresh){ g=this.makeNode(n); const p=n.parent; const sx=p&&p._x!=null?p._x+(p._lw||0)-10:x, sy=p&&p._y!=null?p._y:y;
        g.style.transform=`translate(${sx}px,${sy}px)`; g.style.opacity='0'; this.gn.appendChild(g); this.els.set(n.id,g); g.getBoundingClientRect(); }
      else this.drawNode(g,n);
      g.style.transform=`translate(${x}px,${y}px)`; g.style.opacity='1'; n._x=x; n._y=y;
    }
    for(const [id,g] of this.els) if(!seen.has(id)){ g.remove(); this.els.delete(id); }
    const lseen=new Set();
    for(const c of L.links){
      const p=c.parent; const x1=p._x+p._w+2, y1=p._y, x2=c._x, y2=c._y, mx=x1+(x2-x1)*.55;
      const d=`M${x1},${y1} C${mx},${y1} ${x1+(x2-x1)*.45},${y2} ${x2},${y2}`;
      let el=this.lks.get(c.id); lseen.add(c.id);
      if(!el){ el=document.createElementNS('http://www.w3.org/2000/svg','path'); el.setAttribute('class','lk');
        el.style.d=`path("M${x1},${y1} C${x1},${y1} ${x1},${y1} ${x1},${y1}")`; this.gl.appendChild(el); this.lks.set(c.id,el); el.getBoundingClientRect(); }
      el.style.d=`path("${d}")`; el.setAttribute('d',d);
      if(c.color) el.style.stroke=`color-mix(in srgb, ${c.color} 45%, var(--line2))`;
    }
    for(const [id,el] of this.lks) if(!lseen.has(id)){ el.remove(); this.lks.delete(id); }
    const st=$('#gstat'); if(st) st.textContent=`${L.vis.length} nút`;
  }
  async click(n, onExp){
    if(n.kind==='more'){ n.onMore(n); this.link(n.parent,n.parent.parent); this.render(); return; }
    if(n.load && !n.loaded){
      n.loading=true; this.render();
      try{ n.children=await n.load(n); }catch(e){ n.children=[{kind:'fact',label:'Lỗi: '+e.message,dot:false,dim:true}]; }
      n.loaded=true; n.loading=false; n.open=true; this.link(n,n.parent); n._w=null;
    } else if(n.children&&n.children.length && (onExp || !n.repo || n.kind==='cat' || n.kind==='group' || n.kind==='root' || n.open===false || n.open)) {
      if(onExp || !(n.kind==='file' && n.open===false && false)) n.open=!n.open;
    }
    this.sel=n; this.render(); this.ensureVisible(n);
    if(this.opts.onSelect) this.opts.onSelect(n);
  }
  ensureVisible(n){
    const h=this.host; const x=n._x, y=n._y; const pad=60;
    let sl=h.scrollLeft, st=h.scrollTop; const cw=h.clientWidth;
    const right = x + (n._w||100) + (n.open&&n.children&&n.children.length ? 320 : 40);
    const leftKeep = n.parent && n.parent._x!=null ? n.parent._x - pad : x - pad;
    if(right > sl+cw) sl = Math.max(0, Math.min(leftKeep, right-cw));
    if(x < sl+pad) sl=Math.max(0,x-pad);
    if(y > st+h.clientHeight-pad) st=y-h.clientHeight/2; if(y<st+pad) st=Math.max(0,y-h.clientHeight/2);
    h.scrollTo({left:sl,top:st,behavior:'smooth'});
  }
  setAll(open, depth){ const rec=(n,d)=>{ if(n.children&&n.children.length&&d<depth){ n.open=open; n.children.forEach(c=>rec(c,d+1)); } }; rec(this.root,0); if(!open) this.root.open=true; this.render(); }
}

const CAT_ITEM_LABEL={'bitbake':'Task / nguồn','make':'Target / module','cmake':'Target / package','android-bp':'Module Soong','devicetree':'Node','defconfig':'Option','qnx-buildfile':'Nội dung image','meson':'Target','autotools':'Chương trình','kconfig':'Config','cmake-presets':'Preset','manifest':'Script'};
function repoGraph(a){
  const r=a.repo; const color=kc(r.kind);
  const root={kind:'root',label:bn(r.path)||r.path,sub:r.path,open:true,color,children:[]};
  for(const [cat,info] of Object.entries(a.files)){
    const c={kind:'cat',label:info.label,sub:info.count+' file',color:gc(info.group),open:false,children:[],_list:info.list,_shown:0,_total:info.count};
    addFiles(c, r.path, 30);
    root.children.push(c);
  }
  const ub=(a.yocto||{}).used_by||[];
  if(ub.length){
    root.children.unshift({kind:'cat',label:'Recipe Yocto lấy repo này',sub:ub.length+' recipe',color:'var(--c-yocto)',open:true,
      children:ub.slice(0,40).map(x=>fileNode(x.repo,x.file,'var(--c-yocto)', x.layer+' · '+x.conf))});
  }
  if(root.children.length===1) root.children[0].open=true;
  if(!root.children.length) root.children.push({kind:'fact',label:'Không tìm thấy file build/cấu hình nào',dot:false,dim:true});
  return root;
}
function addFiles(c, repo, k){
  const more=c.children.length&&c.children[c.children.length-1].kind==='more'?c.children.pop():null;
  const next=c._list.slice(c._shown,c._shown+k); c._shown+=next.length;
  for(const f of next) c.children.push(fileNode(repo,f,c.color, dn(f)));
  const remain=c._list.length-c._shown + (c._total-c._list.length);
  if(c._shown<c._list.length) c.children.push({kind:'more',label:`+${c._list.length-c._shown} file nữa…`,dot:false,onMore:()=>addFiles(c,repo,40)});
  else if(c._total>c._list.length) c.children.push({kind:'fact',label:`(${c._total-c._list.length} file khác không liệt kê)`,dot:false,dim:true});
}
function fileNode(repo,file,color,sub){
  return {kind:'file',label:bn(file),sub:sub||'',repo,file,color,load:loadFileKids,tip:repo+'/'+file};
}
async function loadFileKids(n){
  const d=await getFile(n.repo,n.file); const p=d.parsed; const kids=[];
  const grp=(label,arr,open)=>({kind:'group',label,children:arr,open:open!=null?open:arr.length<=14,dot:false});
  if(d.binary) return [{kind:'fact',label:'(file nhị phân)',dot:false,dim:true}];
  if(p){
    if(p.facts.length) kids.push(grp('Thông tin chính · '+p.facts.length, p.facts.slice(0,60).map(f=>({kind:'fact',k:f.k,v:f.v,dot:false,repo:n.repo,file:n.file,line:f.line,tip:f.k+' '+(f.op||'=')+' '+f.v+(f.line?'\n(dòng '+f.line+')':'')})), true));
    if(p.items.length) kids.push(grp((CAT_ITEM_LABEL[p.kind]||'Mục')+' · '+p.items.length, p.items.slice(0,80).map(it=>({kind:'item',label:it.name,sub:it.t+(it.extra?' · '+it.extra:''),color:n.color,repo:n.repo,file:n.file,line:it.line}))));
    if(d.refs.length) kids.push(grp('Liên kết · '+d.refs.length, d.refs.slice(0,80).map(rf=> rf.resolved
        ? Object.assign(fileNode(rf.resolved.repo, rf.resolved.file, n.color, (rf.resolved.repo!==n.repo?'↗ '+rf.resolved.repo+' · ':'')+rf.t),{label:rf.target.length<46?rf.target:bn(rf.resolved.file)})
        : {kind:'ref',label:rf.t+' '+rf.target,sub:'chưa giải được',dim:true,dot:false,repo:n.repo,file:n.file,line:rf.line}), d.refs.length<=14));
    if(!kids.length && p.vars.length) kids.push(grp('Biến · '+p.vars.length, p.vars.slice(0,80).map(v=>({kind:'fact',k:v.k,v:v.v,dot:false,repo:n.repo,file:n.file,line:v.line}))));
  }
  if(!kids.length) kids.push({kind:'fact',label:'(không trích xuất được gì — bấm để xem nguồn)',dot:false,dim:true,repo:n.repo,file:n.file});
  if(kids.length===1) kids[0].open=true;
  return kids;
}
function graphSelect(n){
  if(n.file && (n.kind==='file' || n.line!=null || n.kind==='fact' || n.kind==='item' || n.kind==='ref')) openDrawer(n.repo,n.file,n.line);
}

/* ---------------- drawer ---------------- */
function hlLine(s){
  const t=s.trimStart(); if(t.startsWith('#')||t.startsWith('//')||t.startsWith('/*')||t.startsWith('*')) return '<span class="cm">'+esc(s)+'</span>';
  const re=/("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')|(\$[\{\(][^\}\)\s]*[\}\)])|^(\s*(?:export\s+)?[A-Za-z_][\w.\-:\/${}\[\]]*)(?=\s*(?:\?\?=|\?=|:=|\+=|=\+|\.=|=\.|!=|=)(?!=))/g;
  let out='', last=0, m;
  while((m=re.exec(s))){ out+=esc(s.slice(last,m.index)); out+= m[1]?'<span class="st">'+esc(m[1])+'</span>': m[2]?'<span class="vr">'+esc(m[2])+'</span>':'<span class="ky">'+esc(m[3])+'</span>'; last=re.lastIndex; if(m[0]==='') re.lastIndex++; }
  return out+esc(s.slice(last));
}
async function openDrawer(repo,file,line){
  S.drawer={repo,file}; const dr=$('#drawer'); dr.classList.add('on');
  $('#dfn').textContent=file; $('#drp').textContent=repo; const body=$('#dbody');
  body.innerHTML='<div class="loader">Đang đọc file…</div>';
  let d; try{ d=await getFile(repo,file); }catch(e){ body.innerHTML=`<div class="loader">Lỗi: ${esc(e.message)}</div>`; return; }
  if(S.drawer.repo!==repo||S.drawer.file!==file) return;
  const p=d.parsed; let ins='';
  if(p&&(p.facts.length||p.items.length||d.refs.length)){
    ins+='<div class="ins">';
    if(p.facts.length) ins+='<h3>Thông tin chính</h3><div class="kv">'+p.facts.slice(0,80).map(f=>`<span class="k">${esc(f.k)}</span><span class="v">${esc(f.v)}</span><span class="l" data-act="line" data-line="${f.line||''}">${f.line?':'+f.line:''}</span>`).join('')+'</div>';
    if(p.items.length) ins+=`<h3>${esc(CAT_ITEM_LABEL[p.kind]||'Mục')} · ${p.items.length}</h3><div>`+p.items.slice(0,150).map(it=>`<span class="pill cl" data-act="line" data-line="${it.line||''}" title="${esc(it.t+(it.extra?' · '+it.extra:''))}">${esc(it.name)}</span>`).join('')+'</div>';
    if(d.refs.length) ins+='<h3>Liên kết</h3><div class="kv">'+d.refs.slice(0,120).map(rf=>`<span class="k">${esc(rf.t)}</span><span class="v">${rf.resolved?`<a data-act="open-file" data-repo="${esc(rf.resolved.repo)}" data-file="${esc(rf.resolved.file)}">${esc(rf.target)}</a>${rf.resolved.repo!==repo?` <span style="color:var(--dim)">↗ ${esc(rf.resolved.repo)}</span>`:''}`:`<span style="color:var(--dim)">${esc(rf.target)}</span>`}</span><span class="l" data-act="line" data-line="${rf.line||''}">${rf.line?':'+rf.line:''}</span>`).join('')+'</div>';
    ins+='</div>';
  }
  let src='';
  if(d.binary) src='<div class="loader">File nhị phân — không hiển thị.</div>';
  else{
    const lines=d.text.split('\n'); const N=Math.min(lines.length,12000);
    const parts=new Array(N); for(let i=0;i<N;i++) parts[i]=`<div class="ln" id="L${i+1}"><span class="no">${i+1}</span><span class="tx">${hlLine(lines[i])||' '}</span></div>`;
    src='<div class="src-view">'+parts.join('')+(lines.length>N||d.truncated?`<div class="loader">… đã cắt bớt (file ${(d.size/1024).toFixed(0)} KB)</div>`:'')+'</div>';
  }
  body.innerHTML=ins+src; body.scrollTop=0;
  if(line) gotoLine(line);
}
function gotoLine(line){ const body=$('#dbody'); body.querySelectorAll('.ln.hl').forEach(x=>x.classList.remove('hl')); const el=body.querySelector('#L'+line); if(el){ el.classList.add('hl'); body.scrollTop = el.offsetTop - body.clientHeight/3; } }
function closeDrawer(){ $('#drawer').classList.remove('on'); S.drawer=null; }

/* ---------------- events ---------------- */
document.addEventListener('click', async e=>{
  const t=e.target.closest('[data-act]'); if(!t) return; const act=t.dataset.act;
  if(act==='toggle'){ e.stopPropagation(); const p=t.dataset.path; if(S.expanded.has(p)) S.expanded.delete(p); else S.expanded.add(p); refreshTree(); return; }
  if(act==='node'){ const n=S.nodeByPath.get(t.dataset.path); if(!n) return;
    if(n.repo) go('#/repo/'+encodeURIComponent(n.path));
    else { if(S.expanded.has(n.path)) S.expanded.delete(n.path); else S.expanded.add(n.path); refreshTree(); } return; }
  if(act==='home'){ go('#/'); return; }
  if(act==='open-repo'){ go('#/repo/'+encodeURIComponent(t.dataset.path)); return; }
  if(act==='reveal'){ const n=S.nodeByPath.get(t.dataset.path); if(n&&n.repo&&!n.children.length) return go('#/repo/'+encodeURIComponent(n.path)); S.expanded.add(t.dataset.path); reveal(t.dataset.path); return; }
  if(act==='open-file'){ if(!t.dataset.file) return; openDrawer(t.dataset.repo,t.dataset.file,t.dataset.line?+t.dataset.line:null); return; }
  if(act==='line'){ if(t.dataset.line) gotoLine(+t.dataset.line); return; }
  if(act==='filter-kind'){ const k=t.dataset.kind; if(S.kinds.has(k)) S.kinds.delete(k); else S.kinds.add(k); renderChips(); refreshTree(); if(!S.current) showOverview(); return; }
  if(act==='collapse-all'){ S.expanded.clear(); S.q=''; $('#q').value=''; S.kinds.clear(); renderChips(); refreshTree(); return; }
  if(act==='close-drawer'){ closeDrawer(); return; }
  if(act==='drawer-repo'){ if(S.drawer) go('#/repo/'+encodeURIComponent(S.drawer.repo)); return; }
  if(act==='copy'){ const txt=t.dataset.text; try{ await navigator.clipboard.writeText(txt); }catch(err){ const ta=document.createElement('textarea'); ta.value=txt; document.body.appendChild(ta); ta.select(); try{document.execCommand('copy');}catch(e2){} ta.remove(); } toast('Đã copy'); return; }
  if(act==='rescan'){ if(S.current){ S.fileCache.clear(); showRepo(S.current.repo.path,true); } return; }
  if(act==='g-expand'){ S.graph&&S.graph.setAll(true,2); return; }
  if(act==='g-collapse'){ S.graph&&S.graph.setAll(false,9); return; }
  if(act==='g-tall'){ $('#gcanvas').classList.toggle('tall'); return; }
});
$('#tree').addEventListener('scroll',()=>requestAnimationFrame(renderTree));
window.addEventListener('resize',()=>renderTree());
let qT; $('#q').addEventListener('input',e=>{ clearTimeout(qT); qT=setTimeout(()=>{ S.q=e.target.value; refreshTree(); $('#tree').scrollTop=0; renderTree(); },120); });
$('#q').addEventListener('keydown',e=>{ if(e.key==='Enter'){ const f=S.flat.find(r=>r.n.repo&&r.n._self!==false); if(f) go('#/repo/'+encodeURIComponent(f.n.path)); } if(e.key==='Escape'){ e.target.value=''; S.q=''; refreshTree(); e.target.blur(); } });
document.addEventListener('keydown',e=>{ if(e.key==='/' && document.activeElement!==$('#q')){ e.preventDefault(); $('#q').focus(); $('#q').select(); } if(e.key==='Escape' && document.activeElement!==$('#q')) closeDrawer(); });

/* ---------------- index polling ---------------- */
async function pollIndex(){
  try{ const ix=await api('/api/index'); const was=S.index&&S.index.state; S.index=ix;
    $('#idx').innerHTML = ix.state==='done' ? `<span class="ok"></span>index xong ${ix.took}s · ${ix.machines.length} machine · ${ix.products.length} product · ${ix.xref_count} liên kết`
      : `<span class="pulse"></span>đang index… ${ix.done}/${ix.total}`;
    if(ix.state==='done'){ if(was!=='done'){ if(!S.current) renderPlatforms(); else if(S.current.index_state!=='done'){ S.repoCache.clear(); showRepo(S.current.repo.path,true); } } return; }
  }catch(e){ $('#idx').textContent='mất kết nối server'; }
  setTimeout(pollIndex,1200);
}

/* ---------------- boot ---------------- */
(async function boot(){
  try{ S.ws=await api('/api/workspace'); }catch(e){ $('#main').innerHTML=`<div class="wrap"><div class="note">Không tải được workspace: ${esc(e.message)}</div></div>`; return; }
  $('#rootp').textContent=S.ws.root; document.title='modmap · '+bn(S.ws.root);
  S.root=buildTree(S.ws.repos); renderChips(); refreshTree(); route(); pollIndex();
})();
</script>
</body>
</html>
'''

if __name__ == '__main__':
    main()

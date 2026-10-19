"""Kien truc tong (block view): tu nhan dien component tren moi repo - app/service, thu vien, HAL, kernel/driver,
board/BSP, build/tooling - roi gom thanh khoi theo tang va noi phu thuoc giua cac khoi.

Phan tich tinh, chi doc file build / service: CMake, Meson, Autotools, Make/Kbuild, Android.bp/.mk, QNX, Bazel,
Go, Cargo, npm, Gradle/Maven, Python, Yocto, Docker/Compose, systemd, init .rc / init.d, CI, Terraform, Ansible, Helm.

Ket qua: {'layers': [{id, label}],
          'boxes': [{id, repo, layer, kind, label, total, comps: [{name, type, f, n, note?, svc?, deps?}]}],
          'edges': [[box_tu, box_den, so_lien_ket, 'use'|'build']], 'took', 'repos', 'comps'}
"""
import json
import os
import re
import sys
import time
import traceback
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor

from .fileview import resolve_ref
from .parsers import bp_modules, logical_lines, mk_modules, parse_vars, src_uri_refs, vars_dict
from .parsers_devops import yaml_children
from .parsers_py import read_toml, requirement_names
from .util import bb_name, read_text
from .workspace import find_layers

# tang tu tren xuong duoi
LAYERS = [
    ('app', 'Applications & services'),
    ('lib', 'Libraries & frameworks'),
    ('hal', 'Platform / HAL / user-space drivers'),
    ('ext', 'System & third-party libraries'),
    ('kernel', 'Kernel & drivers'),
    ('board', 'Board / BSP / boot'),
    ('build', 'Build, CI & tooling'),
]
ECO_LABEL = {'c': 'C/C++ system libraries', 'android': 'Android libraries (outside workspace)',
             'py': 'Python packages', 'npm': 'npm packages', 'cargo': 'Rust crates', 'go': 'Go modules',
             'java': 'Java / Maven libraries'}

MAX_BUILD_FILES = 2000   # so file build toi da doc moi loai, moi repo
MAX_COMPS = 80           # so component toi da gui ve moi khoi
MAX_EXT = 60             # so thu vien ngoai toi da moi khoi "ext"
BIG_LAYER = 150          # layer Yocto nhieu recipe hon: chi hien 1 component tong (poky, meta-oe...)

SKIP_SEG = {'test', 'tests', 'testing', 'unittest', 'unittests', 'testdata', 'example', 'examples', 'sample',
            'samples', 'benchmark', 'benchmarks', 'demo', 'demos', 'fuzz', 'fuzzer', 'fuzzers', 'doc', 'docs',
            'node_modules', 'vendor_modules'}
HAL_SEG = {'hal', 'hals', 'hardware', 'hw', 'bsp', 'backend', 'backends', 'adapter', 'adapters', 'osal', 'porting',
           'driver', 'drivers'}
HAL_NAME = re.compile(r'(^|[_\-.])(hal|hw|bsp|osal|drv|driver)s?($|[_\-.@])|^android\.hardware\.|^vendor\..*\.hardware\.',
                      re.I)
TEST_NAME = re.compile(r'(^|[_\-.])(unit)?tests?($|[_\-.])|[a-z]Tests?$|(^|[_\-.])(bench|benchmark|fuzz|fuzzer|example|demo)'
                       r'($|[_\-.])')
TOOL_SEG = {'prebuilts', 'prebuilt', 'toolchain', 'toolchains', 'tools', 'tool', 'build', 'sdk', 'scripts', 'docs',
            'doc', 'manifest', 'manifests', 'ci'}
C_RUNTIME = {'c', 'm', 'dl', 'rt', 'pthread', 'stdc++', 'c++', 'gcc', 'gcc_s', 'atomic', 'threads', 'util'}
SHELLS = {'sh', 'bash', 'env', 'python', 'python3', 'node', 'java', 'busybox', 'start-stop-daemon'}
QNX_DRV = re.compile(r'^(dev[a-z]*|io|spi|i2c|can|usb|deva|devb|devc|devf|devg|devh|devi|devn|devnp|devu)-')
TYPE_ORDER = {'svc': 0, 'exe': 1, 'container': 1, 'iface': 2, 'driver': 3, 'kmod': 3, 'kernel': 3, 'lib': 4}

# file build -> nhom
BUCKET = {'CMakeLists.txt': 'cmake', 'meson.build': 'meson', 'Makefile.am': 'am', 'Android.bp': 'bp',
          'Android.mk': 'amk', 'common.mk': 'qnx', 'BUILD': 'bazel', 'BUILD.bazel': 'bazel', 'go.mod': 'go',
          'Cargo.toml': 'cargo', 'package.json': 'npm', 'build.gradle': 'gradle', 'build.gradle.kts': 'gradle',
          'pom.xml': 'pom', 'pyproject.toml': 'py', 'setup.py': 'py', 'setup.cfg': 'py', 'Makefile': 'make',
          'makefile': 'make', 'GNUmakefile': 'make', 'Kbuild': 'make', 'Chart.yaml': 'helm'}


def _ln(text, pos):
    return text.count('\n', 0, pos) + 1


def _dir(rel):
    return rel.rsplit('/', 1)[0] if '/' in rel else ''


def _bucket(f):
    n = f.rsplit('/', 1)[-1]
    if n in BUCKET:
        return BUCKET[n]
    p = '/' + f
    if n.endswith('.service'):
        return 'svc'
    if n.endswith('.rc'):
        return 'rc'
    if '/init.d/' in p:
        return 'initd'
    if re.match(r'(docker-)?compose[\w.\-]*\.ya?ml$', n):
        return 'compose'
    if n == 'Dockerfile' or n.startswith('Dockerfile.') or n.endswith('.dockerfile'):
        return 'docker'
    if n.endswith('.tf'):
        return 'tf'
    if n.endswith(('.dts', '.dtso')):
        return 'dts'
    if n.endswith('.build') and n != 'meson.build':
        return 'qnxbuild'
    if n.startswith('.gitlab-ci') or n.startswith('Jenkinsfile') or ('/.github/workflows/' in p and n.endswith(('.yml', '.yaml'))) \
            or n in ('azure-pipelines.yml', 'bitbucket-pipelines.yml', '.drone.yml', '.travis.yml'):
        return 'ci'
    if '/roles/' in p and '/tasks/' in p:
        return 'ansible'
    if n.startswith('requirements') and n.endswith('.txt'):
        return 'req'
    return None


def _skipped(rel):
    return any(s.lower() in SKIP_SEG for s in rel.split('/')[:-1])


def _lib(tok):
    """Token trong danh sach link (-lfoo, libfoo.la, Foo::Bar, ../x/libbar.a) -> ten thu vien, hoac None."""
    tok = tok.strip('"\'')
    if not tok or tok in CM_KW or '$' in tok or '<' in tok or '(' in tok:
        return None
    if tok.startswith('-l'):
        return tok[2:] or None
    if tok.startswith('-'):
        return None
    if '::' in tok:
        return tok.split('::')[0]
    tok = tok.rsplit('/', 1)[-1]
    return re.sub(r'\.(so(\.[\d.]+)?|a|la|lib|dylib|o)$', '', tok) or None


class _Repo:
    """Ket qua quet 1 repo: component, lien ket CMake (target -> thu vien), service (systemd / init)."""

    def __init__(self, ws, p):
        self.ws, self.p, self.repo = ws, p, p['path']
        self.comps, self.svcs, self.links = [], [], defaultdict(list)

    def read(self, rel):
        return read_text(self.ws.abs(self.repo, rel)) or ''

    def add(self, name, ctype, layer, f='', n=0, deps=(), eco='c', builds=(), note=''):
        self.comps.append({'name': name, 'type': ctype, 'layer': layer, 'f': f, 'n': n, 'deps': list(deps),
                           'eco': eco, 'builds': list(builds), 'note': note, 'repo': self.repo})


# ---------------------------------------------------------------- C / C++ build systems

CM_TARGET = re.compile(r'\b(add_executable|add_library)\s*\(\s*([^\s)]+)([^)]*)\)', re.I)
CM_LINK = re.compile(r'\btarget_link_libraries\s*\(\s*([^\s)]+)([^)]*)\)', re.I)
CM_PROJECT = re.compile(r'\bproject\s*\(\s*([\w.\-]+)', re.I)
CM_PKG = re.compile(r'\bpkg_(?:check|search)_modules?\s*\(\s*(\w+)([^)]*)\)', re.I)
CM_KW = {'PUBLIC', 'PRIVATE', 'INTERFACE', 'LINK_PUBLIC', 'LINK_PRIVATE', 'LINK_INTERFACE_LIBRARIES', 'debug',
         'optimized', 'general', 'REQUIRED', 'QUIET', 'IMPORTED_TARGET', 'GLOBAL', 'NO_CMAKE_PATH',
         'NO_CMAKE_ENVIRONMENT_PATH'}


def _cmake(R, rel, text):
    text = re.sub(r'#[^\n]*', '', text)
    m = CM_PROJECT.search(text)
    proj = m.group(1) if m else ''

    def sub(s):
        return s.replace('${PROJECT_NAME}', proj).replace('${CMAKE_PROJECT_NAME}', proj).strip('"')

    pkgs = {m.group(1): [re.split(r'[<>=]', x)[0] for x in m.group(2).split() if x not in CM_KW]
            for m in CM_PKG.finditer(text)}
    for m in CM_LINK.finditer(text):
        for tok in m.group(2).split():
            tok = sub(tok)
            v = re.match(r'\$\{(\w+?)_(?:LINK_)?(?:LIBRARIES|LDFLAGS|LIBS)\}$', tok)
            if v:
                R.links[sub(m.group(1))] += pkgs.get(v.group(1), [])
            elif tok.startswith('PkgConfig::'):
                R.links[sub(m.group(1))] += pkgs.get(tok[11:], [tok[11:]])
            elif _lib(tok):
                R.links[sub(m.group(1))].append(_lib(tok))
    for m in CM_TARGET.finditer(text):
        name, opts = sub(m.group(2)), set(m.group(3).split())
        if '$' in name or opts & {'IMPORTED', 'ALIAS', 'OBJECT'} or TEST_NAME.search(name):
            continue
        exe = m.group(1).lower() == 'add_executable'
        R.add(name, 'exe' if exe else 'lib', 'app' if exe else 'lib', rel, _ln(text, m.start()))


MS_TARGET = re.compile(r"\b(executable|library|shared_library|static_library|both_libraries|shared_module)\s*\(\s*'([^']+)'")
MS_DEP = re.compile(r"\bdependency\s*\(\s*'([^']+)'")


def _meson(R, rel, text):
    text = re.sub(r'#[^\n]*', '', text)
    deps = ['pthread' if d == 'threads' else d for d in MS_DEP.findall(text)]
    for m in MS_TARGET.finditer(text):
        if TEST_NAME.search(m.group(2)):
            continue
        exe = m.group(1) == 'executable'
        R.add(m.group(2), 'exe' if exe else 'lib', 'app' if exe else 'lib', rel, _ln(text, m.start()), deps)


def _automake(R, rel, text):
    adds, prim = defaultdict(list), []
    for ln, line in logical_lines(text):
        m = re.match(r'\s*(\w+?)_(PROGRAMS|LTLIBRARIES|LIBRARIES)\s*\+?=\s*(.*)', line)
        if m:
            if m.group(1) in ('check', 'EXTRA') or (m.group(1) == 'noinst' and m.group(2) == 'PROGRAMS'):
                continue
            prim += [(x, m.group(2), ln) for x in m.group(3).split() if '$' not in x and not x.startswith('#')]
            continue
        m = re.match(r'\s*(\w+)_(LDADD|LIBADD)\s*\+?=\s*(.*)', line)
        if m:
            adds[m.group(1)] += m.group(3).split()
    for name, kind, ln in prim:
        deps = [x for x in (_lib(t) for t in adds.get(re.sub(r'[^\w@]', '_', name), [])) if x]
        exe = kind == 'PROGRAMS'
        R.add(re.sub(r'\.(la|a)$', '', name), 'exe' if exe else 'lib', 'app' if exe else 'lib', rel, ln, deps)


MK_OBJM = re.compile(r'^[ \t]*obj-(?:m|\$\(CONFIG_\w+\))[ \t]*[:+]?=[ \t]*(.+)$', re.M)
APP_VARS = {'TARGET', 'PROG', 'PROGRAM', 'PROGRAMS', 'BIN', 'BINARY', 'BINS', 'EXE', 'EXEC', 'APP', 'APPNAME',
            'APP_NAME', 'TARGET_BIN', 'EXECUTABLE'}
LIB_VARS = {'LIB', 'LIBNAME', 'LIB_NAME', 'LIBRARY', 'TARGET_LIB', 'SHARED_LIB', 'STATIC_LIB', 'SONAME', 'LIBTARGET'}
LD_VARS = {'LDLIBS', 'LIBS', 'LDFLAGS', 'LDADD', 'LDLIBS_EXTRA', 'EXTRA_LIBS', 'LIBRARIES'}


def _make(R, rel, text, kmod_only):
    """Makefile thuong / Kbuild: kernel module ngoai cay (obj-m) va bien TARGET/PROG/LIB... (doan, nen chi dung
    khi repo khong co he build co cau truc)."""
    mods = [(tok[:-2], _ln(text, m.start())) for m in MK_OBJM.finditer(text) for tok in m.group(1).split()
            if tok.endswith('.o')]
    for name, ln in mods:
        R.add(name, 'kmod', 'kernel', rel, ln)
    if mods or kmod_only:
        return
    vs = {}
    for ln, k, op, v in parse_vars(text, 'make'):
        old = vs.get(k)
        vs[k] = (old[0], old[1] + ' ' + v) if old and op in ('+=', '.=') else (ln, v)
    deps = [x[2:] for k in LD_VARS if k in vs for x in vs[k][1].split() if x.startswith('-l') and '$' not in x]
    for k in sorted(APP_VARS | LIB_VARS):
        if k not in vs:
            continue
        for tok in vs[k][1].split():
            if '$' in tok or tok.startswith('-') or '=' in tok:
                continue
            base = tok.rsplit('/', 1)[-1]
            lib = k in LIB_VARS or re.search(r'\.(so|a)(\.|$)', base)
            name = re.sub(r'\.(so[\d.]*|a|elf|bin|exe|out|o)$', '', base)
            if name and not TEST_NAME.search(name):
                R.add(name, 'lib' if lib else 'exe', 'lib' if lib else 'app', rel, vs[k][0], deps)


def _qnx(R, rel, text, files):
    d = _dir(rel)
    v = {k: val for _, k, _, val in parse_vars(text, 'make')}
    name = (v.get('NAME') or '').strip()
    if not name or '$' in name:
        name = (d or R.repo).rsplit('/', 1)[-1]
    pre = d + '/' if d else ''
    rx = re.compile(re.escape(pre) + r'nto/[^/]+/([^/]+)/Makefile$')
    variants = {m.group(1).split('.')[0] for m in (rx.match(f) for f in files) if m}
    inst = v.get('INSTALLDIR', '')
    lib = bool(variants & {'so', 'dll', 'a'}) and 'o' not in variants or (not variants and '/lib' in '/' + inst)
    deps = [x for x in (v.get('LIBS') or '').split() if '$' not in x]
    segs = {s.lower() for s in (R.repo + '/' + d).split('/')}
    if QNX_DRV.match(name) or 'dll' in variants or segs & {'drivers', 'driver'}:
        R.add(name, 'driver', 'kernel', rel, 1, deps, note=', '.join(sorted(variants)))
    else:
        R.add(name, 'lib' if lib else 'exe', 'lib' if lib else 'app', rel, 1, deps, note=', '.join(sorted(variants)))


# ---------------------------------------------------------------- Android

def _android_kind(t, host):
    """kieu module Soong/Make -> (type, layer) hoac None neu bo qua (test, defaults, filegroup...)."""
    t = t.lower()
    if re.search(r'test|bench|fuzz|defaults|filegroup|genrule|prebuilt|headers|phony|license|soong_config|'
                 r'apex_key|certificate|_import$|bootstrap_go|sdk_library_import', t):
        return None
    if t.endswith('_interface'):
        return 'iface', 'hal'
    if 'kernel_module' in t or t.startswith('ddk_'):
        return 'kmod', 'kernel'
    host = host or t.endswith('_host') or '_host_' in t
    if t.endswith(('_binary', '_app', 'executable', 'package')) or t in ('android_app', 'build_package'):
        return 'exe', 'build' if host else 'app'
    if 'library' in t:
        return 'lib', 'build' if host else 'lib'
    return None


def _android_bp(R, rel, text):
    for m in bp_modules(text):
        k = _android_kind(m['type'], False)
        if k and m['name'] != '?' and not TEST_NAME.search(m['name']):
            deps = [d for d in m['deps'] if not re.search(r'[-_]?defaults$', d)]
            R.add(m['name'], k[0], k[1], rel, m['line'], deps, eco='android')


def _android_mk(R, rel, text):
    for m in mk_modules(text):
        k = _android_kind(m['type'].replace('BUILD_', ''), 'HOST' in m['type'])
        if k and '$' not in m['name'] and not TEST_NAME.search(m['name']):
            R.add(m['name'], k[0], k[1], rel, m['line'], m['deps'], eco='android')


# ---------------------------------------------------------------- Bazel, Go, Rust, JS, Java

BZ_RULE = re.compile(r'^(\w+)\s*\(\s*name\s*=\s*"([^"]+)"', re.M)


def _bazel(R, rel, text):
    for m in BZ_RULE.finditer(text):
        t = m.group(1)
        if 'test' in t or not t.endswith(('_binary', '_library')) or TEST_NAME.search(m.group(2)):
            continue
        end = text.find('\n)', m.end())
        dm = re.search(r'\bdeps\s*=\s*\[([^\]]*)\]', text[m.end():end if end > 0 else None])
        deps = []
        for x in re.findall(r'"([^"]+)"', dm.group(1) if dm else ''):
            deps.append(x.split('//')[0][1:] if x.startswith('@') else x.split(':')[-1].rsplit('/', 1)[-1])
        eco = {'py': 'py', 'go': 'go', 'java': 'java', 'kt': 'java', 'rust': 'cargo'}.get(t.split('_')[0], 'c')
        exe = t.endswith('_binary')
        R.add(m.group(2), 'exe' if exe else 'lib', 'app' if exe else 'lib', rel, _ln(text, m.start()), deps, eco)


def _go(R, gomod, files):
    d = _dir(gomod)
    pre = d + '/' if d else ''
    text = R.read(gomod)
    m = re.search(r'^module\s+(\S+)', text, re.M)
    tail = (m.group(1) if m else (d or R.repo)).rstrip('/').rsplit('/', 1)[-1]
    gof = [f[len(pre):] for f in files if f.startswith(pre) and f.endswith('.go') and not f.endswith('_test.go')]
    apps = set()
    for f in gof:
        parts = f.split('/')
        if parts[0] == 'cmd' and len(parts) > 2:
            apps.add('cmd/' + parts[1])
        elif parts[-1] == 'main.go':
            apps.add('/'.join(parts[:-1]))
    deps = [x.split('/')[-1] for x in re.findall(r'^\s*(?:require\s+)?([\w.\-]+\.\w+/[\w.\-/]+)\s+v\S+[ \t]*$', text, re.M)]
    for a in sorted(apps):
        R.add(a.rsplit('/', 1)[-1] if a else tail, 'exe', 'app', pre + (a + '/' if a else '') + 'main.go', 0, deps, 'go')
    in_app = lambda f: any(_dir(f) == a or _dir(f).startswith(a + '/') for a in apps)  # noqa: E731
    if any(not in_app(f) for f in gof):
        R.add(tail, 'lib', 'lib', gomod, 1, deps, 'go')


def _cargo(R, rel, text, files):
    t = read_toml(text)
    name = t.get('package', {}).get('name')
    if not name:
        return
    pre = _dir(rel) + '/' if '/' in rel else ''
    deps = list(t.get('dependencies', {}))
    bins = re.findall(r'\[\[bin\]\][^\[]*?name\s*=\s*"([^"]+)"', text, re.S)
    bins += [f[len(pre) + 8:-3] for f in files if f.startswith(pre + 'src/bin/') and f.endswith('.rs') and f.count('/') == pre.count('/') + 2]
    if pre + 'src/main.rs' in files:
        bins.append(name)
    for b in dict.fromkeys(bins):
        R.add(b, 'exe', 'app', rel, 1, deps, 'cargo')
    if pre + 'src/lib.rs' in files or not bins:
        R.add(name, 'lib', 'lib', rel, 1, deps, 'cargo')


def _npm(R, rel, text):
    try:
        j = json.loads(text)
    except ValueError:
        return
    if not isinstance(j, dict):
        return
    name = j.get('name') or (_dir(rel) or R.repo).rsplit('/', 1)[-1]
    deps = list(j.get('dependencies') or {})
    b = j.get('bin')
    bins = [name.rsplit('/', 1)[-1]] if isinstance(b, str) else list(b or {})
    scripts = j.get('scripts') or {}
    for x in bins:
        R.add(x, 'exe', 'app', rel, 1, deps, 'npm')
    if not bins:
        app = not (j.get('main') or j.get('exports') or j.get('module')) and bool(set(scripts) & {'start', 'serve', 'dev'})
        if j.get('workspaces') and not deps:
            return
        R.add(name, 'exe' if app else 'lib', 'app' if app else 'lib', rel, 1, deps, 'npm')


def _gradle(R, rel, text):
    name = (_dir(rel) or R.repo).rsplit('/', 1)[-1]
    if re.search(r'com\.android\.application|[\'"]application[\'"]|\bapplication\b\s*\{|^\s*application\s*$|'
                 r'org\.springframework\.boot', text, re.M):
        kind = 'exe'
    elif re.search(r'com\.android\.library|java-library|[\'"]java[\'"]|\bjava\b\s*$|kotlin', text, re.M):
        kind = 'lib'
    else:
        return
    deps = [x.split(':')[-1] for x in re.findall(r'project\s*\(\s*(?:path\s*:\s*)?["\']:?([\w\-:]+)["\']', text)]
    R.add(name, kind, 'app' if kind == 'exe' else 'lib', rel, 1, deps, 'java')


def _pom(R, rel, text):
    body = re.sub(r'<parent>.*?</parent>|<dependencyManagement>.*?</dependencyManagement>|<build>.*?</build>', '', text, flags=re.S)
    m = re.search(r'<artifactId>\s*([^<\s]+)', re.sub(r'<dependencies>.*?</dependencies>', '', body, flags=re.S))
    pk = re.search(r'<packaging>\s*(\w+)', body)
    if not m or (pk and pk.group(1) == 'pom'):
        return
    app = (pk and pk.group(1) == 'war') or 'spring-boot-maven-plugin' in text
    dm = re.search(r'<dependencies>(.*?)</dependencies>', body, re.S)
    deps = re.findall(r'<artifactId>\s*([^<\s]+)', dm.group(1)) if dm else []
    R.add(m.group(1), 'exe' if app else 'lib', 'app' if app else 'lib', rel, 1, deps, 'java')


# ---------------------------------------------------------------- Python

def _python(R, projs, reqs, files):
    found = False
    for rel in projs:
        text, n = R.read(rel), rel.rsplit('/', 1)[-1]
        name, scripts, deps = '', [], []
        if n == 'pyproject.toml':
            t = read_toml(text)
            proj, poetry = t.get('project', {}), t.get('tool.poetry', {})
            name = proj.get('name') or poetry.get('name') or ''
            scripts = list(t.get('project.scripts', {})) + list(t.get('tool.poetry.scripts', {}))
            deps = list(proj.get('dependencies') or []) + [k for k in t.get('tool.poetry.dependencies', {}) if k != 'python']
        elif n == 'setup.py':
            m = re.search(r'\bname\s*=\s*["\']([^"\']+)', text)
            name = m.group(1) if m else ''
            cs = re.search(r'console_scripts[\'"]?\s*[:=]\s*\[([^\]]*)\]', text, re.S)
            scripts = re.findall(r'["\']\s*([\w.\-]+)\s*=', cs.group(1)) if cs else []
            im = re.search(r'install_requires\s*=\s*\[([^\]]*)\]', text, re.S)
            deps = re.findall(r'["\']([^"\']+)["\']', im.group(1)) if im else []
        elif n == 'setup.cfg':
            m = re.search(r'^name\s*=\s*(\S+)', text, re.M)
            name = m.group(1) if m else ''
            cs = re.search(r'^console_scripts\s*=\s*\n((?:[ \t]+\S.*\n?)+)', text, re.M)
            scripts = re.findall(r'^\s*([\w.\-]+)\s*=', cs.group(1), re.M) if cs else []
        if not name and not scripts:
            continue
        found = True
        deps = [re.split(r'[<>=!~;\[ (]', d.strip())[0] for d in deps if d.strip()]
        for s in scripts:
            R.add(s, 'exe', 'app', rel, 1, deps, 'py')
        if name and not scripts:
            R.add(name, 'lib', 'lib', rel, 1, deps, 'py')
    if found:
        return
    deps = [x for r in reqs[:5] for x in requirement_names(R.read(r))]
    tail = R.repo.rsplit('/', 1)[-1]
    mains = [f for f in files if f.count('/') <= 1 and f.rsplit('/', 1)[-1] in
             ('manage.py', 'app.py', 'main.py', '__main__.py', 'wsgi.py', 'asgi.py', 'server.py')]
    if mains:
        R.add(tail, 'exe', 'app', mains[0], 1, deps, 'py')
    elif any(f.endswith('.py') for f in files):
        R.add(tail, 'lib', 'lib', '', 0, deps, 'py')


# ---------------------------------------------------------------- service, container, DevOps

def _systemd(R, rel, text):
    m = re.search(r'^\s*ExecStart\s*=\s*[-@+!:]*(\S+)', text, re.M)
    unit = re.sub(r'@?\.service$', '', rel.rsplit('/', 1)[-1])
    exe = m.group(1).rsplit('/', 1)[-1] if m else ''
    R.svcs.append((unit, unit if not exe or exe in SHELLS else exe, rel, _ln(text, m.start()) if m else 1))


def _init_rc(R, rel, text):
    for m in re.finditer(r'^\s*service\s+(\S+)\s+(\S+)', text, re.M):
        exe = m.group(2).rsplit('/', 1)[-1]
        R.svcs.append((m.group(1), m.group(1) if exe in SHELLS else exe, rel, _ln(text, m.start())))


def _compose(R, rel, text):
    lines, svcs = text.split('\n'), yaml_children(text, 'services')
    for i, (name, ln) in enumerate(svcs):
        block = '\n'.join(lines[ln:svcs[i + 1][1] - 1 if i + 1 < len(svcs) else len(lines)])
        deps = []
        m = re.search(r'^([ \t]*)depends_on[ \t]*:[ \t]*(\[[^\]]*\])?[ \t]*$', block, re.M)
        if m and m.group(2):
            deps = [x.strip(' "\'') for x in m.group(2)[1:-1].split(',') if x.strip()]
        elif m:
            ind, child = len(m.group(1)), None
            for line in block[m.end():].split('\n')[1:]:
                if not line.strip() or line.lstrip().startswith('#'):
                    continue
                cur = len(line) - len(line.lstrip())
                if cur <= ind:
                    break
                child = cur if child is None else child
                mm = re.match(r'[ \t]*(?:-[ \t]*)?["\']?([\w.\-]+)', line)
                if cur == child and mm:
                    deps.append(mm.group(1))
        img = re.search(r'^[ \t]*image[ \t]*:[ \t]*["\']?([^"\'\s#]+)', block, re.M)
        note = img.group(1) if img and not re.search(r'^[ \t]*build[ \t]*:', block, re.M) else ''
        R.add(name, 'container', 'app', rel, ln, deps, 'docker', note=note)


def _devops(R, b):
    if b['ci']:
        kinds = []
        for f in b['ci']:
            n = f.rsplit('/', 1)[-1]
            kinds.append('GitLab CI' if n.startswith('.gitlab-ci') else 'Jenkins' if n.startswith('Jenkinsfile')
                         else 'GitHub Actions' if '.github/' in f else 'CI')
        for k, c in Counter(kinds).items():
            R.add(k, 'ci', 'build', next(f for f, x in zip(b['ci'], kinds) if x == k), note='%d file' % c)
    if b['docker']:
        R.add('Docker image' if len(b['docker']) == 1 else 'Docker images', 'ci', 'build', b['docker'][0],
              note='%d Dockerfile' % len(b['docker']))
    if b['tf']:
        R.add('Terraform', 'infra', 'build', b['tf'][0], note='%d .tf' % len(b['tf']))
    if b['ansible']:
        roles = {f.split('/roles/', 1)[-1].split('/')[0] for f in ['/' + x for x in b['ansible']]}
        R.add('Ansible', 'infra', 'build', b['ansible'][0], note='%d roles' % len(roles))
    for f in b['helm']:
        m = re.search(r'^name\s*:\s*["\']?([\w.\-]+)', R.read(f), re.M)
        R.add('Helm: ' + (m.group(1) if m else _dir(f) or 'chart'), 'infra', 'build', f)


# ---------------------------------------------------------------- Yocto, kernel, board

def _yocto(R, files):
    d = R.ws.abs(R.repo)
    for L in find_layers(d)[:40]:
        pre = '' if L == '.' else L + '/'
        lfiles = [f for f in files if f.startswith(pre)]
        conf = vars_dict(os.path.join(d, L, 'conf', 'layer.conf'))
        coll = (conf.get('BBFILE_COLLECTIONS') or '').split()
        lname = coll[0] if coll else os.path.basename(os.path.normpath(os.path.join(d, L)))
        recipes = [f for f in lfiles if f.endswith('.bb')]
        builds, small = set(), len(recipes) <= BIG_LAYER
        for f in recipes if small else []:
            pn = bb_name(f)[0]
            refs = src_uri_refs(parse_vars(R.read(f), 'bb'))
            targets = {to['repo'] for to in (resolve_ref(R.ws, R.repo, f, 'src', r['raw']) for r in refs) if to}
            if targets:                                      # source nam trong workspace: repo do tu hien
                builds |= targets
                continue
            cat = next((s for s in f.split('/') if s.startswith('recipes-')), '')
            if 'packagegroup' in pn:
                continue
            if cat == 'recipes-kernel' or pn.startswith(('linux-', 'kernel-module')):
                layer = 'kernel'
            elif cat == 'recipes-bsp' or 'u-boot' in pn or 'firmware' in pn:
                layer = 'board'
            elif '-image' in pn or '/images/' in f:
                layer = 'build'
            elif pn.startswith('lib') or pn.endswith(('-lib', '-libs')):
                layer = 'lib'
            else:
                layer = 'app'
            R.add(pn, 'recipe', layer, f, 1, eco='yocto')
        R.add(lname, 'layer', 'build', pre + 'conf/layer.conf', 1, builds=sorted(builds),
              note='%d recipes' % len(recipes))
        for mf in lfiles:
            if re.search(r'(^|/)conf/machine/[^/]+\.conf$', mf):
                R.add(mf.rsplit('/', 1)[-1][:-5], 'machine', 'board', mf, 1, note='machine')


def _kernel(R):
    v = vars_dict(R.ws.abs(R.repo, 'Makefile'))
    ver = '.'.join(x for x in (v.get('VERSION'), v.get('PATCHLEVEL'), v.get('SUBLEVEL')) if x)
    if 'u-boot' in R.p['tags']:
        R.add('U-Boot', 'boot', 'board', 'Makefile', 1, note=ver)
    else:
        R.add('Linux kernel', 'kernel', 'kernel', 'Makefile', 1, note=ver)


def _board(R, b, files):
    tags = set(R.p['tags'])
    if 'android-device' in tags:
        cfg = next((f for f in files if re.match(r'(.*/)?BoardConfig[\w.\-]*\.mk$', f)), '') or \
            next((f for f in files if f.endswith('AndroidProducts.mk')), '')
        plat = vars_dict(R.ws.abs(R.repo, cfg)).get('TARGET_BOARD_PLATFORM', '') if cfg else ''
        R.add(R.repo.rsplit('/', 1)[-1], 'board', 'board', cfg, 1, note=plat)
    if b['dts']:
        R.add('Device tree', 'dt', 'board', b['dts'][0], 1, note='%d .dts' % len(b['dts']))
    if 'qnx' in tags:
        for f in b['qnxbuild'][:10]:
            R.add('IFS ' + f.rsplit('/', 1)[-1][:-6], 'image', 'board', f, 1, note='QNX buildfile')


def _fallback(R, files):
    """Repo khong rut duoc target nao: doan theo duong dan va loai file nguon."""
    segs = {s.lower() for s in R.repo.split('/')}
    tail = R.repo.rsplit('/', 1)[-1]
    src = [f for f in files if f.endswith(('.c', '.cc', '.cpp', '.cxx', '.rs', '.java', '.kt', '.go'))]
    mains = [f for f in src if re.search(r'(^|/|_)main\.(c|cc|cpp|cxx|rs|go)$', f)]
    if segs & TOOL_SEG:
        R.add(tail, 'repo', 'build', '', 0)
    elif segs & {'firmware', 'bootloader', 'boot', 'device', 'devices', 'board', 'boards', 'bsp'} and not mains:
        R.add(tail, 'boot', 'board', '', 0)
    elif mains:
        R.add(tail, 'exe', 'app', mains[0], 1)
    elif src:
        R.add(tail, 'lib', 'lib', '', 0)
    elif not R.comps:
        R.add(tail, 'repo', 'build', '', 0)


# ---------------------------------------------------------------- 1 repo

def scan_repo(ws, p, refresh=False):
    R = _Repo(ws, p)
    tags = set(p['tags'])
    if p['kind'] == 'missing':
        return R
    if tags & {'kernel', 'u-boot'}:
        _kernel(R)
        return R
    files = ws.repo_files(p['path'], refresh=refresh)[0]
    if 'bitbake' in tags:
        R.add('BitBake', 'tool', 'build', 'bin/bitbake' if 'bin/bitbake' in files else '', 0)
    if tags & {'yocto-layer', 'yocto-multi'}:
        _yocto(R, files)
        return R
    if 'bitbake' in tags:
        return R

    b = defaultdict(list)
    for f in sorted(files, key=lambda f: (f.count('/'), f)):        # file nong truoc: cat bot thi mat file sau
        k = _bucket(f)
        if k and not _skipped(f) and len(b[k]) < MAX_BUILD_FILES:
            b[k].append(f)

    for k, fn in (('cmake', _cmake), ('meson', _meson), ('am', _automake), ('bp', _android_bp),
                  ('amk', _android_mk), ('bazel', _bazel), ('npm', _npm), ('gradle', _gradle), ('pom', _pom)):
        for f in b[k]:
            fn(R, f, R.read(f))
    if 'qnx' in tags:
        for f in b['qnx']:
            _qnx(R, f, R.read(f), files)
    for f in b['go'][:20]:
        _go(R, f, files)
    for f in b['cargo'][:50]:
        _cargo(R, f, R.read(f), files)
    for f in b['make'][:400]:
        _make(R, f, R.read(f), kmod_only=bool(R.comps) or 'qnx' in tags)
    if b['py'] and ('python' in tags or not R.comps):
        _python(R, b['py'][:20], b['req'], files)
    elif 'python' in tags and not R.comps:
        _python(R, [], b['req'], files)
    for c in R.comps:
        if c['name'] in R.links:
            c['deps'] += R.links[c['name']]

    for f in b['svc']:
        _systemd(R, f, R.read(f))
    for f in b['rc']:
        _init_rc(R, f, R.read(f))
    for f in b['initd']:
        n = re.sub(r'^[SK]\d\d', '', f.rsplit('/', 1)[-1])
        R.svcs.append((n, n, f, 1))
    for f in b['compose']:
        _compose(R, f, R.read(f))
    _board(R, b, files)
    _devops(R, b)
    if not any(c['layer'] != 'build' for c in R.comps):
        _fallback(R, files)
    return R


# ---------------------------------------------------------------- toan workspace

def _refine(c):
    """Doi tang theo duong dan / ten: thu vien, process nam duoi hal/, hardware/, drivers/... -> tang HAL."""
    if c['layer'] not in ('app', 'lib'):
        return
    segs = {s.lower() for s in (c['repo'] + '/' + c['f']).split('/')[:-1]}
    if segs & HAL_SEG or HAL_NAME.search(c['name']):
        c['layer'] = 'hal'


def _keys(name):
    n = name.lower()
    n = re.sub(r'\.(so|a|la)$', '', n)
    base = n[3:] if n.startswith('lib') and len(n) > 3 else n
    return {n, base, 'lib' + base}


def _ext_name(eco, d):
    if eco in ('c', 'android'):
        base = d[3:] if d.startswith('lib') and len(d) > 3 else d
        if base.lower() in C_RUNTIME:
            return 'C/C++ runtime (libc, libm, pthread...)'
        return 'lib' + base
    return d


def _scan_safe(ws, p, refresh, errors):
    """1 repo loi (file la, parser vap truong hop chua gap) khong duoc lam hong ca block view:
    bo qua repo do, in traceback ra terminal, bao so repo loi ve giao dien."""
    try:
        return scan_repo(ws, p, refresh)
    except Exception as e:  # noqa: BLE001
        print('  ! block view: %s: %s' % (p['path'], traceback.format_exc()), file=sys.stderr)
        errors.append({'repo': p['path'], 'error': '%s: %s' % (type(e).__name__, e)})
        return _Repo(ws, p)


def build_arch(ws, refresh=False):
    """refresh=True: doc lai danh sach file cua moi repo (nut Rescan)."""
    t0 = time.time()
    errors = []
    with ThreadPoolExecutor(max(1, ws.workers)) as ex:
        results = list(ex.map(lambda p: _scan_safe(ws, p, refresh, errors), ws.repos))

    comps, svcs = [], []
    for R in results:
        seen = set()
        for c in R.comps:
            key = (c['name'], c['layer'], c['type'])
            if key not in seen:
                seen.add(key)
                comps.append(c)
        svcs += [(R.repo,) + s for s in R.svcs]
    for c in comps:
        _refine(c)

    # service (systemd / init) -> danh dau binary tuong ung la service, khong thi tao component rieng
    exes = defaultdict(list)
    for c in comps:
        if c['type'] in ('exe', 'container'):
            exes[c['name'].lower()].append(c)
    for repo, unit, exe, rel, ln in svcs:
        cands = exes.get(exe.lower()) or exes.get(unit.lower()) or []
        cands = [c for c in cands if c['repo'] == repo] or cands
        if cands:
            for c in cands[:1]:
                c['svc'] = True
                c.setdefault('units', []).append(unit)
        elif unit.lower() not in {c['name'].lower() for c in comps if c['repo'] == repo and c['type'] == 'svc'}:
            c = {'name': unit, 'type': 'svc', 'layer': 'app', 'f': rel, 'n': ln, 'deps': [], 'eco': 'c',
                 'builds': [], 'note': exe if exe != unit else '', 'repo': repo, 'svc': True}
            _refine(c)
            comps.append(c)

    # chi muc ten -> component (de noi phu thuoc)
    idx = defaultdict(list)
    for c in comps:
        if c['type'] not in ('svc', 'layer', 'ci', 'infra', 'machine', 'repo'):
            for k in _keys(c['name']):
                idx[k].append(c)

    bid = lambda c: c['repo'] + '#' + c['layer']  # noqa: E731
    edges, ext = Counter(), defaultdict(Counter)
    for c in comps:
        for d in dict.fromkeys(c['deps']):
            cands = []
            for k in _keys(d):
                cands += [x for x in idx.get(k, []) if x is not c]
            if cands:
                t = ([x for x in cands if x['repo'] == c['repo']] or sorted(cands, key=lambda x: x['repo']))[0]
                c.setdefault('uses', []).append(t['name'])
                if bid(t) != bid(c):
                    edges[(bid(c), bid(t), 'use')] += 1
            elif c['eco'] in ECO_LABEL:
                name = _ext_name(c['eco'], d)
                eco = 'c' if c['eco'] == 'android' and name.startswith('C/C++') else c['eco']
                ext[eco][name] += 1
                edges[(bid(c), 'ext#' + eco, 'use')] += 1
                c.setdefault('uses', []).append(name)
        for repo in c['builds']:
            for o in comps:
                if o['repo'] == repo and bid(o) != bid(c):
                    edges[(bid(c), bid(o), 'build')] += 1

    boxes = {}
    kind_of = {p['path']: p['kind'] for p in ws.repos}
    for c in comps:
        b = boxes.setdefault(bid(c), {'id': bid(c), 'repo': c['repo'], 'layer': c['layer'],
                                      'kind': kind_of.get(c['repo'], 'other'), 'label': c['repo'].rsplit('/', 1)[-1] or c['repo'],
                                      'comps': []})
        out = {k: c[k] for k in ('name', 'type', 'f', 'n') if c[k] or k == 'name'}
        if c.get('note'):
            out['note'] = c['note']
        if c.get('svc'):
            out['svc'] = c.get('units') or [c['name']]
        if c.get('uses'):
            out['uses'] = list(dict.fromkeys(c['uses']))[:30]
        b['comps'].append(out)
    for eco, cnt in ext.items():
        boxes['ext#' + eco] = {'id': 'ext#' + eco, 'repo': '', 'layer': 'ext', 'kind': 'ext', 'label': ECO_LABEL[eco],
                               'comps': [{'name': n, 'type': 'ext', 'note': '%d' % k} for n, k in cnt.most_common()]}
    for b in boxes.values():
        if b['layer'] != 'ext':
            b['comps'].sort(key=lambda x: (TYPE_ORDER.get(x['type'], 5) - (0.5 if x.get('svc') else 0), x['name'].lower()))
        b['total'] = len(b['comps'])
        b['comps'] = b['comps'][:MAX_EXT if b['layer'] == 'ext' else MAX_COMPS]

    order = {k: i for i, (k, _) in enumerate(LAYERS)}
    return {'layers': [{'id': k, 'label': v} for k, v in LAYERS],
            'boxes': sorted(boxes.values(), key=lambda b: (order[b['layer']], b['repo'], b['label'])),
            'edges': [[a, b, n, k] for (a, b, k), n in sorted(edges.items())],
            'repos': len(ws.repos), 'comps': len(comps), 'took': round(time.time() - t0, 2),
            'errors': sorted(errors, key=lambda e: e['repo'])}

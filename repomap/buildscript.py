"""Build script cho 1 module (repo): sinh / doc / luu  <repo>/repomap_build_script.sh

Script la ban nhap doan tu thong tin da doc duoc (khong chay gi): Android (envsetup + lunch + mmm), Yocto
(oe-init-build-env + bitbake, ca recipe co SRC_URI tro vao repo nay), kernel / U-Boot, QNX, CMake, Meson, Bazel,
Autotools, Cargo, Go, Gradle/Maven, npm, Python, Make, Docker. Gia tri khong tim duoc de o dau file la TODO;
script dung lai voi thong bao ro neu chua dien. Cach build khac tim thay duoc ghi kem o cuoi (dang comment).
File duoc them vao .git/info/exclude cua repo de khong lam ban `git status`.
"""
import glob
import os
import re
import time

from . import __version__
from .parsers import parse_file, parse_vars, src_uri_refs, vars_dict
from .util import bb_name, git, listdir, read_text

SCRIPT = 'repomap_build_script.sh'
MAX_SIZE = 256 * 1024


# ---------------------------------------------------------------- doc / ghi

def script_path(ws, repo):
    return ws.abs(repo, SCRIPT)


def has_script(ws, repo):
    return os.path.isfile(script_path(ws, repo))


def read_script(ws, repo):
    text = read_text(script_path(ws, repo), MAX_SIZE)
    if text is None:
        raise ValueError('%s not found in %s' % (SCRIPT, repo or '.'))
    return {'repo': repo, 'name': SCRIPT, 'text': text}


def save_script(ws, repo, text):
    text = str(text).replace('\r\n', '\n')
    if len(text.encode('utf-8')) > MAX_SIZE:
        raise ValueError('script is too big (max %d KB)' % (MAX_SIZE // 1024))
    path = script_path(ws, repo)
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)
    try:
        os.chmod(path, 0o755)
    except OSError:
        pass
    exclude_from_git(ws.abs(repo))
    return {'repo': repo, 'name': SCRIPT, 'text': text, 'saved': time.strftime('%H:%M:%S')}


def create_script(ws, repo, overwrite=False):
    if has_script(ws, repo) and not overwrite:
        raise ValueError('%s already exists in %s' % (SCRIPT, repo or '.'))
    return save_script(ws, repo, generate(ws, repo))


def exclude_from_git(d):
    """Them ten script vao .git/info/exclude (repo dung .git la file / symlink cua repo tool van dung)."""
    rel = (git(d, 'rev-parse', '--git-path', 'info/exclude', timeout=10) or '').strip()
    if not rel:
        return
    path = os.path.join(d, rel)
    old = read_text(path) or ''
    if '/' + SCRIPT in old.split():
        return
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'a', encoding='utf-8', newline='\n') as f:
            f.write(('' if not old or old.endswith('\n') else '\n') + '/' + SCRIPT + '\n')
    except OSError:
        pass


# ---------------------------------------------------------------- thong tin chung cua workspace

def _env(ws):
    """Thong tin build cap workspace (tinh 1 lan): envsetup Android, lunch, oe-init, build dir Yocto, QNX."""
    def build():
        root = ws.root
        android = next((p for p in ('build/envsetup.sh', 'build/make/envsetup.sh')
                        if os.path.isfile(os.path.join(root, p))), '')
        lunch = []
        for p in ws.repos:
            if 'android-device' in p['tags']:
                lunch += [c for c in (vars_dict(ws.abs(p['path'], 'AndroidProducts.mk')).get('COMMON_LUNCH_CHOICES')
                                      or '').split() if c not in lunch]
        oe = next((n for n in ('setup-environment', 'oe-init-build-env') if os.path.isfile(os.path.join(root, n))), '')
        for p in ws.repos if not oe else ():
            if os.path.isfile(ws.abs(p['path'], 'oe-init-build-env')):
                oe = p['path'] + '/oe-init-build-env'
                break
        builds = [n for n in listdir(root) if os.path.isfile(os.path.join(root, n, 'conf', 'local.conf'))]
        machine = vars_dict(os.path.join(root, builds[0], 'conf', 'local.conf')).get('MACHINE', '') if builds else ''
        qnx = sorted(glob.glob(os.path.expanduser('~/qnx*/qnxsdp-env.sh')))
        return {'android': android, 'lunch': lunch, 'oe': oe, 'builds': builds, 'machine': machine,
                'qnx': qnx[-1] if qnx else ''}
    return ws.cached(('buildenv',), build)


def _src_map(ws):
    """repo -> [recipe] co SRC_URI tro vao repo do (doc tat ca .bb/.bbappend 1 lan)."""
    from .fileview import resolve_ref

    def build():
        out = {}
        for pn, paths in ws.bb_index().items():
            for path in paths:
                own = ws.owner(path)
                if not own:
                    continue
                for r in src_uri_refs(parse_vars(read_text(path) or '', 'bb')):
                    to = resolve_ref(ws, own[0], own[1], 'src', r['raw'])
                    if to and pn not in out.setdefault(to['repo'], []):
                        out[to['repo']].append(pn)
        return out
    return ws.cached(('srcmap',), build) if ws.layers() else {}


# ---------------------------------------------------------------- tung cach build
# Moi ham tra None (khong ap dung) hoac {'title', 'vars': [(ten, gia tri, ghi chu)], 'need': [ten], 'steps': [...]}
# steps: (mo ta, lenh) - lenh bash, chay tu thu muc module tru khi tu cd.

def _q(s):
    return '"%s"' % str(s).replace('\\', '\\\\').replace('"', '\\"').replace('$', '\\$').replace('`', '\\`')


def _android(c):
    t, env = c['tags'], c['env']
    if not ('android-device' in t or ('android' in t and (env['android'] or env['lunch']))):
        return None
    lunch = env['lunch']
    full = 'android-device' in t
    names = [m['name'] for n in ('Android.bp', 'Android.mk') if n in c['files']
             for m in parse_file(n, read_text(os.path.join(c['dir'], n)) or '')['items']][:12]
    steps = [('Android build environment', 'cd "$WS" && source_env %s' % (env['android'] or 'build/envsetup.sh')),
             ('lunch $LUNCH_TARGET', 'lunch "$LUNCH_TARGET"')]
    if full:
        steps.append(('full build for this product', 'm -j"$JOBS"'))
    else:
        steps.append(('build the modules in %s' % c['repo'], 'mmm %s -j"$JOBS"' % _q(c['repo'])))
    alt = ['m %s' % ' '.join(names)] if names else []
    return {'title': 'Android', 'need': ['LUNCH_TARGET'], 'steps': steps, 'alt': alt,
            'vars': [('LUNCH_TARGET', lunch[0] if lunch else 'TODO',
                      'lunch choices found: ' + ', '.join(lunch[:12]) if lunch else 'e.g. aosp_arm64-userdebug')]}


def _yocto(c):
    t, env, ws = c['tags'], c['env'], c['ws']
    layer = {'yocto-layer', 'yocto-multi'} & set(t)
    users = _src_map(ws).get(c['repo'], [])
    if not (layer or users or 'bitbake' in t):
        return None
    if users:
        recipes, note = users[:6], 'recipes whose SRC_URI points to this repo: ' + ', '.join(users[:20])
    else:
        mine = sorted({pn for pn, paths in ws.bb_index().items() for p in paths
                       if p.startswith(c['dir'] + os.sep) and p.endswith('.bb')})
        images = [pn for pn in mine if '-image' in pn or pn.startswith('image-')]
        recipes = images[:1]
        note = ('images in this layer: ' + ', '.join(images[:12]) if images
                else 'recipes in this layer: ' + ', '.join(mine[:15]) if mine else 'e.g. core-image-minimal')
    oe = env['oe'] or 'TODO'
    return {'title': 'Yocto / BitBake', 'need': ['OE_INIT', 'RECIPES'],
            'vars': [('OE_INIT', oe, 'setup script, relative to the workspace root'),
                     ('BUILD_DIR', env['builds'][0] if env['builds'] else 'build',
                      'existing build folders: ' + ', '.join(env['builds']) if env['builds'] else 'created if missing'),
                     ('RECIPES', ' '.join(recipes) or 'TODO', note)]
            + ([] if env['machine'] else [('MACHINE', '${MACHINE:-}', 'only needed when the build folder is new')]),
            'steps': [('BitBake environment', 'cd "$WS" && source_env "$OE_INIT" "$BUILD_DIR"'),
                      ('bitbake $RECIPES', 'bitbake $RECIPES')],
            'alt': ['bitbake -c cleansstate $RECIPES   # rebuild from scratch']}


def _kernel(c):
    t = c['tags']
    if not ({'kernel', 'u-boot'} & set(t)):
        return None
    d = c['dir']
    if 'u-boot' in t:
        cfgs = sorted(n for n in listdir(os.path.join(d, 'configs')) if n.endswith('_defconfig'))
        return {'title': 'U-Boot', 'need': ['DEFCONFIG'],
                'vars': [('DEFCONFIG', cfgs[0] if len(cfgs) == 1 else 'TODO',
                          '%d in configs/, e.g. %s' % (len(cfgs), ', '.join(cfgs[:6]))),
                         ('CROSS_COMPILE', '${CROSS_COMPILE:-}', 'toolchain prefix, e.g. aarch64-linux-gnu-')],
                'steps': [('configure', 'make O=out "$DEFCONFIG"'),
                          ('build', 'make O=out -j"$JOBS" CROSS_COMPILE="$CROSS_COMPILE"')]}
    archs = {a: sorted(n for n in listdir(os.path.join(d, 'arch', a, 'configs')) if n.endswith('defconfig'))
             for a in listdir(os.path.join(d, 'arch')) if os.path.isdir(os.path.join(d, 'arch', a))}
    with_cfg = sorted((a for a in archs if archs[a]), key=lambda a: -len(archs[a]))
    arch = with_cfg[0] if len(with_cfg) == 1 else 'TODO'
    cfgs = archs.get(arch, [])
    return {'title': 'Linux kernel', 'need': ['ARCH', 'DEFCONFIG'],
            'vars': [('ARCH', arch, 'arch with defconfigs: ' + ', '.join(with_cfg[:10])),
                     ('DEFCONFIG', cfgs[0] if len(cfgs) == 1 else 'TODO',
                      'in arch/$ARCH/configs: ' + ', '.join(cfgs[:8]) if cfgs else 'see arch/$ARCH/configs/'),
                     ('CROSS_COMPILE', '${CROSS_COMPILE:-}', 'toolchain prefix, e.g. aarch64-linux-gnu-')],
            'steps': [('configure', 'make O=out ARCH="$ARCH" "$DEFCONFIG"'),
                      ('build', 'make O=out ARCH="$ARCH" CROSS_COMPILE="$CROSS_COMPILE" -j"$JOBS"')],
            'alt': ['make O=out ARCH="$ARCH" CROSS_COMPILE="$CROSS_COMPILE" -j"$JOBS" modules   # modules only']}


def _qnx(c):
    if 'qnx' not in c['tags']:
        return None
    sdp = c['env']['qnx'] or 'TODO'
    return {'title': 'QNX', 'need': ['QNX_ENV'],
            'vars': [('QNX_ENV', '${QNX_ENV:-%s}' % sdp, 'qnxsdp-env.sh of your QNX SDP')],
            'steps': [('QNX environment', 'source_env "$QNX_ENV"'), ('build all variants', 'make -j"$JOBS"')],
            'alt': ['make -C nto/aarch64/so.le   # one variant']}


def _simple(title, steps, alt=()):
    return {'title': title, 'vars': [], 'need': [], 'steps': steps, 'alt': list(alt)}


def _generic(c):
    """Cac he build thong thuong, theo thu tu uu tien cua tags."""
    f, out = c['files'], []
    for tag in c['tags']:
        if tag == 'cmake':
            out.append(_simple('CMake', [('configure', 'cmake -S . -B build -DCMAKE_BUILD_TYPE=Release'),
                                         ('build', 'cmake --build build -j"$JOBS"')]))
        elif tag == 'meson':
            out.append(_simple('Meson', [('configure', 'meson setup build'), ('build', 'meson compile -C build')]))
        elif tag == 'bazel':
            out.append(_simple('Bazel', [('build', 'bazel build //...')]))
        elif tag == 'autotools':
            out.append(_simple('Autotools', [('configure', 'autoreconf -fi && ./configure'),
                                             ('build', 'make -j"$JOBS"')]))
        elif tag == 'cargo':
            out.append(_simple('Cargo', [('build', 'cargo build --release')]))
        elif tag == 'go':
            out.append(_simple('Go', [('build', 'go build ./...')]))
        elif tag == 'gradle':
            if 'pom.xml' in f and not ({'build.gradle', 'build.gradle.kts'} & f):
                out.append(_simple('Maven', [('build', 'mvn -B package')]))
            else:
                out.append(_simple('Gradle', [('build', ('./gradlew' if 'gradlew' in f else 'gradle') + ' build')]))
        elif tag == 'node':
            pkg = read_text(os.path.join(c['dir'], 'package.json')) or ''
            steps = [('install packages', 'npm ci' if 'package-lock.json' in f else 'npm install')]
            if re.search(r'"build"\s*:', pkg):
                steps.append(('build', 'npm run build'))
            out.append(_simple('npm', steps))
        elif tag == 'python' and ({'pyproject.toml', 'setup.py'} & f):
            out.append(_simple('Python', [('build a wheel into dist/', 'python3 -m pip wheel . --no-deps -w dist')]))
        elif tag == 'make':
            out.append(_simple('Make', [('build', 'make -j"$JOBS"')]))
        elif tag == 'devops' and 'Dockerfile' in f:
            name = re.sub(r'[^a-z0-9_.-]', '-', c['repo'].rsplit('/', 1)[-1].lower()) or 'image'
            out.append(_simple('Docker', [('build the image', 'docker build -t %s .' % name)]))
    return out


def _plans(c):
    out = [p for p in (_android(c), _yocto(c), _kernel(c), _qnx(c)) if p]
    own_make = {'kernel', 'u-boot', 'qnx'} & set(c['tags'])          # Makefile cua kernel / QNX: da co cach rieng
    out += [p for p in _generic(c) if not (own_make and p['title'] == 'Make')]
    return out


# ---------------------------------------------------------------- ghep script

TOP = '''#!/usr/bin/env bash
# Build script for {repo}  ({tags})
# Generated by repomap {ver} on {date}: a best guess from the files repomap read.
# Check the values in the first block. Anything set to TODO was not found: fill it in.
# Edit and save here in repomap (tree view) or with any editor.
# Run:  bash {name}     (from any folder)

set -eo pipefail
'''

HELPERS = '''
# ---- helpers (no need to edit below this line, except the build steps)
trap 'echo "*** failed (exit $?) at line $LINENO: $BASH_COMMAND" >&2' ERR
MODULE="$(cd "$(dirname "${{BASH_SOURCE[0]}}")" && pwd)"   # this module: {repo}
WS="$(cd "$MODULE/{up}" && pwd)"                          # workspace root (the folder with .repo/)
JOBS="${{JOBS:-$(nproc 2>/dev/null || echo 4)}}"

# stop with a clear message while a value is still TODO
need() {{
  local v
  for v in "$@"; do
    if [ -z "${{!v}}" ] || [ "${{!v}}" = TODO ]; then
      echo "Please set $v at the top of ${{BASH_SOURCE[0]}} first." >&2
      exit 2
    fi
  done
}}
# source an environment script (they often fail under set -e / set -u)
source_env() {{
  set +eu
  source "$@"
  local rc=$?
  set -e
  return $rc
}}
step() {{ echo; echo "==> $*"; }}
'''


def _vars_text(p):
    """Khoi gia tri can xem / dien: dat ngay dau file de mo editor la thay."""
    if not p['vars']:
        return []
    lines = ['', '# ---- %s: values to check' % p['title']]
    width = max(len('%s=%s' % (k, _q(v))) for k, v, _ in p['vars'])
    for k, v, note in p['vars']:
        val = v if v.startswith('${') else _q(v)
        lines.append(('%s=%s' % (k, val)).ljust(width + 2) + ('# ' + note if note else '').rstrip())
    return lines


def _steps_text(p):
    lines = ['', '# ---- %s: build steps' % p['title']]
    if p['need']:
        lines.append('need ' + ' '.join(p['need']))
    if any(k == 'MACHINE' for k, _, _ in p['vars']):
        lines.append('[ -n "$MACHINE" ] && export MACHINE')
    lines.append('cd "$MODULE"')
    for desc, cmd in p['steps']:
        lines += ['step "%s"' % re.sub(r'(["\\`])', r'\\\1', desc), cmd]       # $VAR trong mo ta duoc thay
    return lines


def generate(ws, repo):
    p = ws.by_path[repo]
    d = ws.abs(repo)
    c = {'ws': ws, 'repo': repo, 'p': p, 'tags': p['tags'], 'dir': d, 'env': _env(ws),
         'files': {n for n in listdir(d) if os.path.isfile(os.path.join(d, n))}}
    plans = _plans(c)
    depth = len([x for x in repo.split('/') if x not in ('', '.')])
    fmt = {'repo': repo or '.', 'tags': ', '.join(p['tags']) or p['kind'], 'ver': __version__,
           'date': time.strftime('%Y-%m-%d'), 'name': SCRIPT, 'up': '/'.join(['..'] * depth) or '.'}
    out, helpers = TOP.format(**fmt).splitlines(), HELPERS.format(**fmt).splitlines()
    if not plans:
        out += helpers + ['', '# ---- TODO: repomap did not recognise a build system here. Add your build commands.',
                          'cd "$MODULE"', 'step "build"', 'echo "Nothing to build yet: edit %s" >&2' % SCRIPT, 'exit 2']
        return '\n'.join(out) + '\n'
    out += _vars_text(plans[0]) + helpers + _steps_text(plans[0])
    alts = [(q['title'], ['%s   # %s' % (cmd, desc) for desc, cmd in q['steps']]) for q in plans[1:]]
    alts = [('', plans[0].get('alt', []))] + alts + [(q['title'] + ' (more)', q.get('alt', [])) for q in plans[1:]]
    alts = [(t, cmds) for t, cmds in alts if cmds]
    if alts:
        out += ['', '# ---- Other ways to build this module (uncomment to use)']
        for title, cmds in alts:
            out += (['# %s:' % title] if title else []) + ['# ' + x for x in cmds]
    out.append('')
    out.append('echo; echo "==> done"')
    return '\n'.join(out) + '\n'

"""Phan loai nhanh 1 repo chi bang cac file danh dau o thu muc goc (1 lan scandir)."""
import os
import re
from fnmatch import fnmatch

from .util import read_text

# thu tu uu tien: tag dau tien la `kind` chinh cua repo
KINDS = ['yocto-layer', 'yocto-multi', 'bitbake', 'kernel', 'u-boot', 'android-device', 'android', 'qnx',
         'cmake', 'meson', 'bazel', 'python', 'devops', 'autotools', 'make', 'devicetree', 'cargo', 'go', 'gradle',
         'node']

# file o goc repo cho thay day la repo DevOps (CI, container, ha tang)
DEVOPS_FILES = {'Jenkinsfile', '.gitlab-ci.yml', 'Dockerfile', 'docker-compose.yml', 'docker-compose.yaml',
                'compose.yml', 'compose.yaml', 'ansible.cfg', 'Chart.yaml', 'kustomization.yaml',
                'azure-pipelines.yml', 'bitbucket-pipelines.yml', '.drone.yml', '.travis.yml'}

MARKS = {
    'cmake': {'CMakeLists.txt'},
    'meson': {'meson.build'},
    'autotools': {'configure.ac', 'configure.in'},
    'bazel': {'WORKSPACE', 'WORKSPACE.bazel', 'MODULE.bazel', 'BUILD.bazel'},
    'android': {'Android.bp', 'Android.mk'},
    'make': {'Makefile', 'makefile', 'GNUmakefile'},
    'cargo': {'Cargo.toml'},
    'go': {'go.mod'},
    'gradle': {'build.gradle', 'build.gradle.kts', 'settings.gradle', 'pom.xml'},
    'node': {'package.json'},
    'python': {'pyproject.toml', 'setup.py', 'setup.cfg', 'requirements.txt', 'Pipfile', 'poetry.lock',
               'tox.ini', 'noxfile.py', 'environment.yml'},
}

QNX_MK = re.compile(r'recurse\.mk|qtargets\.mk|qmacros\.mk|\bqcc\b|QCONFIG')


def _is_qnx(d, names, dirs):
    if 'nto' in dirs or any(n.endswith('.use') for n in names):
        return True
    for n in ('Makefile', 'common.mk'):
        if n in names and QNX_MK.search(read_text(os.path.join(d, n), 4096) or ''):
            return True
    return any(n.endswith('.build') and n != 'meson.build'
               and '[virtual=' in (read_text(os.path.join(d, n), 8192) or '') for n in names)


def classify(d):
    """Tra (kind, tags) cho thu muc repo d."""
    try:
        ents = {e.name: e.is_dir() for e in os.scandir(d)}
    except OSError:
        return 'missing', []
    names, dirs = set(ents), {n for n, isd in ents.items() if isd}
    ex = lambda *p: os.path.exists(os.path.join(d, *p))  # noqa: E731

    t = {k for k, m in MARKS.items() if names & m}
    if ex('conf', 'layer.conf'):
        t.add('yocto-layer')
    if any(ex(n, 'conf', 'layer.conf') for n in dirs if n.startswith('meta')):
        t.add('yocto-multi')
    if ex('bin', 'bitbake') or ex('bitbake', 'bin', 'bitbake'):
        t.add('bitbake')
    if 'Kconfig' in names and {'arch', 'kernel', 'mm'} <= dirs:
        t.add('kernel')
    elif 'Kconfig' in names and {'arch', 'board', 'cmd'} <= dirs:
        t.add('u-boot')
    if 'AndroidProducts.mk' in names or any(fnmatch(n, 'BoardConfig*.mk') for n in names):
        t.add('android-device')
    if _is_qnx(d, names, dirs):
        t.add('qnx')
    if 'dts' in dirs or any(n.endswith(('.dts', '.dtsi')) for n in names):
        t.add('devicetree')
    if names & DEVOPS_FILES or any(n.endswith('.tf') or n.startswith('Dockerfile.') for n in names) \
            or ex('.github', 'workflows') or 'playbooks' in dirs or ('roles' in dirs and ex('ansible.cfg')):
        t.add('devops')

    tags = [k for k in KINDS if k in t]
    return (tags[0] if tags else 'other'), tags

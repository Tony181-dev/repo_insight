import contextlib
import getpass
import io
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from urllib.request import urlopen

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(__file__))

from fixture import make_workspace  # noqa: E402
from repomap.cli import apply_overrides, interactive, parse_args, resolve_settings  # noqa: E402
from repomap.menu import run_menu  # noqa: E402
from repomap.settings import (Settings, detect_ssh_port, load_settings, parse_ssh_command,  # noqa: E402
                              parse_sshd_ports, save_settings, write_template)
from repomap import cli, local  # noqa: E402
from repomap import builds, buildscript, guard  # noqa: E402
from repomap.arch import box_detail, build_arch, slim_arch  # noqa: E402
from repomap.deepscan import deep_scan  # noqa: E402
from repomap.fileview import file_lines, file_view, ver_match  # noqa: E402
from repomap.insight import insight  # noqa: E402
from repomap.parsers import bp_modules, parse_file, parse_vars  # noqa: E402
from repomap.parsers_py import read_toml  # noqa: E402
from repomap.server import start_server  # noqa: E402
from repomap.source import list_dir, search  # noqa: E402
from repomap.workspace import Workspace  # noqa: E402


# cache cua block view: ghi vao thu muc tam, khong dong vao ~/.cache cua may chay test
os.environ['REPOMAP_CACHE_DIR'] = tempfile.mkdtemp(prefix='repomap-cache-')


class WorkspaceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.ws = Workspace(make_workspace(cls.tmp.name), workers=4).load()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def refs(self, repo, rel):
        return {(r['k'], r['to']['repo'], r['to']['file']) for r in file_view(self.ws, repo, rel)['refs'] if 'to' in r}

    # ---- tang 0
    def test_manifest(self):
        ws = self.ws
        self.assertEqual(ws.mode, 'manifest')
        self.assertIn('tools/extra', ws.by_path)                  # local_manifests
        self.assertNotIn('obsolete/thing', ws.by_path)            # remove-project
        self.assertEqual(ws.by_path['kernel/linux']['revision'], 'v6.1')
        self.assertEqual(ws.by_path['src/foo-app']['revision'], 'main')     # remote revision > default
        self.assertEqual(ws.by_path['src/foo-app']['url'], 'ssh://git.example.com/acme/foo-app')

    def test_classify(self):
        kind = {p: r['kind'] for p, r in self.ws.by_path.items()}
        self.assertEqual(kind['layers/poky'], 'yocto-multi')
        self.assertEqual(kind['layers/meta-acme'], 'yocto-layer')
        self.assertEqual(kind['kernel/linux'], 'kernel')
        self.assertEqual(kind['device/acme/board'], 'android-device')
        self.assertEqual(kind['qnx/drivers/spi'], 'qnx')
        self.assertEqual(kind['src/foo-app'], 'cmake')
        self.assertIn('bitbake', self.ws.by_path['layers/poky']['tags'])

    def test_owner_and_safety(self):
        ws = self.ws
        self.assertEqual(ws.owner(ws.abs('kernel/linux', 'mm/mmap.c')), ('kernel/linux', 'mm/mmap.c'))
        self.assertIsNone(ws.owner(os.path.dirname(ws.root)))
        with self.assertRaises(ValueError):
            ws.abs('kernel/linux', '../../../etc/passwd')

    # ---- tang 1
    def section(self, repo, title):
        secs = {s['title']: dict(s['rows']) for s in insight(self.ws, repo)['sections']}
        self.assertIn(title, secs, list(secs))
        return secs[title]

    def test_insight(self):
        self.assertIn('acme-board', self.section('layers/meta-acme', 'Layer meta-acme')['machines'])
        self.assertEqual(self.section('layers/meta-acme', 'Layer meta-acme')['depends'], 'core')
        self.assertEqual(self.section('kernel/linux', 'Kernel')['version'], '6.1.20')
        self.assertEqual(self.section('device/acme/board', 'BoardConfig.mk')['TARGET_ARCH'], 'arm64')
        self.assertIn('aarch64/so.le', self.section('qnx/drivers/spi', 'QNX')['variants'])
        self.assertIn('conf/machine/acme-board.conf', insight(self.ws, 'layers/meta-acme')['keys'])

    # ---- tang 2
    def cats(self, repo):
        return {c['id']: c for c in deep_scan(self.ws, repo)['cats']}

    def test_deep_scan(self):
        acme = self.cats('layers/meta-acme')
        self.assertEqual({'layer', 'machine', 'recipe', 'bbappend'}, set(acme))
        self.assertEqual(acme['recipe']['items'][0]['l'], 'foo · 1.0')
        dev = self.cats('device/acme/board')
        self.assertIn('cc_library: libacme', [i['l'] for i in dev['modules']['items']])
        self.assertIn('product', dev)
        self.assertEqual(self.cats('qnx/drivers/spi')['qnxvar']['items'][0]['l'], 'nto/aarch64/so.le')
        self.assertEqual(set(self.cats('kernel/linux')) >= {'defconfig', 'dts', 'kconfig', 'make'}, True)

    # ---- tang 3
    def test_bitbake_refs(self):
        refs = self.refs('layers/poky', 'meta/recipes-core/busybox/busybox_1.36.bb')
        self.assertIn(('require', 'layers/poky', 'meta/recipes-core/busybox/busybox.inc'), refs)
        self.assertIn(('inherit', 'layers/poky', 'meta/classes-recipe/cmake.bbclass'), refs)
        self.assertIn(('appended-by', 'layers/meta-acme', 'recipes-core/busybox/busybox_%.bbappend'), refs)
        back = self.refs('layers/meta-acme', 'recipes-core/busybox/busybox_%.bbappend')
        self.assertIn(('appends', 'layers/poky', 'meta/recipes-core/busybox/busybox_1.36.bb'), back)
        src = self.refs('layers/meta-acme', 'recipes-app/foo/foo_1.0.bb')
        self.assertIn(('src', 'src/foo-app', ''), src)

    def test_other_refs(self):
        self.assertIn(('inherit-product', 'device/acme/common', 'common.mk'),
                      self.refs('device/acme/board', 'acme.mk'))
        self.assertIn(('include', 'kernel/linux', 'arch/arm64/boot/dts/acme/board.dtsi'),
                      self.refs('kernel/linux', 'arch/arm64/boot/dts/acme/board.dts'))
        self.assertIn(('source', 'kernel/linux', 'arch/Kconfig'), self.refs('kernel/linux', 'Kconfig'))
        self.assertIn(('add_subdirectory', 'src/foo-app', 'lib/CMakeLists.txt'),
                      self.refs('src/foo-app', 'CMakeLists.txt'))
        self.assertIn(('include', 'qnx/drivers/spi', 'common.mk'),
                      self.refs('qnx/drivers/spi', 'nto/aarch64/so.le/Makefile'))

    def test_c_includes(self):
        refs = self.refs('src/foo-app', 'main.c')
        self.assertIn(('include', 'src/foo-app', 'include/foo.h'), refs)
        self.assertIn(('include', 'src/foo-app', 'lib/inner/deep.h'), refs)
        self.assertIn(('include', 'src/foo-app', 'lib/inner/deep2.h'), refs)     # chi tim duoc theo ten file

    # ---- Python / DevOps
    def test_python_devops_classify_and_insight(self):
        self.assertEqual(self.ws.by_path['tools/extra']['tags'], ['python', 'devops'])
        py = self.section('tools/extra', 'Python')
        self.assertEqual((py['name'], py['version'], py['requires-python']), ('pyapp', '0.3.1', '>=3.8'))
        self.assertIn('requests', py['dependencies'])
        self.assertEqual(py['packages'], 'src/pyapp')
        self.assertEqual(self.section('tools/extra', 'GitLab CI')['stages'], 'build, test, deploy')
        self.assertIn('unit-test', self.section('tools/extra', 'GitLab CI')['jobs'])
        self.assertIn('lint, test', self.section('tools/extra', 'GitHub Actions')['CI'])
        self.assertIn('Build, Deploy', self.section('tools/extra', 'Jenkins')['Jenkinsfile'])
        self.assertIn('FROM python:3.11-slim', self.section('tools/extra', 'Docker')['Dockerfile'])
        self.assertIn('web, db', self.section('tools/extra', 'Docker')['docker-compose.yml'])
        self.assertEqual(self.section('tools/extra', 'Ansible')['roles'], 'common, nginx')
        self.assertEqual(self.section('tools/extra', 'Terraform')['providers'], 'aws')

    def test_python_devops_deep_scan(self):
        cats = self.cats('tools/extra')
        for cid in ('ci', 'docker', 'terraform', 'ansible', 'pydeps', 'pytest', 'py', 'shell'):
            self.assertIn(cid, cats)
        self.assertEqual(cats['ci']['count'], 3)                  # .gitlab-ci.yml, workflow, Jenkinsfile

    def test_python_and_devops_refs(self):
        refs = self.refs('tools/extra', 'src/pyapp/cli.py')
        self.assertIn(('import', 'tools/extra', 'src/pyapp/core.py'), refs)
        self.assertIn(('import', 'tools/extra', 'src/pyapp/util.py'), refs)
        self.assertIn(('import', 'tools/extra', 'src/pyapp/core.py'), self.refs('tools/extra', 'tests/test_core.py'))
        self.assertIn(('include', 'tools/extra', 'ci/common.yml'), self.refs('tools/extra', '.gitlab-ci.yml'))
        self.assertIn(('role', 'tools/extra', 'roles/nginx/tasks/main.yml'), self.refs('tools/extra', 'site.yml'))
        self.assertIn(('include_tasks', 'tools/extra', 'roles/nginx/tasks/extra.yml'),
                      self.refs('tools/extra', 'roles/nginx/tasks/main.yml'))
        self.assertIn(('module', 'tools/extra', 'modules/net/main.tf'), self.refs('tools/extra', 'main.tf'))
        items = file_view(self.ws, 'tools/extra', 'src/pyapp/core.py')['items']
        self.assertEqual([(i['type'], i['name']) for i in items], [('class', 'Runner'), ('method', 'Runner.go'), ('def', 'run')])

    # ---- full source
    def test_list_dir(self):
        d = list_dir(self.ws, 'tools/extra', 'src/pyapp')
        self.assertEqual([f['name'] for f in d['files']], ['__init__.py', 'cli.py', 'core.py', 'util.py'])
        nested = list_dir(self.ws, '', 'device/acme')
        self.assertEqual(nested['dirs'], [{'name': 'board', 'repo': 'device/acme/board'},
                                          {'name': 'common', 'repo': 'device/acme/common'}])
        self.assertIn('error', list_dir(self.ws, 'tools/extra', 'nope'))
        with self.assertRaises(ValueError):
            list_dir(self.ws, 'tools/extra', '../../..')

    def test_search(self):
        names = search(self.ws, 'tools/extra', '', 'core', 'name')['results']
        self.assertEqual(names[0]['f'], 'src/pyapp/core.py')                    # khop ten file truoc
        text = search(self.ws, 'tools/extra', 'src', 'DEF RUN', 'text')['results']
        self.assertEqual(text, [{'f': 'src/pyapp/core.py', 'n': 5, 'text': 'def run():'}])
        self.assertIn('error', search(self.ws, 'tools/extra', '', 'x', 'name'))

    def test_long_file_is_not_cut(self):
        path = self.ws.abs('src/foo-app', 'big.c')
        with open(path, 'w', newline='\n') as f:
            f.write('\n'.join('int v%d;' % i for i in range(1, 12001)) + '\n#include "foo.h"\n')
        v = file_view(self.ws, 'src/foo-app', 'big.c')
        self.assertEqual((v['lines'], v['chunk'], v['partial'], v['truncated']), (12002, 2000, False, False))
        self.assertEqual(v['text'].count('\n'), 1999)                            # chi gui doan dau
        self.assertIn(('include', 'foo.h', 12001), [(r['k'], r['raw'], r['line']) for r in v['refs']])  # tinh tren ca file
        part = file_lines(self.ws, 'src/foo-app', 'big.c', 10000, 3)
        self.assertEqual(part['lines'], ['int v10000;', 'int v10001;', 'int v10002;'])
        self.assertEqual(file_lines(self.ws, 'src/foo-app', 'big.c', 12001, 5000)['lines'], ['#include "foo.h"', ''])
        with open(path, 'a') as f:                                               # file doi -> doc lai, khong dung cache cu
            f.write('int tail;\n')
        self.assertEqual(file_lines(self.ws, 'src/foo-app', 'big.c', 12002, 1)['lines'], ['int tail;'])
        os.remove(path)

    # ---- block view
    def test_arch(self):
        a = build_arch(self.ws)
        box = {b['id']: b for b in a['boxes']}
        names = lambda bid: {c['name'] for c in box[bid]['comps']}  # noqa: E731
        external = lambda eco: {c['name'] for b in a['boxes'] if not b['repo'] and b['id'].endswith('#' + eco)
                                for c in b['comps']}  # noqa: E731
        self.assertEqual(names('apps/venc#app'), {'venc', 'venc-log'})      # venc_test bo qua
        self.assertEqual(box['apps/venc#app']['comps'][0]['svc'], ['venc'])  # venc.service
        self.assertIn('venc_core', names('apps/venc#lib'))
        self.assertIn('libgk7205_backend', names('platform/hal/gk7205#hal'))  # thu muc hal/ -> tang HAL
        self.assertEqual(names('drivers/rtl8812eu#kernel'), {'8812eu'})      # obj-m, bo qua TARGET
        self.assertIn('spi-acme', names('qnx/drivers/spi#kernel'))
        self.assertIn('Linux kernel', names('kernel/linux#kernel'))
        self.assertEqual(names('device/acme/board#app'), {'acmed'})
        self.assertIn('libacme', names('device/acme/board#lib'))
        self.assertIn('acme-board', names('layers/meta-acme#board'))
        self.assertTrue({'pyapp', 'web'} <= names('tools/extra#app'))
        self.assertEqual(names('tools/extra#ext'), {'db'})                    # chi co image: service ben thu ba
        self.assertIn('GitLab CI', names('tools/extra#build'))
        self.assertTrue({'libusb-1.0', 'libmpi'} <= external('c'))
        self.assertTrue({'liblog', 'libcutils', 'libutils'} <= external('android'))   # libutils: tu cc_defaults
        self.assertNotIn('libacme_headers', external('android'))             # header lib trong workspace
        self.assertIn('libacme_headers', names('device/acme/board#lib'))
        self.assertEqual(names('device/acme/board#hal'), {'acme-sensord'})    # relative_install_path: "hw"
        self.assertNotIn('acme_defaults', names('device/acme/board#lib'))
        edges = {(x, y, k) for x, y, _, k in a['edges']}
        self.assertIn(('apps/venc#app', 'platform/hal/gk7205#hal', 'use'), edges)
        self.assertIn(('apps/venc#app', 'apps/venc#lib', 'use'), edges)
        self.assertIn(('device/acme/board#app', 'device/acme/board#lib', 'use'), edges)
        self.assertIn(('device/acme/board#hal', 'device/acme/board#lib', 'use'), edges)
        self.assertIn(('device/acme/common#board', 'device/acme/board#hal', 'start'), edges)   # .rc o repo khac
        self.assertIn(('layers/meta-acme#build', 'src/foo-app#app', 'build'), edges)   # SRC_URI -> repo
        self.assertIn(('apps/venc#app', 'src/foo-app#app', 'use'), edges)    # recipe venc DEPENDS recipe foo
        self.assertEqual(a['errors'], [])
        slim = slim_arch(a)                                       # giao dien: ban gon + chi tiet khi bam khoi
        venc = next(b for b in slim['boxes'] if b['id'] == 'apps/venc#app')
        self.assertEqual(venc['comps'][0], {'name': 'venc', 'type': 'exe', 'svc': 1})
        self.assertEqual(venc['n'], {'svc': 2})                               # venc + venc-log.service
        detail = {c['name']: c for c in box_detail(a, 'apps/venc#app')['comps']}
        self.assertEqual(detail['venc']['f'], 'CMakeLists.txt')
        self.assertIn(['venc-log', 'apps/venc#app'], detail['venc']['uses'])   # Wants=venc-log.service
        self.assertIn(['libgk7205_backend', 'platform/hal/gk7205#hal'], detail['venc']['uses'])
        sensord = box_detail(a, 'device/acme/board#hal')['comps'][0]
        self.assertEqual(sensord['svc'], ['vendor.sensord'])
        lib = next(c for c in box_detail(a, 'device/acme/board#lib')['comps'] if c['name'] == 'libacme')
        self.assertIn(['acme-sensord', 'device/acme/board#hal'], lib['usedBy'])

    def test_arch_layers_by_ecosystem(self):
        """backend/, drivers/ chi goi y tang HAL voi code native; ten phu thuoc chi khop trong cung he sinh thai."""
        with tempfile.TemporaryDirectory() as tmp:
            make_workspace(tmp)
            files = {'tools/extra/backend/main.py': 'print(1)\n',
                     'tools/extra/backend/requirements.txt': 'Foo_Bar\n',
                     'tools/extra/backend/pyproject.toml': '[project]\nname = "web-backend"\n'
                                                           'dependencies = ["foo.bar", "venc"]\n'
                                                           '[project.scripts]\nweb-backend = "x:y"\n',
                     'tools/extra/foo-bar/pyproject.toml': '[project]\nname = "Foo_Bar"\n'}
            for rel, text in files.items():
                os.makedirs(os.path.dirname(os.path.join(tmp, rel)), exist_ok=True)
                with open(os.path.join(tmp, rel), 'w') as f:
                    f.write(text)
            a = build_arch(Workspace(tmp, workers=2).load(), refresh=True)
            comps = {(b['id'], c['name']): c for b in a['boxes'] for c in box_detail(a, b['id'])['comps']}
            self.assertIn(('tools/extra#app', 'web-backend'), comps)            # khong bi day xuong HAL
            uses = comps[('tools/extra#app', 'web-backend')]['uses']
            self.assertIn(['Foo_Bar', 'tools/extra#lib'], uses)       # foo.bar = Foo_Bar (PEP 503), same scope
            self.assertNotIn(['venc', 'apps/venc#app'], uses)                   # goi Python "venc" != binary C venc
            self.assertTrue(any(name == 'venc' and bid.startswith('ext#') and bid.endswith('#py') for name, bid in uses))

    def test_arch_cache(self):
        """Repo chua doi HEAD / index: lan sau dung lai ket qua tren dia; Rescan hoac doi commit: quet lai."""
        with tempfile.TemporaryDirectory() as tmp:
            ws = Workspace(make_workspace(tmp), workers=2).load()
            git_dir = os.path.join(tmp, 'apps', 'venc', '.git')
            os.makedirs(git_dir)
            head = os.path.join(git_dir, 'HEAD')
            with open(head, 'w') as f:
                f.write('0123abcd\n')
            first = build_arch(ws)
            self.assertEqual(first['cached'], 0)
            again = build_arch(ws)
            self.assertEqual(again['cached'], 1)
            self.assertEqual([b['id'] for b in again['boxes']], [b['id'] for b in first['boxes']])
            self.assertEqual(again['edges'], first['edges'])
            self.assertEqual(build_arch(ws, refresh=True)['cached'], 0)
            with open(head, 'w') as f:
                f.write('ffff0000\n')
            self.assertEqual(build_arch(ws)['cached'], 0)

    def test_toml_empty_value(self):
        """Dong 'key =' khong co gia tri (template, file dang sua do) tung lam IndexError ca block view."""
        self.assertEqual(read_toml('[package]\nname = "x"\nversion =\n')['package'], {'name': 'x', 'version': ''})

    def test_file_view_errors(self):
        self.assertIn('error', file_view(self.ws, 'kernel/linux', 'nope.c'))

    # ---- server
    def test_server(self):
        srv = start_server(self.ws, '127.0.0.1', 0)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = 'http://127.0.0.1:%d' % srv.server_address[1]
        try:
            get = lambda p: urlopen(base + p, timeout=10).read()  # noqa: E731
            self.assertIn(b'repomap', get('/'))
            self.assertIn(b'buildTree', get('/js/main.js'))
            tree = json.loads(get('/api/tree'))
            self.assertEqual(len(tree['repos']), len(self.ws.repos))
            ls = json.loads(get('/api/ls?repo=tools/extra&dir=src'))
            self.assertEqual(ls['dirs'], [{'name': 'pyapp'}])
            hits = json.loads(get('/api/search?repo=tools/extra&q=helper&mode=text'))['results']
            self.assertTrue(any(h['f'] == 'src/pyapp/util.py' for h in hits))
            self.assertIn(b'openSource', get('/js/source.js') + get('/js/i18n.js'))
            ins = json.loads(get('/api/insight?repo=kernel/linux'))
            self.assertTrue(ins['keys'])
            arch = json.loads(get('/api/arch'))
            self.assertTrue(arch['boxes'] and arch['edges'])
            self.assertIn(b'archFocusRepo', get('/js/arch.js'))
            with self.assertRaises(Exception):
                get('/../../etc/passwd')
        finally:
            srv.shutdown()
            srv.server_close()

    def test_server_quiet_on_client_reset(self):
        """Client ngat ket noi dot ngot (trinh duyet huy request) khong duoc in traceback."""
        srv = start_server(self.ws, '127.0.0.1', 0)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        err = io.StringIO()
        try:
            with contextlib.redirect_stderr(err):
                for _ in range(3):
                    s = socket.create_connection(srv.server_address, timeout=5)
                    s.sendall(b'GET /api/tr')                        # request dang do
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack('ii', 1, 0))
                    s.close()                                        # -> RST
                time.sleep(0.5)
        finally:
            srv.shutdown()
            srv.server_close()
        self.assertNotIn('Traceback', err.getvalue())


class ParserTest(unittest.TestCase):
    def test_vars_skip_functions_and_recipes(self):
        bb = 'A = "1"\ndo_x() {\n    B = "2"\n}\nC ?= "3"\nRDEPENDS:${PN} += "x"\n'
        self.assertEqual([v[1] for v in parse_vars(bb, 'bb')], ['A', 'C', 'RDEPENDS:${PN}'])
        mk = 'X := 1\nall:\n\tY = 2\ndefine Z\nW = 3\nendef\nV += a \\\n  b\n'
        self.assertEqual([(v[1], v[3]) for v in parse_vars(mk, 'make')], [('X', '1'), ('V', 'a b')])

    def test_bp_modules(self):
        mods = bp_modules('a {\n name: "x", libs: ["y"],\n target: { android: { cflags: [] } },\n}\nb { name: "z" }\n')
        self.assertEqual([(m['type'], m['name'], m['line']) for m in mods], [('a', 'x', 1), ('b', 'z', 5)])
        self.assertEqual(mods[0]['deps'], ['y'])

    def test_android_mk(self):
        mk = 'include $(CLEAR_VARS)\nLOCAL_MODULE := foo\nLOCAL_SHARED_LIBRARIES := liba\ninclude $(BUILD_SHARED_LIBRARY)\n'
        res = parse_file('Android.mk', mk)
        self.assertEqual(res['items'][0]['name'], 'foo')
        self.assertEqual(res['refs'], [])                     # include $(CLEAR_VARS) khong phai tham chieu

    def test_ver_match(self):
        self.assertTrue(ver_match('%', '1.36'))
        self.assertTrue(ver_match('1.3%', '1.36'))
        self.assertFalse(ver_match('2.%', '1.36'))
        self.assertTrue(ver_match('', ''))


class ReleaseTest(unittest.TestCase):
    def test_release_package(self):
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tools'))
        import make_release
        files = {n: (data, mode) for n, data, mode in make_release.release_files('9.9')}
        self.assertEqual(set(files), {'repomap.pyz', 'install.sh', 'repomap-pc.cmd', 'README.txt', 'HUONG_DAN.html',
                                      'app.png'})
        self.assertIn(b'\r\n', files['repomap-pc.cmd'][0])                 # cmd tren Windows can CRLF
        self.assertNotIn(b'\r', files['install.sh'][0])                     # sh tren Linux can LF
        self.assertEqual(files['install.sh'][1], 0o755)
        self.assertIn(b'repomap-9.9.tar.gz', files['README.txt'][0])
        guide = files['HUONG_DAN.html'][0]
        for secret in (b'git clone', b'release.bat', b'build.bat', b'maintainer', b'repomap/rules.py'):
            self.assertNotIn(secret, guide)                                 # phan cho nguoi giu ma nguon da bi cat
        self.assertIn(b'id="cai-dat"', guide)


class SettingsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, 'sub', 'config.ini')

    def tearDown(self):
        self.tmp.cleanup()

    def test_template_and_load(self):
        write_template(self.path)
        with self.assertRaises(SystemExit):                    # khong ghi de
            write_template(self.path)
        s = load_settings(self.path)
        self.assertEqual((s.host, s.port, s.open_browser, s.ssh_local_port), ('127.0.0.1', 6969, 'auto', 0))
        self.assertEqual(s.source, self.path)

    def test_values_and_cli_override(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, 'w') as f:
            f.write('[server]\nport = 9100  # cong\nopen_browser = false\n[ssh]\nlocal_port = 18800\n'
                    'user = dev\nhost = build01\n')
        s = load_settings(self.path)
        self.assertEqual((s.port, s.open_browser), (9100, 'no'))
        s.ssh_port = 22
        self.assertEqual(s.tunnel_hint(9101), 'ssh -N -L 18800:127.0.0.1:9101 dev@build01')
        s.ssh_port, s.ssh_local_port = 2222, 0
        self.assertEqual(s.tunnel_hint(9101), 'ssh -N -L 9101:127.0.0.1:9101 -p 2222 dev@build01')
        a = parse_args(['/ws', '--config', self.path, '--port', '9300', '--host', '0.0.0.0'])
        s = resolve_settings(a)
        self.assertEqual((s.host, s.port, s.ssh_user), ('0.0.0.0', 9300, 'dev'))

    def test_bad_value(self):
        os.makedirs(os.path.dirname(self.path))
        for bad in ('[server]\nport = abc\n', '[ssh]\nport = 70000\n'):
            with open(self.path, 'w') as f:
                f.write(bad)
            with self.assertRaises(SystemExit):
                load_settings(self.path)

    def test_save_round_trip_keeps_comments(self):
        s = Settings(port=9200, ssh_host='build.example.com', ssh_user='dev', ssh_port=2200, workspace='/w/ws')
        save_settings(s, self.path)
        with open(self.path, encoding='utf-8') as f:
            text = f.read()
        self.assertIn('# Server port', text)
        self.assertIn('local_port = \n', text)                        # 0 -> rong (= tu dong)
        back = load_settings(self.path)
        self.assertEqual((back.port, back.ssh_host, back.ssh_user, back.ssh_port, back.ssh_local_port, back.workspace),
                         (9200, 'build.example.com', 'dev', 2200, 0, '/w/ws'))

    def test_parse_ssh_command(self):
        self.assertEqual(parse_ssh_command('ssh -p 56614 worker@hoang6-nguyen-hkmc.vbee.lge.com'),
                         ('worker', 'hoang6-nguyen-hkmc.vbee.lge.com', 56614))
        self.assertEqual(parse_ssh_command('  $ ssh worker@h.vbee.lge.com -p56614 '), ('worker', 'h.vbee.lge.com', 56614))
        self.assertEqual(parse_ssh_command('ssh dev@build01'), ('dev', 'build01', 22))
        self.assertEqual(parse_ssh_command('ssh -l dev -p 2200 build01'), ('dev', 'build01', 2200))
        for bad in ('', 'ssh', 'ssh -p 99999 a@b', 'ssh build01', 'ssh a@b;rm'):
            with self.assertRaises(ValueError):
                parse_ssh_command(bad)

    def test_sshd_port_detection(self):
        self.assertEqual(parse_sshd_ports('#Port 22\nPort 2222\n  port 2200\nListenAddress 0.0.0.0\n'), [2222, 2200])
        cfg = os.path.join(self.tmp.name, 'sshd_config')
        self.assertEqual(detect_ssh_port(cfg), (22, 'default'))      # khong co file -> 22
        os.makedirs(os.path.join(self.tmp.name, 'sshd_config.d'))
        with open(cfg, 'w') as f:
            f.write('# Port 22\n')
        with open(os.path.join(self.tmp.name, 'sshd_config.d', '10-port.conf'), 'w') as f:
            f.write('Port 60022\n')
        self.assertEqual(detect_ssh_port(cfg)[0], 60022)


class MenuTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = os.path.join(self.tmp.name, 'config.ini')

    def tearDown(self):
        self.tmp.cleanup()

    def run_menu(self, answers, root=None, s=None):
        it, out = iter(answers), []
        s = s or Settings(source=self.cfg)
        return run_menu(s, root, inp=lambda prompt: (out.append(prompt), next(it))[1], out=out.append), s, out

    def test_configuration_is_saved_then_exit(self):
        answers = ['2', 'abc', '9300', 'build.example.com', 'dev', '2222', '', '0']
        result, s, out = self.run_menu(answers)
        self.assertIsNone(result)
        self.assertTrue(any('not a number' in o for o in out))          # hoi lai khi nhap sai
        back = load_settings(self.cfg)
        self.assertEqual((back.port, back.ssh_host, back.ssh_user, back.ssh_port, back.ssh_local_port),
                         (9300, 'build.example.com', 'dev', 2222, 0))
        self.assertIn('ssh -N -L 9300:127.0.0.1:9300 -p 2222 dev@build.example.com', '\n'.join(out))

    def test_quick_setup(self):
        answers = ['3', 'ssh nonsense', 'ssh -p 56614 worker@hoang6-nguyen-hkmc.vbee.lge.com', '0']
        result, s, out = self.run_menu(answers)
        self.assertIsNone(result)
        text = '\n'.join(out)
        self.assertIn('Please copy the SSH command', text)
        self.assertIn('expected something like', text)                 # hoi lai khi dan sai
        back = load_settings(self.cfg)
        self.assertEqual((back.ssh_host, back.ssh_user, back.ssh_port, back.port, back.ssh_local_port),
                         ('hoang6-nguyen-hkmc.vbee.lge.com', 'worker', 56614, 6969, 0))
        self.assertIn('ssh -N -L 6969:127.0.0.1:6969 -p 56614 worker@hoang6-nguyen-hkmc.vbee.lge.com', text)

    def test_quick_setup_cancel_and_busy_port(self):
        with socket.socket() as busy:
            busy.bind(('127.0.0.1', 0))
            busy.listen(16)
            port = busy.getsockname()[1]
            result, s, out = self.run_menu(['2', str(port), 'h', 'u', '22', '', '3', '', '0'])
        text = '\n'.join(out)
        self.assertIn('port %d is already in use' % port, text)
        self.assertIn('Cancelled', text)

    def test_run_scan_asks_for_workspace(self):
        ws = self.tmp.name
        result, _, out = self.run_menu(['', os.path.join(ws, 'nope'), ws])     # Enter = 1. Run scan
        self.assertEqual(result, os.path.abspath(ws))
        self.assertTrue(any('folder not found' in o for o in out))

    def test_run_scan_uses_given_root_and_unknown_choice(self):
        result, _, out = self.run_menu(['7', '1'], root=self.tmp.name)
        self.assertEqual(result, os.path.abspath(self.tmp.name))
        self.assertTrue(any('Unknown choice' in o for o in out))

    def test_cli_flags_are_not_saved(self):
        saved = Settings(source=self.cfg)
        s = apply_overrides(parse_args(['/ws', '--no-browser', '--port', '9999']), saved)
        self.assertEqual((s.open_browser, s.port), ('no', 9999))
        self.assertEqual((saved.open_browser, saved.port), ('auto', 6969))   # ban luu khong bi doi

    def test_no_menu_when_not_interactive(self):
        self.assertFalse(interactive(parse_args(['/ws', '--run'])))
        self.assertFalse(interactive(parse_args(['/ws', '--json', 'x'])))


# bash that chay duoc script (tren Windows bo qua: bash.exe trong System32 la WSL, co the chua cai distro)
BASH = os.environ.get('REPOMAP_TEST_BASH') or (shutil.which('bash') if os.name != 'nt' else None)


class BuildScriptTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Workspace(make_workspace(self.tmp.name), workers=4).load()

    def tearDown(self):
        self.tmp.cleanup()

    def gen(self, repo):
        return buildscript.generate(self.ws, repo)

    def test_generate_by_kind(self):
        android = self.gen('device/acme/board')
        self.assertIn('LUNCH_TARGET="acme-userdebug"', android)              # tu COMMON_LUNCH_CHOICES
        self.assertIn('lunch "$LUNCH_TARGET"', android)
        self.assertIn('# m libacme libacme_headers acmed acme-sensord', android)  # module trong Android.bp: cach khac
        yocto_src = self.gen('src/foo-app')
        self.assertIn('RECIPES="foo"', yocto_src)                            # recipe co SRC_URI tro vao repo nay
        self.assertIn('bitbake $RECIPES', yocto_src)
        self.assertIn('# cmake --build build', yocto_src)                    # CMake van ghi kem
        self.assertIn('OE_INIT="TODO"', yocto_src)
        kernel = self.gen('kernel/linux')
        self.assertIn('ARCH="arm64"', kernel)
        self.assertIn('DEFCONFIG="acme_defconfig"', kernel)
        self.assertNotIn('# Make:', kernel)                                  # Makefile kernel khong lap lai
        self.assertIn('cmake -S . -B build', self.gen('apps/venc'))
        self.assertIn('autoreconf -fi', self.gen('platform/hal/gk7205'))
        self.assertIn('source_env "$QNX_ENV"', self.gen('qnx/drivers/spi'))
        extra = self.gen('tools/extra')
        self.assertIn('pip wheel', extra)
        self.assertIn('# docker build -t extra .', extra)
        self.assertIn('WS="$(cd "$MODULE/../.." && pwd)"', extra)
        self.assertIn('did not recognise a build system', self.gen('device/acme/common'))

    @unittest.skipUnless(BASH, 'needs bash')
    def test_scripts_are_valid_bash_and_stop_on_todo(self):
        for p in self.ws.repos:
            r = buildscript.create_script(self.ws, p['path'])
            path = buildscript.script_path(self.ws, p['path'])
            check = subprocess.run([BASH, '-n', path], capture_output=True, text=True)
            self.assertEqual(check.returncode, 0, p['path'] + ': ' + check.stderr)
            self.assertTrue(r['text'].startswith('#!/usr/bin/env bash'))
        run = subprocess.run([BASH, buildscript.script_path(self.ws, 'src/foo-app')], capture_output=True, text=True)
        self.assertEqual(run.returncode, 2)
        self.assertIn('Please set OE_INIT', run.stderr)

    def test_create_save_and_git_exclude(self):
        repo, ws = 'apps/venc', self.ws
        d = ws.abs(repo)
        has_git = shutil.which('git') is not None
        if has_git:
            subprocess.run(['git', 'init', '-q', d], check=True)
        self.assertFalse(buildscript.has_script(ws, repo))
        buildscript.create_script(ws, repo)
        self.assertTrue(buildscript.has_script(ws, repo))
        with self.assertRaisesRegex(ValueError, 'already exists'):
            buildscript.create_script(ws, repo)
        saved = buildscript.save_script(ws, repo, 'echo hi\r\n')
        self.assertEqual(buildscript.read_script(ws, repo)['text'], 'echo hi\n')
        self.assertTrue(saved['saved'])
        self.assertIn('cmake', buildscript.create_script(ws, repo, overwrite=True)['text'])
        with self.assertRaisesRegex(ValueError, 'too big'):
            buildscript.save_script(ws, repo, 'x' * (buildscript.MAX_SIZE + 1))
        if has_git:
            with open(os.path.join(d, '.git', 'info', 'exclude')) as f:
                self.assertEqual(f.read().count('/repomap_build_script.sh'), 1)     # them 1 lan

    def test_rename_build_script(self):
        """Build script doi ten duoc: file moi van la build script (dong MARKER), khong tinh vao script rieng."""
        repo, ws = 'apps/venc', self.ws
        buildscript.create_script(ws, repo)
        runs = builds.Builds(ws)
        r = runs.rename_script(repo, buildscript.SCRIPT, 'build_venc')
        self.assertEqual((r['name'], r['build']), ('repomap_build_venc.sh', True))
        self.assertEqual(buildscript.build_script_name(ws, repo), 'repomap_build_venc.sh')
        self.assertEqual(buildscript.custom_scripts(ws, repo), [])
        self.assertIn('cmake', buildscript.create_script(ws, repo, overwrite=True)['text'])   # Regenerate: cung file
        self.assertFalse(os.path.exists(buildscript.script_path(ws, repo)))
        buildscript.save_script(ws, repo, '#!/bin/sh\necho old\n', 'repomap_build_venc.sh')       # Save giu dong MARKER
        buildscript.rename_script(ws, repo, 'repomap_build_venc.sh', 'repomap_build_script.sh')
        buildscript.rename_script(ws, repo, buildscript.SCRIPT, 'b2')
        self.assertEqual(buildscript.build_script_name(ws, repo), 'repomap_b2.sh')
        self.assertEqual(buildscript.read_script(ws, repo, 'repomap_b2.sh')['text'],
                         '#!/bin/sh\n' + buildscript.MARKER + '\necho old\n')
        buildscript.create_empty_script(ws, repo, 'clean')
        self.assertEqual([s['name'] for s in buildscript.custom_scripts(ws, repo)], ['repomap_clean.sh'])
        with self.assertRaisesRegex(ValueError, 'reserved'):
            buildscript.rename_script(ws, repo, 'repomap_clean.sh', 'build_script')
            st = subprocess.run(['git', 'status', '--porcelain'], cwd=d, capture_output=True, text=True).stdout
            self.assertNotIn('repomap_build_script.sh', st)

    def test_server_api_and_guard(self):
        from urllib.error import HTTPError
        from urllib.request import Request
        srv = start_server(self.ws, '127.0.0.1', 0)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = 'http://127.0.0.1:%d' % srv.server_address[1]
        ok = {'Content-Type': 'application/json', 'X-Repomap': '1'}

        def post(ep, body, headers=ok):
            with urlopen(Request(base + '/api/' + ep, json.dumps(body).encode(), headers), timeout=10) as r:
                return json.load(r)
        try:
            self.assertIsNone(json.load(urlopen(base + '/api/tree'))['noEdit'])
            self.assertFalse(json.load(urlopen(base + '/api/insight?repo=apps/venc'))['script'])
            self.assertIn('cmake', post('script-create', {'repo': 'apps/venc'})['text'])
            self.assertTrue(json.load(urlopen(base + '/api/insight?repo=apps/venc'))['script'])
            self.assertEqual(post('script-save', {'repo': 'apps/venc', 'text': 'make\n'})['text'], 'make\n')
            self.assertEqual(json.load(urlopen(base + '/api/script?repo=apps/venc'))['text'], 'make\n')
            for headers, code in (({'Content-Type': 'application/json'}, 403),                  # thieu X-Repomap
                                  (dict(ok, Origin='http://evil.example'), 403),
                                  (dict(ok, Host='evil.example:80'), 403)):                   # DNS rebinding
                with self.assertRaises(HTTPError) as e:
                    post('script-save', {'repo': 'apps/venc', 'text': 'rm -rf /'}, headers)
                self.assertEqual(e.exception.code, code)
            with self.assertRaises(HTTPError) as e:
                post('script-save', {'repo': '../etc', 'text': 'x'})
            self.assertEqual(e.exception.code, 404)
            self.assertEqual(buildscript.read_script(self.ws, 'apps/venc')['text'], 'make\n')
        finally:
            srv.shutdown()
            srv.server_close()

    def test_guard_peer_uid_and_network_host(self):
        proc = os.path.join(self.tmp.name, 'tcp')
        with open(proc, 'w') as f:            # client 127.0.0.1:0xC350 (50000) -> server :0x1B39 (6969), uid 1234
            f.write('  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid\n'
                    '   0: 0100007F:1B39 0100007F:C350 01 00000000:00000000 00:00000000 00000000  1000\n'
                    '   1: 0100007F:C350 0100007F:1B39 01 00000000:00000000 00:00000000 00000000  1234\n')
        self.assertEqual(guard.peer_uid(50000, 6969, (proc,)), 1234)
        self.assertEqual(guard.peer_uid(50001, 6969, (proc,)), -1)            # doc duoc nhung khong thay
        self.assertIsNone(guard.peer_uid(50000, 6969, (proc + '.missing',)))

        class H:
            def __init__(self, host, headers):
                self.server = type('S', (), {'server_address': (host, 6969)})()
                self.headers, self.client_address = headers, ('127.0.0.1', 50000)
        good = {'Host': '127.0.0.1:7000', 'X-Repomap': '1'}             # port tren PC khac port server: van duoc
        self.assertIn('network', guard.write_denied(H('0.0.0.0', good), (proc,)))
        if hasattr(os, 'getuid'):
            expect = None if os.getuid() == 1234 else 'does not belong'
            res = guard.write_denied(H('127.0.0.1', good), (proc,))
            self.assertTrue(res is None if expect is None else expect in res)
        else:
            self.assertIsNone(guard.write_denied(H('127.0.0.1', good), (proc,)))


@unittest.skipUnless(BASH, 'needs bash')
class BuildRunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ['REPOMAP_BASH'] = BASH
        os.environ['REPOMAP_CACHE_DIR'] = os.path.join(self.tmp.name, 'cache')
        self.ws = Workspace(make_workspace(os.path.join(self.tmp.name, 'ws')), workers=4).load()
        self.runs = builds.Builds(self.ws)

    def tearDown(self):
        self.runs.stop_all()
        os.environ.pop('REPOMAP_BASH', None)
        for _ in range(30):                     # Windows: tien trinh vua bi dung con giu file log mot chut
            try:
                shutil.rmtree(self.tmp.name)
                break
            except OSError:
                time.sleep(0.2)
        with contextlib.suppress(OSError):      # thu muc da xoa o tren (Python 3.8 bao loi neu khong con)
            self.tmp.cleanup()

    def script(self, text, repo='apps/venc'):
        buildscript.save_script(self.ws, repo, text)
        return repo

    def wait(self, repo, timeout=20):
        end = time.time() + timeout
        while self.runs.status(repo)['state'] == 'running' and time.time() < end:
            time.sleep(0.1)
        return self.runs.status(repo)

    def test_run_log_and_exit_codes(self):
        repo = self.script('echo "hello from $(basename "$PWD")"\nfor i in 1 2 3; do echo line $i; done\n')
        self.assertEqual(self.runs.start(repo)['state'], 'running')
        st = self.wait(repo)
        self.assertEqual((st['state'], st['code']), ('done', 0))
        self.assertIn('hello from venc', st['log'])                       # chay tu thu muc module
        self.assertIn('line 3', st['log'])
        self.assertIn('[repomap] done, exit code 0', st['log'])
        tail = self.runs.status(repo, offset=st['next'])                  # doc tiep: khong con gi moi
        self.assertEqual(tail['log'], '')
        self.assertEqual(self.runs.status(repo, offset=-1)['log'], st['log'])    # -1 = phan cuoi
        self.script('echo boom\nexit 3\n')
        self.runs.start(repo)
        st = self.wait(repo)
        self.assertEqual((st['state'], st['code']), ('failed', 3))
        self.assertNotIn('line 3', st['log'])                             # log moi ghi de log cu
        self.assertEqual(self.runs.status('kernel/linux')['state'], 'idle')
        with self.assertRaisesRegex(ValueError, 'create it first'):
            self.runs.start('kernel/linux')

    def test_stop_kills_the_whole_build(self):
        pidfile = os.path.join(self.tmp.name, 'child.pid')
        # Windows (Git Bash / Cygwin): tien trinh nen khong nam trong cay tien trinh Windows -> chi thu lenh tien canh
        body = 'echo started\nsleep 60\n' if os.name == 'nt' else 'sleep 60 &\necho $! > "%s"\necho started\nwait\n'
        repo = self.script(body if os.name == 'nt' else body % pidfile)
        self.runs.start(repo)
        with self.assertRaisesRegex(ValueError, 'already running'):
            self.runs.start(repo)
        end = time.time() + 10
        while 'started' not in self.runs.status(repo)['log'] and time.time() < end:
            time.sleep(0.1)
        self.assertEqual(self.runs.stop(repo, wait=True)['state'], 'stopped')
        st = self.wait(repo)
        self.assertEqual(st['state'], 'stopped')
        if os.name != 'nt':                                               # tien trinh con (sleep) cung bi dung
            with open(pidfile) as f:
                child = int(f.read())
            time.sleep(0.3)
            with self.assertRaises(ProcessLookupError):
                os.kill(child, 0)

    def test_build_outlives_the_thread_that_started_it(self):
        # nhu luong xu ly request HTTP: bat dau build roi ket thuc ngay. Linux: PR_SET_PDEATHSIG gan voi luong tao
        # tien trinh con, nen neu tao build tu luong nay thi build bi giet ngay khi luong ket thuc
        repo = self.script('sleep 1\necho survived\n')
        t = threading.Thread(target=self.runs.start, args=(repo,))
        t.start()
        t.join()
        st = self.wait(repo)
        self.assertEqual((st['state'], st['code']), ('done', 0))
        self.assertIn('survived', st['log'])

    def test_stop_all_when_repomap_stops(self):
        repo = self.script('sleep 60\n')
        self.runs.start(repo)
        self.assertEqual(self.runs.stop_all(), [repo])
        self.assertEqual(self.wait(repo)['state'], 'stopped')
        self.assertEqual(self.runs.summary()[repo]['state'], 'stopped')

    def test_server_build_api(self):
        from urllib.error import HTTPError
        from urllib.request import Request
        self.script('echo via-api\n')
        srv = start_server(self.ws, '127.0.0.1', 0)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = 'http://127.0.0.1:%d/api/' % srv.server_address[1]
        try:
            body = json.dumps({'repo': 'apps/venc'}).encode()
            with self.assertRaises(HTTPError) as e:                       # thieu header X-Repomap
                urlopen(Request(base + 'build-start', body, {'Content-Type': 'application/json'}))
            self.assertEqual(e.exception.code, 403)
            req = Request(base + 'build-start', body, {'Content-Type': 'application/json', 'X-Repomap': '1'})
            self.assertEqual(json.load(urlopen(req))['state'], 'running')
            end = time.time() + 20
            while json.load(urlopen(base + 'builds'))['apps/venc']['state'] == 'running' and time.time() < end:
                time.sleep(0.1)
            st = json.load(urlopen(base + 'build?repo=apps/venc&from=0'))
            self.assertEqual(st['state'], 'done')
            self.assertIn('via-api', st['log'])
        finally:
            srv.builds.stop_all()
            srv.shutdown()
            srv.server_close()


class LocalTest(unittest.TestCase):
    CFG = {'host': 'h.vbee.lge.com', 'user': 'worker', 'port': '56614', 'srv': ''}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = os.environ.get('XDG_CONFIG_HOME')
        os.environ['XDG_CONFIG_HOME'] = self.tmp.name

    def tearDown(self):
        if self.env is None:
            os.environ.pop('XDG_CONFIG_HOME', None)
        else:
            os.environ['XDG_CONFIG_HOME'] = self.env
        self.tmp.cleanup()

    def test_check_cfg(self):
        self.assertEqual(local.check_cfg(self.CFG),
                         {'host': 'h.vbee.lge.com', 'user': 'worker', 'port': 56614, 'srv': 6969, 'dir': '', 'ws': ''})
        for bad, word in (({'host': ''}, 'host'), ({'user': 'a b'}, 'user'), ({'port': 'x'}, 'SSH port'),
                          ({'srv': '70000'}, 'local port'), ({'host': 'a&calc'}, 'host'),
                          ({'dir': 'a b'}, 'Server folder'), ({'dir': 'x;rm'}, 'Server folder'),
                          ({'dir': '../etc'}, 'Server folder'), ({'dir': '-x'}, 'Server folder'),
                          ({'ws': '/w s'}, 'Workspace root'), ({'ws': '$(id)'}, 'Workspace root'),
                          ({'ws': '~root/ws'}, 'Workspace root')):
            with self.assertRaisesRegex(ValueError, word):
                local.check_cfg(dict(self.CFG, **bad))

    def test_remote_dir(self):
        """Trong = ~/.bin; ten thu muc = canh ~/.bin (tao neu chua co); duong dan = phai co san."""
        rd = local.remote_dir
        self.assertEqual(rd(''), {'rel': '.bin', 'path': False, 'show': '~/.bin'})
        self.assertEqual(rd(' tools/ '), {'rel': 'tools', 'path': False, 'show': '~/tools'})
        self.assertEqual(rd('/opt/repomap/'), {'rel': '/opt/repomap', 'path': True, 'show': '/opt/repomap'})
        self.assertEqual(rd('~/work/bin'), {'rel': 'work/bin', 'path': True, 'show': '~/work/bin'})
        self.assertEqual(rd('work/bin'), {'rel': 'work/bin', 'path': True, 'show': '~/work/bin'})
        self.assertEqual(rd('~')['rel'], '.')
        for bad in ('~root/x', '.', 'a/../b', 'x y', '$(id)'):
            with self.assertRaises(ValueError):
                rd(bad)

    def test_commands_and_server_ini(self):
        cfg = local.check_cfg(self.CFG)
        self.assertEqual(local.tunnel_argv(cfg)[:4], ['ssh', '-N', '-L', '6969:127.0.0.1:6969'])
        self.assertEqual(local.tunnel_argv(cfg)[-1], 'worker@h.vbee.lge.com')
        run = local.tunnel_argv(cfg, local.remote_dir('tools'))        # Connect: tunnel + chay repomap.pyz luon
        self.assertEqual(run[:4], ['ssh', '-t', '-L', '6969:127.0.0.1:6969'])
        self.assertEqual(run[-2:], ['worker@h.vbee.lge.com', 'test -f tools/repomap.pyz || exit 3; '
                                    'exec python3 tools/repomap.pyz ~ --connect --port 6969'])   # trong = scan ~
        self.assertIn('"test -f tools/repomap.pyz || exit 3; exec', local.quote(run))
        ws = local.check_cfg(dict(self.CFG, ws='/home/worker/android13/'))
        self.assertTrue(local.tunnel_argv(ws, local.remote_dir(''))[-1].endswith(
            'exec python3 .bin/repomap.pyz /home/worker/android13 --connect --port 6969'))
        self.assertEqual(local.remote_ws('src/ws'), {'arg': 'src/ws', 'show': '~/src/ws', 'home': False})
        argv = local.deploy_argv(cfg, '/tmp/x/.bin')
        self.assertEqual(argv[:4], ['scp', '-r', '-P', '56614'])
        self.assertEqual(argv[-2:], ['/tmp/x/.bin', 'worker@h.vbee.lge.com:'])
        argv = local.deploy_argv(cfg, ['/t/repomap.pyz', '/t/repomap.ini'], '/opt/x')   # duong dan: phai co san
        self.assertEqual(argv, ['scp', '-P', '56614', '-o', 'ConnectTimeout=15', '/t/repomap.pyz', '/t/repomap.ini',
                                'worker@h.vbee.lge.com:/opt/x/'])
        path = os.path.join(self.tmp.name, 'repomap.ini')
        with open(path, 'w', encoding='utf-8') as f:
            f.write(local.server_ini(cfg))
        s = load_settings(path)
        self.assertEqual(s.tunnel_hint(s.port), 'ssh -N -L 6969:127.0.0.1:6969 -p 56614 worker@h.vbee.lge.com')
        self.assertEqual(s.workspace, '')                                   # trong: menu tren server tu hoi
        with open(path, 'w', encoding='utf-8') as f:
            f.write(local.server_ini(ws))
        self.assertEqual(load_settings(path).workspace, '/home/worker/android13')   # chay tay tren server cung dung

    def test_presets_max_and_overwrite(self):
        jobs = local.Jobs()
        for i in range(local.MAX_PRESETS):
            local.post(jobs, 'preset', dict(self.CFG, name='s%d' % i))
        local.post(jobs, 'preset', dict(self.CFG, name='s0', port='22', ws='~/ws0'))   # trung ten -> ghi de
        with self.assertRaisesRegex(ValueError, 'up to 5'):
            local.post(jobs, 'preset', dict(self.CFG, name='s9'))
        local.post(jobs, 'preset-del', {'name': 's1'})
        local.post(jobs, 'config', self.CFG)
        st = local.state(jobs)
        self.assertEqual([p['name'] for p in st['presets']], ['s2', 's3', 's4', 's0'])
        self.assertEqual(st['presets'][-1]['port'], 22)
        self.assertEqual(st['presets'][-1]['ws'], '~/ws0')                     # preset mang theo Workspace root
        self.assertEqual(st['config']['host'], 'h.vbee.lge.com')
        orig, jobs.push = jobs.push, lambda cfg: None
        try:                                              # Push / Connect nho gia tri vua dung (lan sau tu dien)
            local.post(jobs, 'deploy', dict(self.CFG, ws='/srv/ws', dir='tools'))
        finally:
            jobs.push = orig
        self.assertEqual((local.state(jobs)['config']['ws'], local.state(jobs)['config']['dir']), ('/srv/ws', 'tools'))
        self.assertEqual(local.post(jobs, 'quick', {'text': 'ssh -p 2 u@h'}),
                         {'host': 'h', 'user': 'u', 'port': 2, 'srv': 6969})

    def test_connect_refuses_busy_port(self):
        with socket.socket() as busy:
            busy.bind(('127.0.0.1', 0))
            busy.listen(16)
            port = busy.getsockname()[1]
            with self.assertRaisesRegex(ValueError, 'Port %d on this PC is already in use' % port):
                local.Jobs().connect(local.check_cfg(dict(self.CFG, srv=port)), open_browser=False)
            self.assertEqual(local.Jobs().check(port)['why'], 'noreply')         # port co nguoi nghe, khong phai repomap
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as e:
                cli.attach_running(port)                                   # tren server: --connect bao loi, ma 4
            self.assertEqual(e.exception.code, local.EXIT_PORT_TAKEN)
        self.assertFalse(cli.attach_running(port))                         # port trong: chay repomap moi

    def test_connect_once_disconnect_and_check(self):
        """Dang ket noi thi Connect lan nua bi tu choi (khong mo 2 cua so ssh); Disconnect dong tunnel."""
        class Alive:
            pid = 0

            def poll(self):
                return None
        jobs = local.Jobs()
        jobs.tunnel_proc = Alive()
        jobs.tunnel = {'state': 'up', 'host': 'h.vbee.lge.com'}
        with self.assertRaisesRegex(ValueError, 'Already connected'):
            jobs.connect(local.check_cfg(self.CFG), open_browser=False)
        killed = []
        orig, local.kill_tree = local.kill_tree, killed.append
        try:
            self.assertEqual(jobs.disconnect(), {'ok': True, 'was': 'up'})
        finally:
            local.kill_tree = orig
        self.assertEqual(len(killed), 1)
        self.assertEqual((jobs.tunnel_proc, jobs.tunnel['state']), (None, 'closed'))
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0))
            free = s.getsockname()[1]
        self.assertEqual(jobs.check(free), {'ok': False, 'why': 'noport', 'state': 'closed'})

    def test_repomap_answers_through_tunnel(self):
        """Check connection / cli --connect: /api/ping cua server repomap (user, workspace) qua port tunnel."""
        with tempfile.TemporaryDirectory() as tmp:
            ws = Workspace(make_workspace(tmp), workers=2).load()
            srv = start_server(ws, port=0, tries=1)
            port = srv.server_address[1]
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            try:
                info = local.Jobs().check(port)
                self.assertTrue(info['ok'])
                self.assertEqual((info['app'], info['root'], info['running']), ('repomap', ws.root, 0))
                self.assertEqual(info['user'], getpass.getuser())
                self.assertEqual(cli.ping(port)['pid'], os.getpid())
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as e:
                    cli.attach_running(port, os.path.join(tmp, 'src'))      # repomap cua minh, workspace khac
                self.assertEqual(e.exception.code, local.EXIT_OTHER_WS)
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as e:
                    cli.connect_workspace(parse_args([os.path.join(tmp, 'nope'), '--connect']), Settings())
                self.assertEqual(e.exception.code, local.EXIT_NO_WS)
                self.assertEqual(cli.connect_workspace(parse_args([tmp, '--connect']), Settings()), os.path.abspath(tmp))
            finally:
                srv.shutdown()
                srv.server_close()

    def test_http_only_from_own_page(self):
        from urllib.error import HTTPError
        from urllib.request import Request
        srv = local.Server(('127.0.0.1', 0), None)
        port = srv.server_address[1]
        srv.RequestHandlerClass = local.make_handler(local.Jobs(), port, False)[0]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            base = 'http://127.0.0.1:%d' % port
            with urlopen(base + '/') as r:
                self.assertIn(b'Saved servers', r.read())
            with urlopen(base + '/api/local/state') as r:
                self.assertEqual(json.load(r)['max'], 5)
            body = json.dumps(dict(self.CFG, name='a')).encode()
            ok = Request(base + '/api/local/preset', body, {'Content-Type': 'application/json', 'Origin': base})
            with urlopen(ok) as r:
                self.assertTrue(json.load(r)['ok'])
            for headers in ({'Content-Type': 'application/json', 'Origin': 'http://evil.example'},
                            {'Content-Type': 'text/plain'}):
                with self.assertRaises(HTTPError) as e:
                    urlopen(Request(base + '/api/local/preset', body, headers))
                self.assertEqual(e.exception.code, 403)
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == '__main__':
    unittest.main()

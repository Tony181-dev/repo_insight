import contextlib
import io
import json
import os
import socket
import struct
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
from repomap import local  # noqa: E402
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
        self.assertEqual(names('apps/venc#app'), {'venc'})                   # venc_test bo qua
        self.assertEqual(box['apps/venc#app']['comps'][0]['svc'], ['venc'])  # venc.service
        self.assertIn('venc_core', names('apps/venc#lib'))
        self.assertIn('libgk7205_backend', names('platform/hal/gk7205#hal'))  # thu muc hal/ -> tang HAL
        self.assertEqual(names('drivers/rtl8812eu#kernel'), {'8812eu'})      # obj-m, bo qua TARGET
        self.assertIn('spi-acme', names('qnx/drivers/spi#kernel'))
        self.assertIn('Linux kernel', names('kernel/linux#kernel'))
        self.assertEqual(names('device/acme/board#app'), {'acmed'})
        self.assertIn('libacme', names('device/acme/board#lib'))
        self.assertIn('acme-board', names('layers/meta-acme#board'))
        self.assertTrue({'pyapp', 'web', 'db'} <= names('tools/extra#app'))
        self.assertIn('GitLab CI', names('tools/extra#build'))
        self.assertTrue({'libusb-1.0', 'libmpi'} <= names('ext#c'))
        self.assertTrue({'liblog', 'libcutils'} <= names('ext#android'))
        edges = {(x, y, k) for x, y, _, k in a['edges']}
        self.assertIn(('apps/venc#app', 'platform/hal/gk7205#hal', 'use'), edges)
        self.assertIn(('apps/venc#app', 'apps/venc#lib', 'use'), edges)
        self.assertIn(('device/acme/board#app', 'device/acme/board#lib', 'use'), edges)
        self.assertIn(('layers/meta-acme#build', 'src/foo-app#app', 'build'), edges)   # SRC_URI -> repo
        self.assertEqual(a['errors'], [])
        slim = slim_arch(a)                                       # giao dien: ban gon + chi tiet khi bam khoi
        venc = next(b for b in slim['boxes'] if b['id'] == 'apps/venc#app')
        self.assertEqual(venc['comps'], [{'name': 'venc', 'type': 'exe', 'svc': 1}])
        self.assertEqual(venc['n'], {'svc': 1})
        self.assertEqual(box_detail(a, 'apps/venc#app')['comps'][0]['f'], 'CMakeLists.txt')

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
            busy.listen(1)
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
                         {'host': 'h.vbee.lge.com', 'user': 'worker', 'port': 56614, 'srv': 6969})
        for bad, word in (({'host': ''}, 'host'), ({'user': 'a b'}, 'user'), ({'port': 'x'}, 'SSH port'),
                          ({'srv': '70000'}, 'local port'), ({'host': 'a&calc'}, 'host')):
            with self.assertRaisesRegex(ValueError, word):
                local.check_cfg(dict(self.CFG, **bad))

    def test_commands_and_server_ini(self):
        cfg = local.check_cfg(self.CFG)
        self.assertEqual(local.tunnel_argv(cfg)[:4], ['ssh', '-N', '-L', '6969:127.0.0.1:6969'])
        self.assertEqual(local.tunnel_argv(cfg)[-1], 'worker@h.vbee.lge.com')
        argv = local.deploy_argv(cfg, '/tmp/x/.bin')
        self.assertEqual(argv[:4], ['scp', '-r', '-P', '56614'])
        self.assertEqual(argv[-2:], ['/tmp/x/.bin', 'worker@h.vbee.lge.com:'])
        path = os.path.join(self.tmp.name, 'repomap.ini')
        with open(path, 'w', encoding='utf-8') as f:
            f.write(local.server_ini(cfg))
        s = load_settings(path)
        self.assertEqual(s.tunnel_hint(s.port), 'ssh -N -L 6969:127.0.0.1:6969 -p 56614 worker@h.vbee.lge.com')

    def test_presets_max_and_overwrite(self):
        jobs = local.Jobs()
        for i in range(local.MAX_PRESETS):
            local.post(jobs, 'preset', dict(self.CFG, name='s%d' % i))
        local.post(jobs, 'preset', dict(self.CFG, name='s0', port='22'))         # trung ten -> ghi de
        with self.assertRaisesRegex(ValueError, 'up to 5'):
            local.post(jobs, 'preset', dict(self.CFG, name='s9'))
        local.post(jobs, 'preset-del', {'name': 's1'})
        local.post(jobs, 'config', self.CFG)
        st = local.state(jobs)
        self.assertEqual([p['name'] for p in st['presets']], ['s2', 's3', 's4', 's0'])
        self.assertEqual(st['presets'][-1]['port'], 22)
        self.assertEqual(st['config']['host'], 'h.vbee.lge.com')
        self.assertEqual(local.post(jobs, 'quick', {'text': 'ssh -p 2 u@h'}),
                         {'host': 'h', 'user': 'u', 'port': 2, 'srv': 6969})

    def test_connect_refuses_busy_port(self):
        with socket.socket() as busy:
            busy.bind(('127.0.0.1', 0))
            busy.listen(1)
            port = busy.getsockname()[1]
            with self.assertRaisesRegex(ValueError, 'Port %d on this PC is already in use' % port):
                local.Jobs().connect(local.check_cfg(dict(self.CFG, srv=port)), open_browser=False)

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

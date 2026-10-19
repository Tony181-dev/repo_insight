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
from repomap.cli import parse_args, resolve_settings  # noqa: E402
from repomap.settings import load_settings, write_template  # noqa: E402
from repomap.deepscan import deep_scan  # noqa: E402
from repomap.fileview import file_view, ver_match  # noqa: E402
from repomap.insight import insight  # noqa: E402
from repomap.parsers import bp_modules, parse_file, parse_vars  # noqa: E402
from repomap.server import start_server  # noqa: E402
from repomap.workspace import Workspace  # noqa: E402


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
            ins = json.loads(get('/api/insight?repo=kernel/linux'))
            self.assertTrue(ins['keys'])
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
        self.assertEqual((s.host, s.port, s.open_browser, s.ssh_local_port), ('127.0.0.1', 8800, 'auto', 0))
        self.assertEqual(s.source, self.path)

    def test_values_and_cli_override(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, 'w') as f:
            f.write('[server]\nport = 9100  # cong\nopen_browser = false\n[ssh]\nlocal_port = 18800\n'
                    'user = dev\nhost = build01\n')
        s = load_settings(self.path)
        self.assertEqual((s.port, s.open_browser), (9100, 'no'))
        self.assertEqual(s.tunnel_hint(9101), 'ssh -L 18800:127.0.0.1:9101 dev@build01')
        a = parse_args(['/ws', '--config', self.path, '--port', '9300', '--host', '0.0.0.0'])
        s = resolve_settings(a)
        self.assertEqual((s.host, s.port, s.ssh_user), ('0.0.0.0', 9300, 'dev'))

    def test_bad_value(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, 'w') as f:
            f.write('[server]\nport = abc\n')
        with self.assertRaises(SystemExit):
            load_settings(self.path)


if __name__ == '__main__':
    unittest.main()

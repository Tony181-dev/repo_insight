"""VM/root boundaries must affect both navigation data and dependency matching."""
import os
import tempfile
import unittest
from pathlib import Path

from repomap.arch import box_detail, build_arch, slim_arch
from repomap.workspace import Workspace


class ScopeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.old_cache = os.environ.get('REPOMAP_CACHE_DIR')
        os.environ['REPOMAP_CACHE_DIR'] = str(self.root / 'cache')

    def tearDown(self):
        if self.old_cache is None:
            os.environ.pop('REPOMAP_CACHE_DIR', None)
        else:
            os.environ['REPOMAP_CACHE_DIR'] = self.old_cache
        self.tmp.cleanup()

    def put(self, rel, text=''):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding='utf-8')

    def manifest(self, root, paths):
        self.put(root + '/.repo/manifest.xml', '<manifest>' + ''.join(
            '<project name="%s" path="%s"/>' % (p, p) for p in paths) + '</manifest>')

    def arch(self, root=None):
        ws = Workspace(str(root or self.root), workers=2).load()
        return ws, build_arch(ws, refresh=True)

    def test_single_android_includes_its_linux_kernel_and_generic_repos(self):
        self.manifest('guest', ['frameworks/base', 'kernel/linux', 'tools/host'])
        self.put('guest/frameworks/base/Android.bp', 'cc_binary { name: "app" }')
        self.put('guest/kernel/linux/Makefile', 'VERSION = 6\n')
        self.put('guest/kernel/linux/Kconfig')
        for d in ['arch', 'kernel', 'mm']:
            (self.root / 'guest/kernel/linux' / d).mkdir()
        self.put('guest/tools/host/CMakeLists.txt', 'add_executable(host main.c)')
        _, a = self.arch(self.root / 'guest')
        self.assertEqual([s['platform'] for s in a['scopes']], ['Android'])
        self.assertEqual(len({b['scope'] for b in a['boxes']}), 1)
        self.assertTrue(any(b['layer'] == 'kernel' for b in a['boxes']))

    def test_multiple_manifests_and_duplicate_os_roots(self):
        for root in ['guest-a', 'guest-b']:
            self.manifest(root, ['frameworks/base'])
            self.put(root + '/frameworks/base/Android.bp', 'cc_binary { name: "app" }')
        self.manifest('qnx', ['drivers/spi'])
        self.put('qnx/drivers/spi/Makefile', 'include recurse.mk\n')
        self.put('qnx/drivers/spi/common.mk', 'NAME=spi-test\ninclude qtargets.mk\n')
        ws, a = self.arch()
        self.assertEqual(len(ws.repos), 3)
        self.assertEqual(len(a['scopes']), 3)
        self.assertEqual(len({s['label'] for s in a['scopes']}), 3)
        self.assertEqual({p['scan_root'] for p in ws.repos}, {'guest-a', 'guest-b', 'qnx'})
        self.assertEqual(slim_arch(a)['scopes'], a['scopes'])
        self.assertTrue(all(b['scope'] for b in slim_arch(a)['boxes']))

    def test_two_plain_git_roots_stay_separate(self):
        for name in ['first', 'second']:
            (self.root / name / '.git').mkdir(parents=True)
            self.put(name + '/CMakeLists.txt', 'add_executable(app main.c)')
        _, a = self.arch()
        self.assertEqual({s['label'] for s in a['scopes']}, {'first', 'second'})

    def test_named_vm_folder_groups_git_repos(self):
        for path in ['android-vm/frameworks/base', 'android-vm/kernel', 'qnx-vm/drivers']:
            (self.root / path / '.git').mkdir(parents=True)
            self.put(path + '/CMakeLists.txt', 'add_executable(app main.c)')
        _, a = self.arch()
        self.assertEqual({s['platform']: s['repos'] for s in a['scopes']}, {'Android': 2, 'QNX': 1})

    def test_dependency_defaults_and_services_do_not_leak_between_vms(self):
        for root, dep in [('android-one', 'first'), ('android-two', 'second')]:
            self.manifest(root, ['app', 'lib'])
            self.put(root + '/app/Android.bp', 'cc_binary { name: "app", defaults: ["base"], shared_libs: ["common", "missing"] }')
            self.put(root + '/lib/Android.bp', 'cc_defaults { name: "base", shared_libs: ["%s"] }\n'
                     'cc_library { name: "common" }\ncc_library { name: "%s" }' % (dep, dep))
            self.put(root + '/app/app.service', '[Service]\nExecStart=/usr/bin/app\n')
        _, a = self.arch()
        for root, dep in [('android-one', 'first'), ('android-two', 'second')]:
            app = box_detail(a, root + '/app#app')['comps'][0]
            self.assertEqual(app['svc'], ['app'])
            self.assertIn(['common', root + '/lib#lib'], app['uses'])
            self.assertIn([dep, root + '/lib#lib'], app['uses'])
            self.assertFalse(any(other in name for name, _ in app['uses'] for other in
                                 (['second'] if dep == 'first' else ['first'])))
        external = [b for b in a['boxes'] if not b['repo']]
        self.assertEqual(len(external), 2)
        scopes = {b['id']: b['scope'] for b in a['boxes']}
        self.assertTrue(all(scopes[x] == scopes[y] for x, y, _, _ in a['edges']))

    def test_mixed_manifest_recognizes_three_os_scopes(self):
        self.manifest('mixed', ['android/app', 'qnx/app', 'linux/app'])
        for path in ['android/app', 'qnx/app', 'linux/app']:
            self.put('mixed/' + path + '/CMakeLists.txt', 'add_executable(app main.c)')
        _, a = self.arch(self.root / 'mixed')
        self.assertEqual({s['platform'] for s in a['scopes']}, {'Android', 'QNX', 'Linux'})

    def test_explicit_qnx_path_wins_over_android_build_files(self):
        self.manifest('mixed', ['android/app', 'qnx/portable'])
        for path in ['android/app', 'qnx/portable']:
            self.put('mixed/' + path + '/Android.bp', 'cc_library { name: "portable" }')
        _, a = self.arch(self.root / 'mixed')
        self.assertEqual({s['platform'] for s in a['scopes']}, {'Android', 'QNX'})


if __name__ == '__main__':
    unittest.main()

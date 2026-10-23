"""Exercise actual packaged processes, including the Windows double-click wrapper."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import zipapp

from repomap import local, runtime


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class LifecycleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='repomap lifecycle ')
        cls.root = Path(cls.temp.name)
        cls.package = cls.root / 'release with spaces & symbols'
        cls.package.mkdir()
        cls.pyz = str(cls.package / 'repomap.pyz')
        local.build_pyz(cls.pyz)
        shutil.copyfile(Path(__file__).resolve().parents[1] / 'tools/repomap-pc.cmd', cls.package / 'repomap-pc.cmd')

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.procs, self.servers = [], []
        self.env = dict(os.environ, XDG_CONFIG_HOME=str(self.root / 'config'),
                        REPOMAP_CACHE_DIR=str(self.root / 'cache'))

    def tearDown(self):
        for port, info, pc in self.servers:
            runtime.request_stop(port, info, local=pc)
        for proc in reversed(self.procs):
            if proc.poll() is None:
                local.kill_tree(proc)
            proc.wait(timeout=15)

    def spawn(self, argv):
        proc = subprocess.Popen(argv, env=self.env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                **({'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}))
        self.procs.append(proc)
        return proc

    def wait_info(self, port, pc=True, old_pid=None):
        end = time.monotonic() + 25
        while time.monotonic() < end:
            info = local.local_info(port) if pc else local.repomap_info(port, timeout=0.5)
            if info and info.get('pid') != old_pid and info.get('instance'):
                self.servers.append((port, info, pc))
                return info
            time.sleep(0.1)
        self.fail('Packaged server did not start/replace on port %d' % port)

    def test_fingerprint_matches_packaged_source(self):
        out = subprocess.check_output([sys.executable, '-c',
            'import sys; sys.path.insert(0, sys.argv[1]); from repomap.runtime import BUILD_ID; print(BUILD_ID)', self.pyz], text=True)
        self.assertEqual(out.strip(), runtime.BUILD_ID)

    def test_packaged_local_replaces_old_instance_on_same_port(self):
        port = free_port()
        old = self.spawn([sys.executable, self.pyz, '--local', '--port', str(port), '--no-browser'])
        first = self.wait_info(port)
        new = self.spawn([sys.executable, self.pyz, '--local', '--replace', '--port', str(port), '--no-browser'])
        second = self.wait_info(port, old_pid=first['pid'])
        old.wait(timeout=15)
        self.assertIsNone(new.poll())
        self.assertEqual(second['build'], runtime.BUILD_ID)
        self.assertNotEqual(first['instance'], second['instance'])

    def test_replace_legacy_without_shutdown_or_pid_endpoint(self):
        folder = self.root / 'legacy stub'
        folder.mkdir(exist_ok=True)
        (folder / '__main__.py').write_text(
            'import json, sys\nfrom http.server import BaseHTTPRequestHandler, HTTPServer\n'
            'class H(BaseHTTPRequestHandler):\n'
            ' def log_message(self,*a): pass\n'
            ' def do_GET(self):\n'
            '  self.send_response(200); self.end_headers(); self.wfile.write(b\'{"app":"repomap-local"}\')\n'
            ' def do_POST(self):\n'
            '  self.rfile.read(int(self.headers.get("Content-Length",0))); self.send_response(404); self.end_headers()\n'
            'HTTPServer(("127.0.0.1",int(sys.argv[sys.argv.index("--port")+1])),H).serve_forever()\n', encoding='utf-8')
        archive = self.root / 'legacy' / 'repomap.pyz'
        archive.parent.mkdir(exist_ok=True)
        zipapp.create_archive(str(folder), str(archive))
        port = free_port()
        old = self.spawn([sys.executable, str(archive), '--local', '--port', str(port)])
        end = time.monotonic() + 10
        while not local.already_running(port) and time.monotonic() < end:
            time.sleep(0.1)
        self.spawn([sys.executable, self.pyz, '--local', '--replace', '--port', str(port), '--no-browser'])
        self.assertEqual(self.wait_info(port)['build'], runtime.BUILD_ID)
        old.wait(timeout=15)

    def test_scanner_replace_shutdown_and_scoped_api_from_package(self):
        workspace = self.root / 'workspace'
        for name in ('android', 'qnx'):
            path = workspace / name
            (path / '.repo').mkdir(parents=True, exist_ok=True)
            (path / '.repo/manifest.xml').write_text('<manifest><project name="app"/></manifest>')
            (path / 'app').mkdir(exist_ok=True)
            (path / 'app/CMakeLists.txt').write_text('add_executable(app main.c)')
        port = free_port()
        argv = [sys.executable, self.pyz, str(workspace), '--connect', '--replace', '--port', str(port)]
        old = self.spawn(argv)
        first = self.wait_info(port, pc=False)
        self.spawn(argv)
        info = self.wait_info(port, pc=False, old_pid=first['pid'])
        old.wait(timeout=15)
        with urlopen('http://127.0.0.1:%d/api/arch' % port) as r:
            data = json.load(r)
        self.assertEqual({s['platform'] for s in data['scopes']}, {'Android', 'QNX'})
        # A stale page may not stop a replacement instance.
        req = Request('http://127.0.0.1:%d/api/shutdown' % port,
                      json.dumps({'instance': first['instance']}).encode(),
                      {'Content-Type': 'application/json', 'X-Repomap': '1'})
        with self.assertRaises(HTTPError) as err:
            urlopen(req)
        self.assertEqual(err.exception.code, 409)
        self.assertTrue(runtime.request_stop(port, info))
        self.assertTrue(runtime.wait_free(port))

    def test_never_stops_unrelated_listener(self):
        port = free_port()
        self.spawn([sys.executable, '-m', 'http.server', str(port), '--bind', '127.0.0.1'])
        end = time.monotonic() + 5
        while not local.port_busy(port) and time.monotonic() < end:
            time.sleep(0.1)
        with self.assertRaises(ValueError):
            runtime.stop_listener(port, ('local', 'scanner', 'tunnel'))
        self.assertTrue(local.port_busy(port))

    def test_stale_remote_build_never_opens_browser(self):
        class Proc:
            returncode = None
            def poll(self): return None
        jobs, proc = local.Jobs(), Proc()
        jobs.tunnel_proc = proc
        with tempfile.TemporaryDirectory() as folder, patch.object(local, 'port_busy', return_value=True), \
                patch.object(local, 'repomap_info', return_value={'build': 'old'}), \
                patch.object(local, 'kill_tree') as kill, patch.object(local.webbrowser, 'open') as browser:
            jobs._watch_tunnel(proc, 6969, 'http://127.0.0.1:6969/', folder, True)
        self.assertEqual(jobs.tunnel['state'], 'stale')
        kill.assert_called_once_with(proc)
        browser.assert_not_called()

    @unittest.skipUnless(os.name == 'nt', 'Windows wrapper')
    def test_actual_cmd_wrapper_replaces_instance(self):
        port = free_port()
        command = 'cmd.exe /d /s /c ""%s" --port %d --no-browser"' % (self.package / 'repomap-pc.cmd', port)
        first_cmd = self.spawn(command)
        first_cmd.wait(timeout=15)
        self.assertEqual(first_cmd.returncode, 0)
        first = self.wait_info(port)
        second_cmd = self.spawn(command)
        second_cmd.wait(timeout=15)
        second = self.wait_info(port, old_pid=first['pid'])
        self.assertEqual(os.path.normcase(second['artifact']), os.path.normcase(self.pyz))
        self.assertEqual(second['build'], runtime.BUILD_ID)

    @unittest.skipUnless(os.name == 'nt', 'Windows console process tree')
    def test_disconnect_stops_scanner_console_and_releases_port(self):
        port = free_port()
        ws = self.root / 'single workspace'
        (ws / '.git').mkdir(parents=True, exist_ok=True)
        (ws / 'CMakeLists.txt').write_text('add_executable(app main.c)')
        with tempfile.TemporaryDirectory(prefix='repomap console & ') as folder:
            proc = local.open_console('repomap test', [sys.executable, self.pyz, str(ws),
                                      '--connect', '--replace', '--port', str(port)], 'Stopped.', folder, visible=False)
            self.procs.append(proc)
            info = self.wait_info(port, pc=False)
            jobs = local.Jobs()
            jobs.tunnel_proc = proc
            jobs.tunnel = dict(info, state='up', port=port)
            self.assertEqual(jobs.disconnect(), {'ok': True, 'was': 'up'})
            self.assertEqual(jobs.tunnel['state'], 'closed')
            self.assertIsNotNone(proc.poll())
            self.assertFalse(local.port_busy(port))


if __name__ == '__main__':
    unittest.main()

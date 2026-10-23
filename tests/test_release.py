import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import make_release
from repomap.runtime import BUILD_ID


class ZipReleaseTest(unittest.TestCase):
    def test_one_zip_replaces_only_our_old_archives(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in ['repomap-0.9.zip', 'repomap-1.0.0.tar.gz', 'unrelated.zip']:
                (root / name).write_bytes(b'old')
            with patch.object(make_release, 'DIST', folder), patch.object(sys, 'argv', ['make_release.py']), \
                    contextlib.redirect_stdout(io.StringIO()):
                make_release.main()
            self.assertEqual({p.name for p in root.iterdir()}, {'repomap-1.0.0.zip', 'unrelated.zip'})
            with zipfile.ZipFile(root / 'repomap-1.0.0.zip') as z:
                build = json.loads(z.read('repomap-1.0.0/BUILD.json'))
                self.assertEqual(build['build'], BUILD_ID)
                self.assertIn(b'--local --replace', z.read('repomap-1.0.0/repomap-pc.cmd'))
                pyz = z.read('repomap-1.0.0/repomap.pyz')
                with zipfile.ZipFile(io.BytesIO(pyz)) as package:
                    self.assertIn('repomap/arch_scope.py', package.namelist())
                    self.assertIn(b'--session', package.read('repomap/cli.py'))

    def test_failed_release_preserves_existing_archive(self):
        with tempfile.TemporaryDirectory() as folder:
            old = Path(folder) / 'repomap-1.0.0.zip'
            old.write_bytes(b'previous good release')
            with patch.object(make_release, 'DIST', folder), patch.object(sys, 'argv', ['make_release.py']), \
                    patch.object(make_release, 'write_zip', side_effect=OSError('disk full')):
                with self.assertRaises(OSError):
                    make_release.main()
            self.assertEqual(old.read_bytes(), b'previous good release')


if __name__ == '__main__':
    unittest.main()

"""Dong goi repomap thanh 1 file chay duoc: dist/repomap.pyz (chay tren Windows hoac Linux deu ra cung ket qua).

  python tools/build_pyz.py            # -> dist/repomap.pyz
  python tools/build_pyz.py --test     # chay unit test truoc khi dong goi

File .pyz la file zip co dong shebang `#!/usr/bin/env python3`: tren Linux chay bang
`python3 repomap.pyz ...` hoac `chmod +x repomap.pyz && ./repomap.pyz ...`.
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import zipapp

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.join(ROOT, 'repomap')
DIST = os.path.join(ROOT, 'dist')


def version():
    ns = {}
    with open(os.path.join(PKG, '__init__.py'), encoding='utf-8') as f:
        exec(f.read(), ns)
    return ns['__version__']


def run_tests():
    print('chay unit test...')
    r = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests'], cwd=ROOT)
    if r.returncode:
        sys.exit('unit test loi, khong dong goi')


def build(out):
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        shutil.copytree(PKG, os.path.join(tmp, 'repomap'), ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        zipapp.create_archive(tmp, out, interpreter='/usr/bin/env python3', main='repomap.cli:main', compressed=True)
    return out


def main():
    ap = argparse.ArgumentParser(description='Dong goi repomap thanh dist/repomap.pyz')
    ap.add_argument('--test', action='store_true', help='chay unit test truoc')
    ap.add_argument('-o', '--output', default=os.path.join(DIST, 'repomap.pyz'))
    a = ap.parse_args()
    if a.test:
        run_tests()
    out = build(os.path.abspath(a.output))
    print('da tao: %s  (repomap %s, %.0f KB)' % (out, version(), os.path.getsize(out) / 1024))


if __name__ == '__main__':
    main()

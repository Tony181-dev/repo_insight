"""Tao goi phat hanh de dua cho nguoi dung (khong kem repo ma nguon):

  dist/repomap-<version>.tar.gz   (cho Linux: tar xzf ... && sh install.sh)
  dist/repomap-<version>.zip      (cung noi dung, de mo tren Windows)

Noi dung goi:
  repomap.pyz      tool (1 file, chay bang python3)
  install.sh       cai vao ~/.local/bin/repomap, khong can root
  repomap-pc.cmd   tren PC Windows: nhap dup -> Connect (co Deploy) / Saved servers
  README.txt       huong dan nhanh
  HUONG_DAN.html   huong dan day du (+ app.png)

  python tools/make_release.py [--test]
"""
import argparse
import io
import os
import re
import sys
import tarfile
import tempfile
import time
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_pyz import DIST, ROOT, build, run_tests, version  # noqa: E402

TOOLS = os.path.join(ROOT, 'tools')
DOCS = os.path.join(ROOT, 'docs')


def read(path):
    with open(path, 'rb') as f:
        return f.read()


def lf(data):
    return data.replace(b'\r\n', b'\n')


def crlf(data):
    return lf(data).replace(b'\n', b'\r\n')


MAINTAINER = re.compile(rb'<!-- maintainer:start.*?<!-- maintainer:end -->\n?', re.S)


def user_guide():
    """docs/index.html bo cac doan chi danh cho nguoi giu ma nguon (danh dau maintainer:start/end)."""
    html = MAINTAINER.sub(b'', read(os.path.join(DOCS, 'index.html')))
    if b'maintainer:' in html or b'id="dong-goi"' in html:
        raise SystemExit('docs/index.html: danh dau maintainer:start/end khong khop')
    return html


def release_files(ver):
    """[(ten trong goi, du lieu bytes, mode)]"""
    with tempfile.TemporaryDirectory() as tmp:
        pyz = build(os.path.join(tmp, 'repomap.pyz'))
        files = [('repomap.pyz', read(pyz), 0o755)]
    files += [
        ('install.sh', lf(read(os.path.join(TOOLS, 'install.sh'))), 0o755),
        ('repomap-pc.cmd', crlf(read(os.path.join(TOOLS, 'repomap-pc.cmd'))), 0o644),
        ('README.txt', lf(read(os.path.join(TOOLS, 'release_readme.txt'))).replace(b'{version}', ver.encode()), 0o644),
        ('HUONG_DAN.html', user_guide(), 0o644),
        ('app.png', read(os.path.join(DOCS, 'app.png')), 0o644),
    ]
    return files


def write_tar(path, top, files):
    now = time.time()
    with tarfile.open(path, 'w:gz') as tf:
        d = tarfile.TarInfo(top)
        d.type, d.mode, d.mtime = tarfile.DIRTYPE, 0o755, now
        tf.addfile(d)
        for name, data, mode in files:
            ti = tarfile.TarInfo('%s/%s' % (top, name))
            ti.size, ti.mode, ti.mtime = len(data), mode, now
            tf.addfile(ti, io.BytesIO(data))


def write_zip(path, top, files):
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for name, data, mode in files:
            zi = zipfile.ZipInfo('%s/%s' % (top, name), time.localtime()[:6])
            zi.create_system = 3                       # unix -> giu quyen thuc thi khi unzip tren Linux
            zi.external_attr = (0o100000 | mode) << 16
            zi.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(zi, data)


def main():
    ap = argparse.ArgumentParser(description='Tao goi phat hanh repomap trong dist/')
    ap.add_argument('--test', action='store_true', help='chay unit test truoc')
    a = ap.parse_args()
    if a.test:
        run_tests()
    ver = version()
    top = 'repomap-' + ver
    files = release_files(ver)
    os.makedirs(DIST, exist_ok=True)
    tgz, zp = os.path.join(DIST, top + '.tar.gz'), os.path.join(DIST, top + '.zip')
    write_tar(tgz, top, files)
    write_zip(zp, top, files)
    print('noi dung goi: ' + ', '.join(n for n, _, _ in files))
    for p in (tgz, zp):
        print('da tao: %s  (%.0f KB)' % (p, os.path.getsize(p) / 1024))


if __name__ == '__main__':
    main()

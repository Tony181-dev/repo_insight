"""Cau hinh (file INI). Thu tu uu tien: tham so dong lenh > file cau hinh > mac dinh.

File duoc tim theo thu tu (lay file dau tien ton tai):
  --config PATH  >  $REPOMAP_CONFIG  >  ./repomap.ini  >  $XDG_CONFIG_HOME/repomap/config.ini (~/.config/...)
Tao file mau:  repomap --init-config
"""
import configparser
import getpass
import os
import socket
from dataclasses import dataclass, fields

TEMPLATE = """\
# repomap - file cau hinh. Tham so dong lenh (--port, --host...) se ghi de cac gia tri nay.

[server]
# dia chi server nghe. 127.0.0.1 = chi may nay (an toan, dung kem ssh tunnel);
# 0.0.0.0 = mo cho ca mang LAN (khong co xac thuc, can than).
host = 127.0.0.1
# cong server; neu dang ban se thu tiep port+1, port+2... toi da port_tries lan
port = 8800
port_tries = 20
# auto = chi mo trinh duyet khi co man hinh (DISPLAY/WAYLAND_DISPLAY); yes / no
open_browser = auto

[ssh]
# Dung de in san lenh tunnel:  ssh -L <local_port>:127.0.0.1:<port> <user>@<host>
# local_port: cong tren may ban (de trong = giong port cua server)
local_port =
# user / host: tai khoan va ten may build khi ssh vao (de trong = user va hostname hien tai)
user =
host =

[scan]
# so luong thread phan loai repo luc khoi dong (0 = tu chon theo so CPU)
workers = 0
"""


@dataclass
class Settings:
    host: str = '127.0.0.1'
    port: int = 8800
    port_tries: int = 20
    open_browser: str = 'auto'      # auto | yes | no
    workers: int = 0                # 0 = tu chon
    ssh_local_port: int = 0         # 0 = bang port that cua server
    ssh_user: str = ''
    ssh_host: str = ''
    source: str = ''                # file cau hinh da doc ('' = mac dinh)

    def resolved_workers(self):
        return self.workers or min(32, (os.cpu_count() or 4) * 2)

    def should_open_browser(self):
        if self.open_browser == 'auto':
            return os.name == 'nt' or bool(os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY'))
        return self.open_browser == 'yes'

    def tunnel_hint(self, port):
        """Lenh ssh tunnel de mo giao dien tu may khac, theo port server dang chay that."""
        user = self.ssh_user or getpass.getuser()
        host = self.ssh_host or socket.gethostname()
        return 'ssh -L %d:127.0.0.1:%d %s@%s' % (self.ssh_local_port or port, port, user, host)


# (section, key) trong file INI -> ten truong trong Settings
KEYS = {
    ('server', 'host'): 'host',
    ('server', 'port'): 'port',
    ('server', 'port_tries'): 'port_tries',
    ('server', 'open_browser'): 'open_browser',
    ('ssh', 'local_port'): 'ssh_local_port',
    ('ssh', 'user'): 'ssh_user',
    ('ssh', 'host'): 'ssh_host',
    ('scan', 'workers'): 'workers',
}
BOOL_WORDS = {'1': 'yes', 'true': 'yes', 'on': 'yes', 'yes': 'yes',
              '0': 'no', 'false': 'no', 'off': 'no', 'no': 'no', 'auto': 'auto'}


def default_path():
    base = os.environ.get('XDG_CONFIG_HOME') or os.path.join(os.path.expanduser('~'), '.config')
    return os.path.join(base, 'repomap', 'config.ini')


def find_config(explicit=None):
    if explicit:
        if not os.path.isfile(explicit):
            raise SystemExit('khong thay file cau hinh: ' + explicit)
        return explicit
    for p in (os.environ.get('REPOMAP_CONFIG'), 'repomap.ini', default_path()):
        if p and os.path.isfile(p):
            return p
    return None


def load_settings(explicit=None):
    s = Settings()
    path = find_config(explicit)
    if not path:
        return s
    cp = configparser.ConfigParser(inline_comment_prefixes=('#', ';'))
    try:
        cp.read(path, encoding='utf-8')
    except configparser.Error as e:
        raise SystemExit('loi file cau hinh %s: %s' % (path, e))
    types = {f.name: f.type for f in fields(Settings)}
    for (sec, key), attr in KEYS.items():
        raw = cp.get(sec, key, fallback='').strip()
        if not raw:
            continue
        try:
            if attr == 'open_browser':
                value = BOOL_WORDS[raw.lower()]
            elif types[attr] in (int, 'int'):
                value = int(raw)
            else:
                value = raw
        except (KeyError, ValueError):
            raise SystemExit('gia tri khong hop le trong %s: [%s] %s = %s' % (path, sec, key, raw))
        setattr(s, attr, value)
    s.source = path
    return s


def write_template(path=None):
    path = path or default_path()
    if os.path.exists(path):
        raise SystemExit('file da ton tai, khong ghi de: ' + path)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(TEMPLATE)
    return path

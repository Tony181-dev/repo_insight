"""Cau hinh (file INI). Thu tu uu tien: tham so dong lenh > file cau hinh > mac dinh.

File duoc tim theo thu tu (lay file dau tien ton tai):
  --config PATH  >  $REPOMAP_CONFIG  >  ./repomap.ini  >  repomap.ini canh repomap.pyz
  >  $XDG_CONFIG_HOME/repomap/config.ini (~/.config/...)
Tao/sua file: menu "2. Configuration" (luu lai de lan sau khong phai nhap), hoac  repomap --init-config
"""
import configparser
import getpass
import glob
import os
import re
import shlex
import socket
from dataclasses import dataclass, fields

# {ten} duoc thay bang gia tri hien tai khi ghi file; gia tri 0 cua so nguyen ghi thanh rong (= tu dong)
TEMPLATE = """\
# repomap configuration. Command line options (--port, --host, ...) override these values.
# Edit by hand or with the menu: repomap -> 2. Configuration

[server]
# Address the server listens on. 127.0.0.1 = this machine only (safe, use with an ssh tunnel);
# 0.0.0.0 = whole network (no authentication, be careful).
host = {host}
# Server port; if it is busy, port+1, port+2... are tried, up to port_tries times.
port = {port}
port_tries = {port_tries}
# auto = open a browser only when a display is available (DISPLAY/WAYLAND_DISPLAY); yes / no
open_browser = {open_browser}

[ssh]
# Used to print the tunnel command to run on your PC:
#   ssh -N -L <local_port>:127.0.0.1:<server port> [-p <port>] <user>@<host>
# host: name or IP of this machine as seen from your PC (empty = full host name of this machine)
host = {ssh_host}
# user: your ssh account on this machine (empty = current user)
user = {ssh_user}
# port: ssh port of this machine (empty = read from /etc/ssh/sshd_config, default 22)
port = {ssh_port}
# local_port: port on your PC (empty = same as the server port)
local_port = {ssh_local_port}

[scan]
# Last workspace scanned (the folder that contains .repo/); offered as default in the menu.
workspace = {workspace}
# Threads used to classify repos at startup (0 = based on CPU count)
workers = {workers}
"""


DEFAULT_PORT = 6969


@dataclass
class Settings:
    host: str = '127.0.0.1'
    port: int = DEFAULT_PORT
    port_tries: int = 20
    open_browser: str = 'auto'      # auto | yes | no
    workers: int = 0                # 0 = tu chon
    ssh_host: str = ''              # '' = FQDN cua may nay
    ssh_user: str = ''              # '' = user hien tai
    ssh_port: int = 0               # 0 = doc tu sshd_config
    ssh_local_port: int = 0         # 0 = bang port that cua server
    workspace: str = ''             # workspace quet lan truoc
    source: str = ''                # file cau hinh da doc ('' = mac dinh)

    def resolved_workers(self):
        return self.workers or min(32, (os.cpu_count() or 4) * 2)

    def should_open_browser(self):
        if self.open_browser == 'auto':
            return os.name == 'nt' or bool(os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY'))
        return self.open_browser == 'yes'

    def resolved_ssh(self):
        """(user, host, port) dung cho lenh tunnel."""
        port = self.ssh_port or detect_ssh_port()[0]
        return self.ssh_user or getpass.getuser(), self.ssh_host or default_ssh_host(), port

    def tunnel_hint(self, port):
        """Lenh ssh tunnel chay tren PC de mo giao dien, theo port server dang chay that."""
        user, host, ssh_port = self.resolved_ssh()
        extra = ' -p %d' % ssh_port if ssh_port != 22 else ''
        return 'ssh -N -L %d:127.0.0.1:%d%s %s@%s' % (self.ssh_local_port or port, port, extra, user, host)


# (section, key) trong file INI -> ten truong trong Settings
KEYS = {
    ('server', 'host'): 'host',
    ('server', 'port'): 'port',
    ('server', 'port_tries'): 'port_tries',
    ('server', 'open_browser'): 'open_browser',
    ('ssh', 'host'): 'ssh_host',
    ('ssh', 'user'): 'ssh_user',
    ('ssh', 'port'): 'ssh_port',
    ('ssh', 'local_port'): 'ssh_local_port',
    ('scan', 'workspace'): 'workspace',
    ('scan', 'workers'): 'workers',
}
BOOL_WORDS = {'1': 'yes', 'true': 'yes', 'on': 'yes', 'yes': 'yes',
              '0': 'no', 'false': 'no', 'off': 'no', 'no': 'no', 'auto': 'auto'}
PORT_FIELDS = {'port', 'ssh_port', 'ssh_local_port'}


# ---------------------------------------------------------------- tu dong do

def default_ssh_host():
    """Ten day du (FQDN) cua may nay - ten ma PC thuong dung de ssh vao."""
    fqdn = socket.getfqdn()
    if not fqdn or fqdn.startswith('localhost'):
        fqdn = socket.gethostname()
    return fqdn


def parse_sshd_ports(text):
    """Cac dong 'Port N' trong sshd_config (bo qua comment)."""
    return [int(m.group(1)) for m in re.finditer(r'^\s*Port\s+(\d+)\s*$', text, re.M | re.I)]


QUICK_HELP = 'Please copy the SSH command for your vbee server and paste it here.'
QUICK_EXAMPLE = 'ssh -p <ssh_port> <user>@<host>.vbee.lge.com'


def parse_ssh_command(text):
    """'ssh -p 56614 worker@host.vbee.lge.com' -> ('worker', 'host.vbee.lge.com', 56614).
    -p o vi tri nao cung duoc, thieu -p = 22; ValueError neu khong doc duoc."""
    try:
        words = shlex.split(text.strip().lstrip('$> '))
    except ValueError:
        words = text.split()
    if words and os.path.basename(words[0]).lower() in ('ssh', 'ssh.exe'):
        words = words[1:]
    port, user, host, i = 22, '', '', 0
    while i < len(words):
        w = words[i]
        if w in ('-p', '-l') and i + 1 < len(words):
            if w == '-p':
                port = check_port(words[i + 1])
            else:
                user = words[i + 1]
            i += 2
            continue
        if w.startswith('-p') and w[2:].isdigit():
            port = check_port(w[2:])
        elif not w.startswith('-') and not host:
            user, _, host = w.rpartition('@') if '@' in w else (user, '', w)
        i += 1
    if not (user and host) or not re.match(r'^[\w.-]+$', user) or not re.match(r'^[A-Za-z0-9.-]+$', host):
        raise ValueError('expected something like: ' + QUICK_EXAMPLE)
    return user, host, port


def port_busy(port, host='127.0.0.1'):
    """Da co chuong trinh nghe o port nay tren may nay chua."""
    try:
        with socket.create_connection((host, port), timeout=0.3):
            return True
    except OSError:
        return False


def detect_ssh_port(config='/etc/ssh/sshd_config'):
    """(port, cach tim ra). Doc sshd_config (+ sshd_config.d/*.conf), uu tien port dang mo tren may nay."""
    ports = []
    for path in [config] + sorted(glob.glob(os.path.join(os.path.dirname(config), 'sshd_config.d', '*.conf'))):
        try:
            with open(path, encoding='utf-8', errors='replace') as f:
                ports += [p for p in parse_sshd_ports(f.read()) if p not in ports]
        except OSError:
            continue
    if not ports:
        return 22, 'default'
    for p in ports:
        if port_busy(p):
            return p, 'sshd_config, listening'
    return ports[0], 'sshd_config'


# ---------------------------------------------------------------- doc / ghi

def default_path():
    base = os.environ.get('XDG_CONFIG_HOME') or os.path.join(os.path.expanduser('~'), '.config')
    return os.path.join(base, 'repomap', 'config.ini')


def app_archive():
    """Duong dan repomap.pyz dang chay (None neu chay tu source)."""
    archive = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return archive if os.path.isfile(archive) else None


def find_config(explicit=None):
    if explicit:
        if not os.path.isfile(explicit):
            raise SystemExit('config file not found: ' + explicit)
        return explicit
    archive = app_archive()
    for p in (os.environ.get('REPOMAP_CONFIG'), 'repomap.ini',
              archive and os.path.join(os.path.dirname(archive), 'repomap.ini'), default_path()):
        if p and os.path.isfile(p):
            return p
    return None


def check_port(value):
    """'6969' -> 6969; bao loi ValueError neu khong phai port hop le."""
    try:
        port = int(str(value).strip())
    except ValueError:
        raise ValueError('not a number: %s' % value)
    if not 1 <= port <= 65535:
        raise ValueError('port must be between 1 and 65535: %s' % value)
    return port


def load_settings(explicit=None):
    s = Settings()
    path = find_config(explicit)
    if not path:
        return s
    cp = configparser.ConfigParser(inline_comment_prefixes=('#', ';'), interpolation=None)
    try:
        cp.read(path, encoding='utf-8')
    except configparser.Error as e:
        raise SystemExit('invalid config file %s: %s' % (path, e))
    types = {f.name: f.type for f in fields(Settings)}
    for (sec, key), attr in KEYS.items():
        raw = cp.get(sec, key, fallback='').strip()
        if not raw:
            continue
        try:
            if attr == 'open_browser':
                value = BOOL_WORDS[raw.lower()]
            elif attr in PORT_FIELDS:
                value = check_port(raw)
            elif types[attr] in (int, 'int'):
                value = int(raw)
            else:
                value = raw
        except (KeyError, ValueError):
            raise SystemExit('invalid value in %s: [%s] %s = %s' % (path, sec, key, raw))
        setattr(s, attr, value)
    s.source = path
    return s


def render(s):
    values = {f.name: getattr(s, f.name) for f in fields(Settings)}
    for k, v in values.items():
        if isinstance(v, int) and v == 0 and k != 'workers':
            values[k] = ''
    return TEMPLATE.format(**values)


def save_settings(s, path=None):
    """Ghi cau hinh (giu chu thich) vao file dang dung, hoac ~/.config/repomap/config.ini."""
    path = path or s.source or default_path()
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(render(s))
    s.source = path
    return path


def write_template(path=None):
    path = path or default_path()
    if os.path.exists(path):
        raise SystemExit('file already exists, not overwriting: ' + path)
    return save_settings(Settings(), path)

"""Build/instance identity and replacement of verified, same-user listeners."""
import getpass
import hashlib
import json
import os
import re
import secrets
import signal
import subprocess
import time
import zipfile
from urllib.request import Request, urlopen

from .settings import app_archive, port_busy


def build_id():
    """Same fingerprint for source and its pyz, independent of ZIP timestamps."""
    archive = app_archive()
    digest = hashlib.sha256()
    if archive:
        with zipfile.ZipFile(archive) as z:
            files = [(n[len('repomap/'):], z.read(n)) for n in sorted(z.namelist())
                     if n.startswith('repomap/') and n.endswith(('.py', '.js', '.css', '.html'))]
    else:
        base = os.path.dirname(__file__)
        files = []
        for dp, dirs, names in os.walk(base):
            dirs[:] = [d for d in dirs if d != '__pycache__']
            for name in names:
                if name.endswith(('.py', '.js', '.css', '.html')):
                    path = os.path.join(dp, name)
                    with open(path, 'rb') as f:
                        files.append((os.path.relpath(path, base).replace(os.sep, '/'), f.read()))
    for name, data in sorted(files):
        digest.update(name.encode() + b'\0' + data.replace(b'\r\n', b'\n') + b'\0')
    return digest.hexdigest()[:16]


BUILD_ID = build_id()                 # freeze before a deployed pyz is overwritten
INSTANCE = secrets.token_urlsafe(24)
SESSION = ''


def identity():
    return {'build': BUILD_ID, 'instance': INSTANCE, 'session': SESSION, 'pid': os.getpid(), 'user': getpass.getuser(),
            'artifact': app_archive() or os.path.dirname(os.path.dirname(__file__))}


def request_stop(port, info, local=False):
    """Ask precisely the observed instance to stop (also works through SSH)."""
    if not info.get('instance'):
        return False
    path = '/api/local/shutdown' if local else '/api/shutdown'
    req = Request('http://127.0.0.1:%d%s' % (port, path),
                  json.dumps({'instance': info['instance']}).encode(),
                  {'Content-Type': 'application/json', 'X-Repomap': '1'})
    try:
        with urlopen(req, timeout=15) as r:
            return json.load(r).get('ok', False)
    except Exception:  # noqa: BLE001 - legacy server, already closed, or refused
        return False


def listener_pid(port):
    if os.name == 'nt':
        out = subprocess.run(['netstat', '-ano', '-p', 'tcp'], capture_output=True, text=True,
                             creationflags=subprocess.CREATE_NO_WINDOW, timeout=10).stdout
        for line in out.splitlines():
            row = line.split()
            if (len(row) == 5 and row[0] == 'TCP' and row[3] == 'LISTENING'
                    and row[1].rsplit(':', 1)[-1] == str(port)
                    and row[1].rsplit(':', 1)[0] in ('127.0.0.1', '0.0.0.0', '[::]', '[::1]')):
                return int(row[4])
    elif os.path.isdir('/proc'):
        inodes = set()
        for name in ('tcp', 'tcp6'):
            try:
                with open('/proc/net/' + name) as f:
                    for line in f.read().splitlines()[1:]:
                        row = line.split()
                        if int(row[1].rsplit(':', 1)[1], 16) == port and row[3] == '0A':
                            inodes.add('socket:[%s]' % row[9])
            except OSError:
                pass
        for pid in os.listdir('/proc'):
            if not pid.isdigit():
                continue
            try:
                for fd in os.listdir('/proc/%s/fd' % pid):
                    if os.readlink('/proc/%s/fd/%s' % (pid, fd)) in inodes:
                        return int(pid)
            except OSError:
                continue
    return None


def process_info(pid):
    if os.name == 'nt':
        script = ("$p=Get-CimInstance Win32_Process -Filter 'ProcessId = %d'; "
                  "if ($p) { $o=Invoke-CimMethod -InputObject $p -MethodName GetOwner; "
                  "@{cmd=$p.CommandLine; user=$o.User} | ConvertTo-Json -Compress }") % pid
        r = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                           capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW, timeout=15)
        return json.loads(r.stdout or '{}')
    try:
        if os.stat('/proc/%d' % pid).st_uid != os.getuid():
            return {}
        with open('/proc/%d/cmdline' % pid, 'rb') as f:
            return {'cmd': f.read().replace(b'\0', b' ').decode(errors='replace'), 'user': getpass.getuser()}
    except OSError:
        return {}


def listener_kind(port, allowed):
    """Never interpret a remote /api/ping PID as a local PID or kill by image name."""
    pid = listener_pid(port)
    if not pid or pid == os.getpid():
        raise ValueError('Cannot safely identify the old listener on port %d.' % port)
    info = process_info(pid)
    cmd = info.get('cmd') or ''
    if (info.get('user') or '').lower() != getpass.getuser().lower():
        raise ValueError('Port %d belongs to another user; it was not stopped.' % port)
    repomap = re.search(r'repomap\.pyz|(?:^|\s)-m\s+repomap(?:\.cli)?(?:\s|$)', cmd, re.I)
    kind = ('local' if '--local' in cmd else 'scanner') if repomap else None
    if re.search(r'(?:^|[\\/"\s])ssh(?:\.exe)?["\s]', cmd, re.I) and re.search(
            r'-L\s*["\']?(?:127\.0\.0\.1:)?%d:127\.0\.0\.1:%d\b' % (port, port), cmd):
        kind = 'tunnel'
    if kind not in allowed:
        raise ValueError('Port %d is occupied by an unrecognized process; it was not stopped.' % port)
    return pid, kind


def wait_free(port, seconds=8):
    end = time.monotonic() + seconds
    while port_busy(port) and time.monotonic() < end:
        time.sleep(0.1)
    return not port_busy(port)


def stop_listener(port, allowed=('local',), info=None):
    if not port_busy(port):
        return False
    pid, kind = listener_kind(port, allowed)
    info = info or {}
    if kind != 'tunnel' and request_stop(port, info, local=kind == 'local') and wait_free(port, 15):
        return True
    # Legacy PC servers have Disconnect but no shutdown/identity API.
    if kind == 'local' and not info.get('instance'):
        try:
            req = Request('http://127.0.0.1:%d/api/local/disconnect' % port, b'{}',
                          {'Content-Type': 'application/json'})
            urlopen(req, timeout=15).close()
        except Exception:  # noqa: BLE001
            pass
    if not port_busy(port):
        return True
    if listener_kind(port, allowed)[0] != pid:
        raise ValueError('Port %d changed owner while stopping; try again.' % port)
    if os.name == 'nt':
        subprocess.run(['taskkill', '/F', '/T', '/PID', str(pid)], capture_output=True,
                       creationflags=subprocess.CREATE_NO_WINDOW, timeout=15)
    else:
        os.kill(pid, signal.SIGTERM)
    if not wait_free(port, 15):
        if os.name != 'nt' and listener_kind(port, allowed)[0] == pid:
            os.kill(pid, signal.SIGKILL)
        if not wait_free(port, 3):
            raise ValueError('Old repomap still owns port %d; it could not be stopped.' % port)
    return True

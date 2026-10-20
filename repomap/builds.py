"""Chay repomap_build_script.sh cua module tren server, log ra file de giao dien doc dan.

Vong doi:
  - moi script trong moi module chay toi da 1 lan; tien trinh chay trong process group rieng (session moi)
  - Stop (nut tren giao dien): SIGTERM ca nhom, sau STOP_GRACE giay chua tat thi SIGKILL
  - repomap dung (Ctrl+C, SIGTERM, SIGHUP khi phien ssh chay repomap bi dut): cli goi stop_all() -> build dung theo
  - repomap bi kill -9: tren Linux bash bao ve nhan SIGTERM (PR_SET_PDEATHSIG) va dung ca nhom
  - trinh duyet / tunnel mat ket noi: build van chay; giao diện doc tiep log tu offset da doc khi noi lai
Log: $REPOMAP_CACHE_DIR (mac dinh ~/.cache/repomap)/builds-<hash workspace>/<repo>[__<script>].log, ghi de moi lan chay.
"""
import hashlib
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from .buildscript import (SCRIPT, create_empty_script as create_user_script_file,
                          delete_script as remove_script_file, rename_user_script, script_path)

STOP_GRACE = 10           # giay cho build tu dung sau SIGTERM
MAX_READ = 512 * 1024     # toi da moi lan doc log

# bash bao ve: la truong nhom; nhan TERM/HUP/INT -> dung ca nhom (make, ninja, bitbake... con chau)
GUARD = ('trap \'trap - TERM HUP INT; kill -TERM 0 2>/dev/null; exit 143\' TERM HUP INT\n'
         'bash -l "$1" </dev/null &\n'
         'wait $!\n')


def log_dir(ws):
    base = os.environ.get('REPOMAP_CACHE_DIR') or os.path.join(
        os.environ.get('XDG_CACHE_HOME') or os.path.join(os.path.expanduser('~'), '.cache'), 'repomap')
    return os.path.join(base, 'builds-' + hashlib.sha1(ws.root.encode('utf-8')).hexdigest()[:16])


def _pdeathsig_hook():
    """Linux: ham chay trong tien trinh con (truoc exec) dat PR_SET_PDEATHSIG = SIGTERM cho bash bao ve.
    libc nap san o tien trinh cha: nap thu vien sau fork khi dang co nhieu luong co the deadlock.
    Luu y: tin hieu ban ra khi LUONG tao tien trinh con ket thuc -> Builds tao build tu 1 luong song mai."""
    if not sys.platform.startswith('linux'):
        return None
    try:
        import ctypes
        prctl = ctypes.CDLL(None, use_errno=True).prctl
    except Exception:  # noqa: BLE001 - khong co prctl: van chay, chi mat lop bao ve nay
        return None
    return lambda: prctl(1, signal.SIGTERM)                          # PR_SET_PDEATHSIG = 1


class WinJob:
    """Windows: Job Object chua build va moi tien trinh con chau (Cygwin/Git Bash lam dut quan he cha-con nen
    taskkill /T khong du). Dung = TerminateJobObject; repomap chet thi job tu dong (KILL_ON_JOB_CLOSE)."""

    def __init__(self, pid):
        import ctypes
        from ctypes import wintypes as W

        class Basic(ctypes.Structure):
            _fields_ = [('a', ctypes.c_int64), ('b', ctypes.c_int64), ('LimitFlags', W.DWORD),
                        ('c', ctypes.c_size_t), ('d', ctypes.c_size_t), ('e', W.DWORD), ('f', ctypes.c_size_t),
                        ('g', W.DWORD), ('h', W.DWORD)]

        class Extended(ctypes.Structure):
            _fields_ = [('Basic', Basic), ('Io', ctypes.c_ulonglong * 6), ('i', ctypes.c_size_t * 4)]

        k = self.k32 = ctypes.WinDLL('kernel32', use_last_error=True)
        k.CreateJobObjectW.restype = k.OpenProcess.restype = W.HANDLE
        k.SetInformationJobObject.argtypes = [W.HANDLE, ctypes.c_int, ctypes.c_void_p, W.DWORD]
        k.AssignProcessToJobObject.argtypes = [W.HANDLE, W.HANDLE]
        k.TerminateJobObject.argtypes = [W.HANDLE, W.UINT]
        k.CloseHandle.argtypes = [W.HANDLE]
        self.job = k.CreateJobObjectW(None, None)
        info = Extended()
        info.Basic.LimitFlags = 0x2000                                  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        k.SetInformationJobObject(self.job, 9, ctypes.byref(info), ctypes.sizeof(info))
        proc = k.OpenProcess(0x0101, False, pid)                       # PROCESS_SET_QUOTA | PROCESS_TERMINATE
        k.AssignProcessToJobObject(self.job, proc)
        k.CloseHandle(proc)

    def kill(self):
        self.k32.TerminateJobObject(self.job, 1)


class Run:
    def __init__(self, repo, script, log):
        self.repo, self.script, self.log = repo, script, log
        self.proc = self.job = None
        self.state, self.code = 'running', None          # running | done | failed | stopped
        self.started, self.ended = time.time(), None
        self.stopping = False
        self.clear_at = 0

    def info(self):
        end = self.ended or time.time()
        return {'repo': self.repo, 'script': self.script, 'state': self.state, 'code': self.code, 'started': self.started,
                'elapsed': round(end - self.started, 1)}


class Builds:
    def __init__(self, ws):
        self.ws = ws
        self.runs = {}                                    # (repo, script) -> Run
        self.lock = threading.Lock()
        # tien trinh build duoc tao tu 1 luong song suot doi server (khong phai luong xu ly request, luong do
        # ket thuc ngay sau khi tra loi -> PR_SET_PDEATHSIG se giet build ngay)
        self.spawner = ThreadPoolExecutor(max_workers=1, thread_name_prefix='repomap-build')
        self.hook = _pdeathsig_hook()

    def bash(self):
        b = os.environ.get('REPOMAP_BASH') or shutil.which('bash')      # REPOMAP_BASH: chon bash khac
        if not b:
            raise ValueError('bash was not found on the server, cannot run scripts')
        return b

    def start(self, repo, script=SCRIPT):
        path = script_path(self.ws, repo, script)
        if not os.path.isfile(path):
            raise ValueError('%s not found in %s: create it first' % (script, repo or '.'))
        key = (repo, script)
        with self.lock:
            if not os.path.isfile(path):
                raise ValueError('%s not found in %s: create it first' % (script, repo or '.'))
            old = self.runs.get(key)
            if old and old.state == 'running':
                raise ValueError('%s is already running in %s' % (script, repo or '.'))
            os.makedirs(log_dir(self.ws), exist_ok=True)
            stem = (repo or 'root').replace('/', '__')
            if script != SCRIPT:
                stem += '__' + script
            log = os.path.join(log_dir(self.ws), stem + '.log')
            run = Run(repo, script, log)
            out = open(log, 'wb')
            out.write(('$ bash %s   (repomap, %s)\n' % (path, time.strftime('%Y-%m-%d %H:%M:%S'))).encode('utf-8'))
            out.flush()
            env = dict(os.environ, TERM='dumb', PYTHONUNBUFFERED='1')
            kw = {}
            if os.name == 'nt':
                kw['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                kw['start_new_session'] = True
                if self.hook:
                    kw['preexec_fn'] = self.hook
            # Windows: khong can bash bao ve, Job Object lo viec dung ca cay (WinJob)
            argv = [self.bash(), '-l', path] if os.name == 'nt' else [self.bash(), '-c', GUARD, 'repomap-build', path]
            try:
                run.proc = self.spawner.submit(subprocess.Popen, argv, cwd=os.path.dirname(path), stdin=subprocess.DEVNULL,
                                               stdout=out, stderr=subprocess.STDOUT, env=env, **kw).result()
                if os.name == 'nt':
                    try:
                        run.job = WinJob(run.proc.pid)
                    except Exception:  # noqa: BLE001 - khong tao duoc job: van dung duoc bang taskkill /T
                        run.job = None
            finally:
                out.close()
            self.runs[key] = run
        threading.Thread(target=self._wait, args=(run,), daemon=True).start()
        return run.info()

    def delete_script(self, repo, script=SCRIPT):
        key = (repo, script)
        with self.lock:
            run = self.runs.get(key)
            if run and run.state == 'running':
                return {'error': '%s is running in %s; stop it before deleting' % (script, repo or '.')}
            result = remove_script_file(self.ws, repo, script)
            self.runs.pop(key, None)
            if run:
                try:
                    os.remove(run.log)
                except OSError:
                    pass
            return result

    def create_user_script(self, repo, name):
        with self.lock:
            return create_user_script_file(self.ws, repo, name)

    def rename_script(self, repo, old_name, new_name):
        key = (repo, old_name)
        with self.lock:
            run = self.runs.get(key)
            if run and run.state == 'running':
                return {'error': '%s is running in %s; stop it before renaming' % (old_name, repo or '.')}
            result = rename_user_script(self.ws, repo, old_name, new_name)
            if result['name'] != old_name:
                self.runs.pop(key, None)
                if run:
                    try:
                        os.remove(run.log)
                    except OSError:
                        pass
            return result

    def _wait(self, run):
        run.proc.wait()
        self._finish(run)

    def _finish(self, run):
        """Chot trang thai khi tien trinh da ket thuc (goi tu luong cho hoac tu stop(wait=True); chi chay 1 lan)."""
        code = run.proc.poll()
        with self.lock:
            if code is None or run.ended:
                return
            run.code, run.ended = code, time.time()
            run.state = 'stopped' if run.stopping else ('done' if code == 0 else 'failed')
        try:
            with open(run.log, 'ab') as f:
                f.write(('\n[repomap] %s, exit code %d, %.0f s\n' % (run.state, code, run.ended - run.started))
                        .encode('utf-8'))
        except OSError:
            pass

    def stop(self, repo, wait=False, script=SCRIPT):
        run = self.runs.get((repo, script))
        if not run or run.state != 'running':
            return run.info() if run else {'repo': repo, 'script': script, 'state': 'idle'}
        run.stopping = True
        self._signal(run, signal.SIGTERM)
        hard = getattr(signal, 'SIGKILL', signal.SIGTERM)
        killer = threading.Timer(STOP_GRACE, lambda: run.proc.poll() is None and self._signal(run, hard))
        killer.daemon = True
        killer.start()
        if wait:
            try:
                run.proc.wait(STOP_GRACE + 2)
                self._finish(run)
            except subprocess.TimeoutExpired:
                pass
        return run.info()

    def _signal(self, run, sig):
        try:
            if run.job:
                run.job.kill()
            elif os.name == 'nt':
                subprocess.run(['taskkill', '/F', '/T', '/PID', str(run.proc.pid)], capture_output=True)
            else:
                os.killpg(run.proc.pid, sig)
        except (ProcessLookupError, PermissionError, OSError):
            pass

    def stop_all(self):
        """repomap sap thoat: dung moi build dang chay (cho toi da STOP_GRACE giay)."""
        running = [key for key, run in list(self.runs.items()) if run.state == 'running']
        for repo, script in running:
            self.stop(repo, script=script)
        end = time.time() + STOP_GRACE
        for key in running:
            proc = self.runs[key].proc
            try:
                proc.wait(max(0.1, end - time.time()))
            except subprocess.TimeoutExpired:
                self._signal(self.runs[key], getattr(signal, 'SIGKILL', signal.SIGTERM))
        return running

    def status(self, repo, offset=0, script=SCRIPT):
        """Trang thai + log tu byte `offset` (giao dien goi lien tuc khi dang xem; noi lai thi doc tiep)."""
        run = self.runs.get((repo, script))
        if not run:
            return {'repo': repo, 'script': script, 'state': 'idle', 'log': '', 'next': 0}
        info = run.info()
        try:
            size = os.path.getsize(run.log)
            requested = int(offset)
            reset = requested >= 0 and requested < run.clear_at
            offset = max(run.clear_at, size - MAX_READ) if requested < 0 else max(run.clear_at, min(requested, size))
            with open(run.log, 'rb') as f:
                f.seek(offset)
                data = f.read(MAX_READ)
            if len(data) == MAX_READ and data.rfind(b'\n') > 0:      # khong cat giua 1 ky tu UTF-8
                data = data[:data.rfind(b'\n') + 1]
        except (OSError, ValueError):
            size, offset, data, reset = 0, 0, b'', False
        info.update(log=data.decode('utf-8', 'replace'), next=offset + len(data), size=size, reset=reset)
        return info

    def clear(self, repo, script=SCRIPT):
        with self.lock:
            run = self.runs.get((repo, script))
            if not run:
                return {'repo': repo, 'script': script, 'state': 'idle', 'next': 0}
            try:
                run.clear_at = os.path.getsize(run.log)
            except OSError:
                run.clear_at = 0
            return dict(run.info(), next=run.clear_at)

    def summary(self):
        out = {}
        with self.lock:
            runs = list(self.runs.items())
        for (repo, script), run in runs:
            row = out.setdefault(repo, {'state': run.state, 'scripts': {}, '_latest': run.state})
            row['scripts'][script] = {'state': run.state}
            row['_latest'] = run.state
        for row in out.values():
            row['state'] = 'running' if any(s['state'] == 'running' for s in row['scripts'].values()) else row['_latest']
            del row['_latest']
        return out

"""Menu dong lenh tuong tac:  1. Run scan   2. Configuration   0. Exit

Configuration hoi port server va thong tin ssh tunnel, luu vao file cau hinh de lan sau khong phai nhap.
Ham nhan `inp`/`out` de test duoc ma khong can terminal.
"""
import os

from . import __version__
from .settings import check_port, default_path, default_ssh_host, detect_ssh_port, save_settings


def ask(prompt, default, check=None, inp=input, out=print):
    """Hoi 1 gia tri; Enter = giu mac dinh; hoi lai neu `check` bao loi."""
    while True:
        raw = inp('%s [%s]: ' % (prompt, default) if str(default) != '' else '%s: ' % prompt).strip()
        value = raw if raw else str(default)
        if check is None:
            return value
        try:
            return check(value)
        except ValueError as e:
            out('    ' + str(e))


def check_workspace(value):
    path = os.path.abspath(os.path.expanduser(value))
    if not value or not os.path.isdir(path):
        raise ValueError('folder not found: %s' % (value or '(empty)'))
    return path


def configure(s, inp=input, out=print):
    out('')
    out('Configuration (press Enter to keep the value in brackets)')
    s.port = ask('  Server port', s.port, check_port, inp, out)
    out('  The values below are only used to print the ssh tunnel command for your PC.')
    s.ssh_host = ask('  SSH host (name or IP you use to ssh into this machine)',
                     s.ssh_host or default_ssh_host(), None, inp, out)
    s.ssh_user = ask('  SSH user', s.resolved_ssh()[0], None, inp, out)
    detected, how = detect_ssh_port()
    s.ssh_port = ask('  SSH port (detected: %d, %s)' % (detected, how), s.ssh_port or detected, check_port, inp, out)
    local = ask('  Local port on your PC', s.ssh_local_port or s.port, check_port, inp, out)
    s.ssh_local_port = 0 if local == s.port else local
    out('Saved to ' + save_settings(s))
    out('Tunnel command: ' + s.tunnel_hint(s.port))


def run_menu(s, root=None, inp=input, out=print):
    """Hien menu; tra ve duong dan workspace can quet, hoac None neu chon thoat."""
    while True:
        out('')
        out('repomap %s' % __version__)
        out('  workspace : %s' % (root or s.workspace or '(not set)'))
        out('  config    : %s' % (s.source or default_path() + '  (not saved yet)'))
        out('  tunnel    : %s' % s.tunnel_hint(s.port))
        out('')
        out('  1. Run scan')
        out('  2. Configuration')
        out('  0. Exit')
        choice = inp('Choose [1]: ').strip() or '1'
        if choice == '1':
            if root:
                return check_workspace(root)
            return ask('Workspace root (the folder that contains .repo/)', s.workspace, check_workspace, inp, out)
        if choice == '2':
            configure(s, inp, out)
        elif choice in ('0', 'q', 'exit'):
            return None
        else:
            out('  Unknown choice: %s' % choice)

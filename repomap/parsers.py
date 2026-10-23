"""Parser tinh cho file build/config: BitBake, Make/Android.mk, Android.bp, Kconfig, DTS, CMake, Meson, QNX,
C/C++ (#include), Python (parsers_py) va DevOps (parsers_devops).

Moi file -> {'type', 'vars': [[line, key, op, value]], 'refs': [{k, raw, line}], 'items': [{type, name, line}]}
"""
import os
import re

from .parsers_devops import dockerfile_items, jenkins_items_refs, terraform_items_refs, yaml_items_refs
from .parsers_py import py_refs_items
from .util import read_text

C_EXT = ('.c', '.h', '.cc', '.cpp', '.cxx', '.hh', '.hpp', '.hxx', '.ipp', '.inl')


def ftype(rel):
    n = rel.rsplit('/', 1)[-1]
    if n.endswith(('.bb', '.bbappend', '.bbclass', '.inc')) or \
            (n.endswith(('.conf', '.conf.sample')) and '/conf/' in '/' + rel):
        return 'bb'
    if n.endswith(C_EXT):
        return 'c'
    if n.endswith('.py'):
        return 'py'
    if n.endswith(('.yml', '.yaml')):
        return 'yaml'
    if n == 'Dockerfile' or n.startswith('Dockerfile.') or n.endswith('.dockerfile'):
        return 'docker'
    if n.startswith('Jenkinsfile') or n.endswith('.groovy'):
        return 'groovy'
    if n.endswith(('.tf', '.tfvars', '.hcl')):
        return 'tf'
    if n.endswith('.bp'):
        return 'bp'
    if n.startswith('Kconfig'):
        return 'kconfig'
    if n.endswith('defconfig') or n == '.config':
        return 'defconfig'
    if n in ('Makefile', 'makefile', 'GNUmakefile', 'Kbuild') or n.endswith(('.mk', '.mak')) or n.startswith('Makefile.'):
        return 'make'
    if n == 'CMakeLists.txt' or n.endswith('.cmake'):
        return 'cmake'
    if n.endswith(('.dts', '.dtsi', '.dtso')):
        return 'dts'
    if n in ('meson.build', 'meson_options.txt'):
        return 'meson'
    if n.endswith('.build'):
        return 'qnxbuild'
    return 'text'


def logical_lines(text):
    """(so dong bat dau, dong) - noi cac dong ket thuc bang backslash."""
    buf, start = [], 0
    for i, raw in enumerate(text.splitlines(), 1):
        if not buf:
            start = i
        cont = raw.endswith('\\')
        part = raw[:-1] if cont else raw
        buf.append(part if not buf else part.strip())
        if not cont:
            yield start, ' '.join(buf)
            buf = []
    if buf:
        yield start, ' '.join(buf)


# ---------------------------------------------------------------- variables

ASSIGN = re.compile(r'^\s*(?:export\s+|override\s+)?([A-Za-z_][\w\-.${}/:\[\]+~@]*?)\s*'
                    r'(\?\?=|\?=|::=|:=|\+=|=\+|\.=|=\.|!=|=)\s*(.*)$')
BB_FUNC = re.compile(r'^(?:python\s+|fakeroot\s+)*[\w\-${}:.]*\s*\(\)\s*\{\s*$')


def parse_vars(text, ft):
    out, end = [], None
    for ln, line in logical_lines(text):
        s = line.strip()
        if end:                                   # dang trong khoi ham bitbake / define make
            if end.match(s):
                end = None
            continue
        if not s or s[0] == '#' or (ft == 'make' and line[0] == '\t') or (ft == 'bb' and line[0] in ' \t'):
            continue
        if ft == 'bb' and BB_FUNC.match(s):
            end = re.compile(r'^\}$')
            continue
        if ft == 'make' and s.startswith('define '):
            end = re.compile(r'^endef\b')
            continue
        m = ASSIGN.match(line)
        if m:
            k, op, v = m.group(1), m.group(2), m.group(3).strip()
            if ft == 'make':
                v = re.sub(r'\s+#.*$', '', v)
            if len(v) >= 2 and v[0] == v[-1] and v[0] in '"\'':
                v = v[1:-1].strip()
            out.append([ln, k, op, ' '.join(v.split())[:2000]])
    return out


def vars_dict(path):
    """Gop bien cua 1 file thanh dict (xu ly don gian = / ?= / +=)."""
    out = {}
    for _, k, op, v in parse_vars(read_text(path) or '', ftype(path.replace(os.sep, '/'))):
        if op in ('+=', '=+', '.=', '=.') and k in out:
            out[k] = (out[k] + ' ' + v).strip()
        elif not (op in ('?=', '??=') and k in out):
            out[k] = v
    return out


# ---------------------------------------------------------------- references (include/require/inherit...)

# (regex, multi) - multi: group(2) chua nhieu ten cach nhau bang khoang trang
REF_RX = {
    'bb': [(re.compile(r'^\s*(require|include)\s+(\S+)'), False),
           (re.compile(r'^\s*(inherit)\s+(.+)'), True)],
    'make': [(re.compile(r'^\s*-?s?(include)\s+(.+)'), True),
             (re.compile(r'\$\(call\s+(inherit-product)(?:-if-exists)?\s*,\s*([^)\s]+)'), False)],
    'dts': [(re.compile(r'^\s*#(include)\s+[<"]([^>"]+)'), False),
            (re.compile(r'^\s*/(include)/\s+"([^"]+)"'), False)],
    'kconfig': [(re.compile(r'^\s*(o?r?source)\s+"?([^"\s]+)'), False)],
    'cmake': [(re.compile(r'\b(add_subdirectory|include)\s*\(\s*"?([^\s)"]+)', re.I), False)],
    'meson': [(re.compile(r"\b(subdir)\s*\(\s*'([^']+)'"), False)],
    'c': [(re.compile(r'^\s*#\s*(include)\s*[<"]([^>"]+)'), False)],
}
_JUNK = re.compile(r"[(),'@]")                     # token rac tu ${@...} cua bitbake
_PLAIN_VAR = re.compile(r'\$[({][\w/.\-]+[)}][\w/.\-${}()]*')
_BUILD_VAR = re.compile(r'\$\([A-Z_]+\)')          # include $(BUILD_SHARED_LIBRARY) ...


def parse_refs(text, ft):
    refs = []
    for ln, line in logical_lines(text):
        for rx, multi in REF_RX.get(ft, ()):
            for m in rx.finditer(line):
                for raw in (m.group(2).split() if multi else [m.group(2)]):
                    if raw.startswith('#'):
                        break
                    if _BUILD_VAR.fullmatch(raw) or (_JUNK.search(raw) and not _PLAIN_VAR.fullmatch(raw)):
                        continue
                    refs.append({'k': m.group(1), 'raw': raw, 'line': ln})
    return refs


def src_uri_refs(vars_):
    """URL git trong SRC_URI -> ref 'src' (de noi nguoc ve repo trong manifest)."""
    refs = []
    for ln, k, _, v in vars_:
        if k.startswith('SRC_URI'):
            for tok in v.split():
                if re.match(r'(git|gitsm|ssh|https?)://', tok) and \
                        (tok.startswith('git') or '.git' in tok or 'protocol=' in tok):
                    refs.append({'k': 'src', 'raw': tok, 'line': ln})
    return refs


# ---------------------------------------------------------------- items (module, target...)

_BRACE = re.compile(r'[{}]')


def _line_of(text, pos):
    return text.count('\n', 0, pos) + 1


def bp_modules(text):
    """Android.bp: [{type, name, line, deps, defaults, hw}].
    defaults: cac module *_defaults duoc ke thua (deps cua chung cung la deps cua module nay);
    hw: cai vao .../bin/hw (relative_install_path "hw") hoac co vintf_fragments -> HAL service / HAL impl."""
    t = re.sub(r'//[^\n]*|/\*.*?\*/', lambda m: re.sub(r'[^\n]', ' ', m.group()), text, flags=re.S)
    mods, pos = [], 0
    for m in re.finditer(r'^(\w+)\s*\{', t, re.M):
        if m.start() < pos:
            continue
        depth, end = 0, len(t)
        for b in _BRACE.finditer(t, m.end() - 1):
            depth += 1 if b.group() == '{' else -1
            if depth == 0:
                end = b.start()
                break
        body, pos = t[m.end():end], end
        nm = re.search(r'\bname\s*:\s*"([^"]+)"', body)
        deps = set()
        for d in re.finditer(r'\b(?:shared_libs|static_libs|header_libs|whole_static_libs|libs|required|deps|'
                             r'runtime_libs|defaults)\s*:\s*\[([^\]]*)\]', body):
            deps.update(re.findall(r'"([^"]+)"', d.group(1)))
        dm = re.search(r'\bdefaults\s*:\s*\[([^\]]*)\]', body)
        hw = bool(re.search(r'\brelative_install_path\s*:\s*"hw"|\bvintf_fragments\s*:', body))
        mods.append({'type': m.group(1), 'name': nm.group(1) if nm else '?',
                     'line': _line_of(t, m.start()), 'deps': sorted(deps)[:80],
                     'defaults': re.findall(r'"([^"]+)"', dm.group(1)) if dm else [], 'hw': hw})
    return mods


def mk_modules(text):
    """Android.mk: module = LOCAL_MODULE ... include $(BUILD_xxx)."""
    mods, cur = [], {}
    for ln, line in logical_lines(text):
        s = line.strip()
        if re.match(r'include\s+\$\(CLEAR_VARS\)', s):
            cur = {'line': ln, 'deps': []}
        elif re.match(r'LOCAL_MODULE_RELATIVE_PATH\s*:?=\s*hw\s*$|LOCAL_VINTF_FRAGMENTS\s*[:+]?=', s):
            cur['hw'] = True
        elif re.match(r'LOCAL_MODULE\s*:?=', s):
            cur['name'] = s.split('=', 1)[1].strip()
            cur.setdefault('line', ln)
        elif re.match(r'LOCAL_(SHARED|STATIC|HEADER|WHOLE_STATIC)_LIBRARIES|LOCAL_REQUIRED_MODULES', s) and '=' in s:
            cur.setdefault('deps', []).extend(s.split('=', 1)[1].split())
        else:
            m = re.match(r'include\s+\$\((BUILD_\w+|PREBUILT\w*)\)', s)
            if m and cur.get('name'):
                mods.append({'type': m.group(1), 'name': cur['name'], 'line': cur.get('line', ln),
                             'deps': sorted(set(cur.get('deps', [])))[:80], 'hw': cur.get('hw', False)})
                cur = {}
    return mods


def dts_items(text):
    items = []
    for k in ('model', 'compatible'):
        m = re.search(r'^\s*' + k + r'\s*=\s*([^;]+);', text, re.M)
        if m:
            items.append({'type': k, 'name': ', '.join(re.findall(r'"([^"]*)"', m.group(1))),
                          'line': _line_of(text, m.start())})
    return items


def cmake_items(text):
    return [{'type': m.group(1).lower(), 'name': m.group(2), 'line': _line_of(text, m.start())}
            for m in re.finditer(r'\b(project|add_executable|add_library)\s*\(\s*([\w.\-${}]+)', text, re.I)]


def qnx_build_items(text):
    return [{'type': 'attr', 'name': m.group(1), 'line': _line_of(text, m.start())}
            for m in re.finditer(r'^\s*\[([^\]]*(?:virtual|image)=[^\]]*)\]', text, re.M)]


# ---------------------------------------------------------------- entry

def parse_file(rel, text):
    ft = ftype(rel)
    name = rel.rsplit('/', 1)[-1]
    vars_ = parse_vars(text, ft) if ft in ('bb', 'make', 'kconfig', 'defconfig', 'qnxbuild', 'text') else []
    refs = parse_refs(text, ft)
    items = []
    if ft == 'bb':
        refs += src_uri_refs(vars_)
    elif ft == 'bp':
        items = bp_modules(text)
    elif name == 'Android.mk':
        items = mk_modules(text)
    elif ft == 'dts':
        items = dts_items(text)
    elif ft == 'cmake':
        items = cmake_items(text)
    elif ft == 'qnxbuild':
        items = qnx_build_items(text)
    elif ft == 'py':
        refs, items = py_refs_items(text)
    elif ft == 'yaml':
        items, refs = yaml_items_refs(rel, text)
    elif ft == 'docker':
        items = dockerfile_items(text)
    elif ft == 'groovy':
        items, refs = jenkins_items_refs(text)
    elif ft == 'tf':
        items, refs = terraform_items_refs(text)
    return {'type': ft, 'vars': vars_, 'refs': refs, 'items': items}

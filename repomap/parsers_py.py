"""Parser cho du an Python: import va class/def (dung `ast` cua thu vien chuan), doc TOML toi gian.

Python 3.8 chua co tomllib nen read_toml() chi doc phan hay gap trong pyproject.toml:
bang [a.b], khoa = chuoi / so / bool / mang (ke ca mang nhieu dong).
"""
import ast
import re


def _join(mod, name):
    return mod + name if mod.endswith('.') or not mod else mod + '.' + name


def py_refs_items(text):
    """(refs, items). ref: {k: 'import', raw: 'pkg.mod' hoac '..mod', line, opt}.
    opt=True: 'from X import name' - chi giu neu name la module that (file .py)."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):        # cu phap moi hon Python dang chay -> doc bang regex
        return _regex_refs(text), []
    refs, items = [], []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            refs += [{'k': 'import', 'raw': a.name, 'line': node.lineno} for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            mod = '.' * (node.level or 0) + (node.module or '')
            if node.module:
                refs.append({'k': 'import', 'raw': mod, 'line': node.lineno})
            refs += [{'k': 'import', 'raw': _join(mod, a.name), 'line': node.lineno, 'opt': True}
                     for a in node.names if a.name != '*']
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            items.append({'type': 'def', 'name': node.name, 'line': node.lineno})
        elif isinstance(node, ast.ClassDef):
            items.append({'type': 'class', 'name': node.name, 'line': node.lineno})
            items += [{'type': 'method', 'name': '%s.%s' % (node.name, f.name), 'line': f.lineno}
                      for f in node.body if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))]
    return refs, items


def _regex_refs(text):
    refs = []
    for i, line in enumerate(text.splitlines(), 1):
        m = re.match(r'\s*from\s+(\.*[\w.]*)\s+import\s+', line)
        if m:
            refs.append({'k': 'import', 'raw': m.group(1), 'line': i})
            continue
        m = re.match(r'\s*import\s+([\w.]+(?:\s*,\s*[\w.]+)*)', line)
        if m:
            refs += [{'k': 'import', 'raw': x.strip(), 'line': i} for x in m.group(1).split(',')]
    return refs


# ---------------------------------------------------------------- TOML toi gian

def _toml_value(raw):
    raw = raw.strip()
    if raw.startswith('['):
        return [a or b for a, b in re.findall(r'"([^"]*)"|\'([^\']*)\'', raw)]
    if raw[:1] in '"\'':
        q = raw[0] * 3 if raw.startswith(raw[0] * 3) else raw[0]
        end = raw.find(q, len(q))
        return raw[len(q):end] if end > 0 else raw.strip(q)
    return raw.split('#', 1)[0].strip()


def read_toml(text):
    """{'project': {'name': 'x', 'dependencies': [...]}, 'project.scripts': {...}, ...}"""
    out, table, buf, key = {}, '', None, None
    for line in text.splitlines():
        if buf is not None:                              # dang gom mang nhieu dong
            buf += ' ' + line.split('#', 1)[0]
            if buf.count('[') <= buf.count(']'):
                out.setdefault(table, {})[key] = _toml_value(buf)
                buf = None
            continue
        s = line.strip()
        if not s or s.startswith('#'):
            continue
        m = re.match(r'\[\[?\s*([^\]]+?)\s*\]\]?$', s)
        if m:
            table = m.group(1).replace('"', '').replace("'", '')
            continue
        m = re.match(r'([\w.\-"\']+)\s*=\s*(.*)$', s)
        if not m:
            continue
        key, val = m.group(1).strip('"\''), m.group(2)
        if val.startswith('[') and val.count('[') > val.count(']'):
            buf = val
            continue
        out.setdefault(table, {})[key] = _toml_value(val)
    return out


def requirement_names(text):
    """Ten goi trong requirements.txt (bo comment, -r, -e, option)."""
    names = []
    for line in text.splitlines():
        s = line.split('#', 1)[0].strip()
        if not s or s.startswith('-'):
            continue
        m = re.match(r'([A-Za-z0-9_.\-]+)', s)
        if m:
            names.append(m.group(1))
    return names

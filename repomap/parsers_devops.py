"""Parser cho file DevOps: YAML (GitLab CI, GitHub Actions, Compose, Ansible), Dockerfile, Jenkinsfile, Terraform.

Khong co thu vien YAML trong stdlib nen chi doc theo thut le: khoa cap 0 va khoa con cua mot khoa.
Du de dung muc luc (job, service, stage...) va tham chieu (include, import_tasks, role...).
Moi regex neo dau dong dung [ \\t]* (khong dung \\s*) de khong khop xuyen qua dong trong.
"""
import re

YAML_KEY = re.compile(r'^([ \t]*)(?:-[ \t]+)?(["\']?)([\w.\-/ ]+?)\2[ \t]*:(?:\s|$)')
GITLAB_RESERVED = {'stages', 'variables', 'include', 'default', 'workflow', 'image', 'services',
                   'before_script', 'after_script', 'cache'}


def _line(text, pos):
    return text.count('\n', 0, pos) + 1


def yaml_keys(text):
    """[(indent, key, line)] cua moi dong 'key:' (bo comment)."""
    out = []
    for i, raw in enumerate(text.splitlines(), 1):
        if raw.lstrip().startswith('#'):
            continue
        m = YAML_KEY.match(raw)
        if m:
            out.append((len(m.group(1)), m.group(3), i))
    return out


def yaml_children(text, parent):
    """Khoa con truc tiep cua khoa cap 0 `parent` (vd: jobs, services)."""
    out, inside, level = [], False, None
    for indent, key, line in yaml_keys(text):
        if indent == 0:
            inside, level = key == parent, None
            continue
        if inside:
            level = indent if level is None else level
            if indent == level:
                out.append((key, line))
    return out


def yaml_list(text, key):
    """Gia tri cua 'key: [a, b]' hoac danh sach '- a' ngay duoi 'key:' (cap 0)."""
    m = re.search(r'^' + re.escape(key) + r'[ \t]*:[ \t]*\[([^\]]*)\]', text, re.M)
    if m:
        return [x.strip().strip('"\'') for x in m.group(1).split(',') if x.strip()]
    m = re.search(r'^' + re.escape(key) + r'[ \t]*:[ \t]*\n((?:[ \t]*-[^\n]*\n?)+)', text, re.M)
    if not m:
        return []
    return [x.strip().strip('"\'') for x in re.findall(r'^[ \t]*-[ \t]*([^\n#]+)', m.group(1), re.M)]


def _under(text, pos, key):
    """Dong bat dau tai pos co nam ngay trong khoi 'key:' khong (bo qua cac phan tu '-' cung cap)."""
    cur = text[pos:].split('\n', 1)[0]
    indent = len(cur) - len(cur.lstrip())
    for line in reversed(text[:pos].split('\n')[:-1]):
        s = line.strip()
        if not s or s.startswith('#'):
            continue
        ind = len(line) - len(line.lstrip())
        if ind > indent or (ind == indent and s.startswith('-')):
            continue
        return bool(re.match(r'-?\s*' + re.escape(key) + r'\s*:\s*$', s))
    return False


def yaml_items_refs(rel, text):
    items, refs = [], []
    name = rel.rsplit('/', 1)[-1]
    for parent, kind in (('jobs', 'job'), ('services', 'service')):
        items += [{'type': kind, 'name': k, 'line': ln} for k, ln in yaml_children(text, parent)]
    if name.startswith('.gitlab-ci') or '/.gitlab/' in '/' + rel:
        items += [{'type': 'job', 'name': k, 'line': ln} for i, k, ln in yaml_keys(text)
                  if i == 0 and k not in GITLAB_RESERVED and not k.startswith('.')]
        for m in re.finditer(r'^[ \t]*-?[ \t]*(?:local:[ \t]*)?["\']?(/?[\w.\-/]+\.ya?ml)["\']?[ \t]*$', text, re.M):
            if _under(text, m.start(), 'include'):
                refs.append({'k': 'include', 'raw': m.group(1).lstrip('/'), 'line': _line(text, m.start())})
    items += [{'type': 'play', 'name': m.group(1).strip(), 'line': _line(text, m.start())}
              for m in re.finditer(r'^-[ \t]+hosts:[ \t]*([^\n#]+)', text, re.M)]
    if not items:
        items = [{'type': 'key', 'name': k, 'line': ln} for i, k, ln in yaml_keys(text) if i == 0][:200]
    for m in re.finditer(r'^[ \t]*-?[ \t]*(import_playbook|include_tasks|import_tasks|include_vars)[ \t]*:'
                         r'[ \t]*["\']?([^"\'\s{}]+)', text, re.M):
        refs.append({'k': m.group(1), 'raw': m.group(2), 'line': _line(text, m.start())})
    for m in re.finditer(r'^[ \t]*-[ \t]*(?:role:[ \t]*)?([\w.\-]+)[ \t]*$', text, re.M):
        if _under(text, m.start(), 'roles'):
            refs.append({'k': 'role', 'raw': m.group(1), 'line': _line(text, m.start())})
    for m in re.finditer(r'^[ \t]*-?[ \t]*(?:role|name):[ \t]*([\w.\-]+)[ \t]*$', text, re.M):
        if _under(text, m.start(), 'include_role') or _under(text, m.start(), 'import_role'):
            refs.append({'k': 'role', 'raw': m.group(1), 'line': _line(text, m.start())})
    return items, refs


def dockerfile_items(text):
    return [{'type': m.group(1).upper(), 'name': m.group(2).strip()[:200], 'line': _line(text, m.start())}
            for m in re.finditer(r'^[ \t]*(FROM|EXPOSE|ENTRYPOINT|CMD)[ \t]+(.+)$', text, re.M | re.I)]


def jenkins_items_refs(text):
    items = [{'type': 'stage', 'name': m.group(1), 'line': _line(text, m.start())}
             for m in re.finditer(r'stage\s*\(\s*[\'"]([^\'"]+)[\'"]', text)]
    refs = [{'k': 'load', 'raw': m.group(1), 'line': _line(text, m.start())}
            for m in re.finditer(r'\bload\s*\(?\s*[\'"]([^\'"]+)[\'"]', text)]
    return items, refs


def terraform_items_refs(text):
    items = [{'type': m.group(1), 'name': '.'.join(x for x in m.group(2, 3) if x), 'line': _line(text, m.start())}
             for m in re.finditer(r'^[ \t]*(resource|data|module|provider|variable|output)[ \t]+"([^"]+)"'
                                  r'(?:[ \t]+"([^"]+)")?', text, re.M)]
    refs = [{'k': 'module', 'raw': m.group(1), 'line': _line(text, m.start())}
            for m in re.finditer(r'^[ \t]*source[ \t]*=[ \t]*"(\.{1,2}/[^"]+)"', text, re.M)]
    return items, refs

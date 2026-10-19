"""Tang 1 cho du an Python va DevOps (CI, Docker, Ansible, Terraform, Helm).

Moi ham nhan (d, files, dirs, keys) - d: thu muc repo, files/dirs: ten o goc, keys: list file chinh (them vao).
Tra list section {title, rows}.
"""
import configparser
import os
import re
from fnmatch import fnmatch

from .parsers_devops import (dockerfile_items, jenkins_items_refs, terraform_items_refs, yaml_children, yaml_items_refs,
                             yaml_list)
from .parsers_py import read_toml, requirement_names
from .util import listdir, read_text


def _sec(title, rows):
    rows = [[k, v] for k, v in rows if v]
    return {'title': title, 'rows': rows} if rows else None


def _names(items, limit=30):
    names = list(dict.fromkeys(items))
    return ', '.join(names[:limit]) + (' … (+%d)' % (len(names) - limit) if len(names) > limit else '')


def _yaml_scalar(text, key):
    m = re.search(r'^' + re.escape(key) + r'[ \t]*:[ \t]*["\']?([^"\'\n#]+)', text, re.M)
    return m.group(1).strip() if m else ''


# ---------------------------------------------------------------- Python

def python_sections(d, files, dirs, keys):
    rows, read = [], lambda n: read_text(os.path.join(d, n)) or ''
    deps = []
    if 'pyproject.toml' in files:
        t = read_toml(read('pyproject.toml'))
        proj, poetry = t.get('project', {}), t.get('tool.poetry', {})
        rows += [('name', proj.get('name') or poetry.get('name')), ('version', proj.get('version') or poetry.get('version')),
                 ('requires-python', proj.get('requires-python')),
                 ('build backend', t.get('build-system', {}).get('build-backend')),
                 ('scripts', _names(list(t.get('project.scripts', {})) + list(t.get('tool.poetry.scripts', {}))))]
        deps += proj.get('dependencies') or []
        deps += [k for k in t.get('tool.poetry.dependencies', {}) if k != 'python']
        if 'tool.pytest.ini_options' in t:
            rows.append(('tests', 'pytest'))
    if 'setup.cfg' in files:
        cp = configparser.ConfigParser(interpolation=None)
        try:
            cp.read_string(read('setup.cfg'))
            rows += [('name', cp.get('metadata', 'name', fallback='')), ('version', cp.get('metadata', 'version', fallback='')),
                     ('requires-python', cp.get('options', 'python_requires', fallback=''))]
            deps += [x for x in cp.get('options', 'install_requires', fallback='').split('\n') if x.strip()]
        except configparser.Error:
            pass
    if 'setup.py' in files:
        text = read('setup.py')
        for k in ('name', 'version'):
            m = re.search(r'\b' + k + r'\s*=\s*["\']([^"\']+)', text)
            rows.append((k, m.group(1) if m else ''))
    reqs = [n for n in files if fnmatch(n, 'requirements*.txt')]
    for n in reqs:
        deps += requirement_names(read(n))
    pkgs = [x for x in dirs if os.path.isfile(os.path.join(d, x, '__init__.py'))]
    pkgs += ['src/' + x for x in listdir(os.path.join(d, 'src')) if os.path.isfile(os.path.join(d, 'src', x, '__init__.py'))]
    tests = [name for name, hit in (('pytest', 'pytest.ini' in files or 'conftest.py' in files or 'tests' in dirs),
                                    ('tox', 'tox.ini' in files), ('nox', 'noxfile.py' in files)) if hit]
    rows += [('dependencies', '%d: %s' % (len(set(deps)), _names(re.split(r'[<>=!~; \[]', x)[0] for x in deps))
              if deps else ''),
             ('packages', ', '.join(pkgs)), ('tests', ', '.join(tests))]
    seen, uniq = set(), []
    for k, v in rows:                                  # cung 1 khoa tu nhieu file: giu gia tri dau tien
        if v and k not in seen:
            seen.add(k)
            uniq.append((k, v))
    keys += [n for n in ('pyproject.toml', 'setup.py', 'setup.cfg', 'Pipfile', 'tox.ini', 'noxfile.py') if n in files] + reqs
    return [s for s in [_sec('Python', uniq)] if s]


# ---------------------------------------------------------------- DevOps

def _gitlab(d, files, keys):
    if '.gitlab-ci.yml' not in files:
        return None
    text = read_text(os.path.join(d, '.gitlab-ci.yml')) or ''
    items, refs = yaml_items_refs('.gitlab-ci.yml', text)
    keys.append('.gitlab-ci.yml')
    return _sec('GitLab CI', [('stages', ', '.join(yaml_list(text, 'stages'))),
                              ('jobs', _names(i['name'] for i in items if i['type'] == 'job')),
                              ('include', ', '.join(r['raw'] for r in refs))])


def _github(d, keys):
    wf = os.path.join(d, '.github', 'workflows')
    rows = []
    for n in listdir(wf):
        if n.endswith(('.yml', '.yaml')):
            text = read_text(os.path.join(wf, n)) or ''
            jobs = [k for k, _ in yaml_children(text, 'jobs')]
            rows.append((_yaml_scalar(text, 'name') or n, 'jobs: ' + _names(jobs)))
            keys.append('.github/workflows/' + n)
    return _sec('GitHub Actions', rows[:20])


def _jenkins(d, files, keys):
    rows = []
    for n in files:
        if n.startswith('Jenkinsfile'):
            items, _ = jenkins_items_refs(read_text(os.path.join(d, n)) or '')
            rows.append((n, 'stages: ' + _names(i['name'] for i in items)))
            keys.append(n)
    return _sec('Jenkins', rows)


def _docker(d, files, keys):
    rows = []
    for n in files:
        if n == 'Dockerfile' or n.startswith('Dockerfile.') or n.endswith('.dockerfile'):
            items = dockerfile_items(read_text(os.path.join(d, n)) or '')
            rows.append((n, '; '.join('%s %s' % (i['type'], i['name']) for i in items if i['type'] in ('FROM', 'EXPOSE'))))
            keys.append(n)
        elif re.match(r'(docker-)?compose[\w.\-]*\.ya?ml$', n):
            services = [k for k, _ in yaml_children(read_text(os.path.join(d, n)) or '', 'services')]
            rows.append((n, 'services: ' + _names(services)))
            keys.append(n)
    return _sec('Docker', rows)


def _ansible(d, files, dirs, keys):
    rows = []
    roles = [r for r in listdir(os.path.join(d, 'roles')) if os.path.isdir(os.path.join(d, 'roles', r))]
    plays = [n for n in files if n.endswith(('.yml', '.yaml'))]
    plays += ['playbooks/' + n for n in listdir(os.path.join(d, 'playbooks')) if n.endswith(('.yml', '.yaml'))]
    for p in plays:
        items, _ = yaml_items_refs(p, read_text(os.path.join(d, p)) or '')
        hosts = [i['name'] for i in items if i['type'] == 'play']
        if hosts:
            rows.append((p, 'hosts: ' + ', '.join(hosts)))
            keys.append(p)
    if roles:
        rows.append(('roles', _names(roles)))
    if 'ansible.cfg' in files:
        keys.append('ansible.cfg')
    return _sec('Ansible', rows[:20])


def _terraform(d, files, keys):
    tf = [n for n in files if n.endswith('.tf')]
    if not tf:
        return None
    items = []
    for n in tf:
        items += terraform_items_refs(read_text(os.path.join(d, n)) or '')[0]
    keys += tf[:10]
    by = lambda kind: [i['name'] for i in items if i['type'] == kind]  # noqa: E731
    return _sec('Terraform', [('providers', _names(by('provider'))), ('modules', _names(by('module'))),
                              ('resources', '%d: %s' % (len(by('resource')), _names(by('resource'), 15)) if by('resource') else ''),
                              ('variables', str(len(by('variable'))) if by('variable') else '')])


def _helm(d, files, keys):
    if 'Chart.yaml' not in files:
        return None
    text = read_text(os.path.join(d, 'Chart.yaml')) or ''
    keys.append('Chart.yaml')
    return _sec('Helm chart', [(k, _yaml_scalar(text, k)) for k in ('name', 'version', 'appVersion', 'description')])


def devops_sections(d, files, dirs, keys):
    secs = [_gitlab(d, files, keys), _github(d, keys), _jenkins(d, files, keys), _docker(d, files, keys),
            _ansible(d, files, dirs, keys), _terraform(d, files, keys), _helm(d, files, keys)]
    return [s for s in secs if s]

"""Bang quy tac: nhom file khi quet sau, va bien quan trong can doc o tang insight."""
import re
from fnmatch import fnmatch

# (id, nhan, test(path_co_slash_dau, ten_file, chuoi_tags)) - nhom dau tien khop se thang
CATS = [
    ('layer', 'Yocto layer.conf', lambda p, n, t: p.endswith('/conf/layer.conf')),
    ('machine', 'Yocto machine', lambda p, n, t: '/conf/machine/' in p and n.endswith('.conf')),
    ('distro', 'Yocto distro', lambda p, n, t: '/conf/distro/' in p and n.endswith('.conf')),
    ('bbconf', 'BitBake conf', lambda p, n, t: 'yocto' in t and '/conf/' in p and re.search(r'\.conf(\.sample)?$', n)),
    ('image', 'Yocto image', lambda p, n, t: n.endswith('.bb') and ('/images/' in p or '-image' in n)),
    ('recipe', 'Recipe .bb', lambda p, n, t: n.endswith('.bb')),
    ('bbappend', '.bbappend', lambda p, n, t: n.endswith('.bbappend')),
    ('bbclass', '.bbclass / .inc', lambda p, n, t: n.endswith('.bbclass') or (n.endswith('.inc') and 'yocto' in t)),
    ('product', 'Android product / board', lambda p, n, t: n == 'AndroidProducts.mk' or fnmatch(n, 'BoardConfig*.mk')
     or ('android-device' in t and n.endswith('.mk') and n != 'Android.mk')),
    ('bp', 'Android.bp', lambda p, n, t: n == 'Android.bp'),
    ('amk', 'Android.mk', lambda p, n, t: n == 'Android.mk'),
    ('rc', 'init .rc', lambda p, n, t: 'android' in t and n.endswith('.rc')),
    ('sepolicy', 'SELinux policy', lambda p, n, t: n.endswith('.te') or n.endswith('_contexts')),
    ('hal', 'HAL / AIDL / VINTF', lambda p, n, t: n.endswith(('.hal', '.aidl'))
     or ('android' in t and re.match(r'(manifest|compatibility_matrix).*\.xml$', n))),
    ('defconfig', 'defconfig', lambda p, n, t: n.endswith('defconfig')),
    ('dts', 'Device tree', lambda p, n, t: n.endswith(('.dts', '.dtsi', '.dtso'))),
    ('kconfig', 'Kconfig', lambda p, n, t: n.startswith('Kconfig')),
    ('qnxbuild', 'QNX buildfile', lambda p, n, t: n.endswith('.build') and n != 'meson.build'),
    ('qnxmk', 'QNX common.mk / .use', lambda p, n, t: 'qnx' in t and (n == 'common.mk' or n.endswith('.use'))),
    # DevOps
    ('ci', 'CI pipeline', lambda p, n, t: n.startswith('Jenkinsfile') or n.startswith('.gitlab-ci')
     or ('/.github/workflows/' in p and n.endswith(('.yml', '.yaml'))) or '/.gitlab/ci/' in p
     or n in ('azure-pipelines.yml', 'bitbucket-pipelines.yml', '.drone.yml', '.travis.yml')),
    ('docker', 'Docker / Compose', lambda p, n, t: n == 'Dockerfile' or n.startswith('Dockerfile.')
     or n.endswith('.dockerfile') or n == '.dockerignore'
     or re.match(r'(docker-)?compose[\w.\-]*\.ya?ml$', n)),
    ('terraform', 'Terraform', lambda p, n, t: n.endswith(('.tf', '.tfvars')) or n == '.terraform.lock.hcl'),
    ('helm', 'Helm / Kubernetes', lambda p, n, t: n in ('Chart.yaml', 'kustomization.yaml')
     or ('devops' in t and '/templates/' in p and n.endswith(('.yaml', '.yml', '.tpl')))),
    ('ansible', 'Ansible', lambda p, n, t: 'devops' in t and (n == 'ansible.cfg' or (n.endswith(('.yml', '.yaml'))
     and any(d in p for d in ('/roles/', '/playbooks/', '/group_vars/', '/host_vars/', '/inventory'))))),
    # Python
    ('pydeps', 'Python project / dependencies', lambda p, n, t: n in (
        'pyproject.toml', 'setup.py', 'setup.cfg', 'Pipfile', 'Pipfile.lock', 'poetry.lock', 'tox.ini',
        'noxfile.py', 'environment.yml', 'MANIFEST.in') or re.match(r'(requirements|constraints)[\w.\-]*\.(txt|in)$', n)),
    ('pytest', 'Python tests', lambda p, n, t: ('python' in t or 'devops' in t) and n.endswith('.py')
     and (n.startswith('test_') or n.endswith('_test.py') or n == 'conftest.py' or '/tests/' in p or '/test/' in p)),
    ('py', 'Python modules', lambda p, n, t: ('python' in t or 'devops' in t) and n.endswith('.py')),
    ('shell', 'Shell scripts', lambda p, n, t: 'devops' in t and n.endswith(('.sh', '.bash'))),
    ('cmake', 'CMake', lambda p, n, t: n == 'CMakeLists.txt' or n.endswith('.cmake')),
    ('meson', 'Meson', lambda p, n, t: n in ('meson.build', 'meson_options.txt', 'meson.options')),
    ('autotools', 'Autotools', lambda p, n, t: n in ('configure.ac', 'configure.in', 'Makefile.am')),
    ('make', 'Makefile / Kbuild / .mk', lambda p, n, t: n in ('Makefile', 'makefile', 'GNUmakefile', 'Kbuild')
     or n.endswith(('.mk', '.mak'))),
    ('conf', 'Other config', lambda p, n, t: n.endswith(('.conf', '.cfg', '.ini', '.yaml', '.yml', '.toml', '.json',
                                                          '.config', '.properties'))),
]


def categorize(rel, tagstr):
    n = rel.rsplit('/', 1)[-1]
    p = '/' + rel
    for cid, _, test in CATS:
        if test(p, n, tagstr):
            return cid
    return None


# (mau ten file o goc repo, cac bien can hien) - dung o tang insight
KEYVARS = [
    ('AndroidProducts.mk', 'PRODUCT_MAKEFILES COMMON_LUNCH_CHOICES'),
    ('BoardConfig*.mk', 'TARGET_ARCH TARGET_ARCH_VARIANT TARGET_CPU_VARIANT TARGET_CPU_ABI TARGET_2ND_ARCH '
                        'TARGET_BOARD_PLATFORM TARGET_BOOTLOADER_BOARD_NAME TARGET_NO_KERNEL BOARD_KERNEL_CMDLINE '
                        'BOARD_VENDORIMAGE_FILE_SYSTEM_TYPE'),
    ('*.mk', 'PRODUCT_NAME PRODUCT_DEVICE PRODUCT_BRAND PRODUCT_MODEL PRODUCT_MANUFACTURER PRODUCT_PLATFORM '
             'TARGET_BOARD_PLATFORM'),
    ('Makefile', 'NAME LIST EARLY_DIRS LATE_DIRS USEFILE INSTALLDIR LIBS EXTRA_INCVPATH EXTRA_SRCVPATH VARIANT_LIST '
                 'ARCH CROSS_COMPILE SUBDIRS TARGET PROJECT'),
    ('common.mk', 'NAME USEFILE INSTALLDIR LIBS EXTRA_INCVPATH EXTRA_SRCVPATH EXTRA_LIBVPATH CCFLAGS PINFO'),
]

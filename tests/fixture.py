"""Tao workspace `repo sync` gia lap (Yocto + kernel + Android device + QNX + CMake) de test."""
import os
import textwrap

FILES = {
    '.repo/manifest.xml': '<manifest><include name="default.xml"/></manifest>',
    '.repo/manifests/default.xml': '''
        <manifest>
          <remote name="origin" fetch="ssh://git.example.com" revision="main"/>
          <default remote="origin" revision="dev"/>
          <project name="yocto/poky" path="layers/poky"/>
          <project name="acme/meta-acme" path="layers/meta-acme" groups="bsp"/>
          <project name="acme/foo-app" path="src/foo-app"/>
          <project name="kernel/linux" path="kernel/linux" revision="v6.1"/>
          <project name="device/acme/board"/>
          <project name="device/acme/common"/>
          <project name="qnx/drivers/spi"/>
          <project name="obsolete/thing"/>
          <remove-project name="obsolete/thing"/>
        </manifest>''',
    '.repo/local_manifests/extra.xml': '<manifest><project name="extra/tool" path="tools/extra"/></manifest>',

    # Yocto core
    'layers/poky/meta/conf/layer.conf': '''
        BBFILE_COLLECTIONS += "core"
        BBFILE_PRIORITY_core = "5"
        LAYERSERIES_COMPAT_core = "scarthgap"''',
    'layers/poky/meta/classes-recipe/cmake.bbclass': 'OECMAKE_GENERATOR ?= "Ninja"',
    'layers/poky/meta/recipes-core/busybox/busybox.inc': 'DEPENDS += "zlib"',
    'layers/poky/meta/recipes-core/busybox/busybox_1.36.bb': '''
        SUMMARY = "Busybox"
        require busybox.inc
        inherit cmake ${@bb.utils.contains('A', 'b', 'c', '', d)}
        do_install() {
            FOO = "not a var"
        }''',
    'layers/poky/bitbake/bin/bitbake': '#!/usr/bin/env python3',

    # BSP layer
    'layers/meta-acme/conf/layer.conf': '''
        BBFILE_COLLECTIONS += "acme"
        BBFILE_PRIORITY_acme = "9"
        LAYERDEPENDS_acme = "core"''',
    'layers/meta-acme/conf/machine/acme-board.conf': '''
        PREFERRED_PROVIDER_virtual/kernel = "linux-acme"
        KERNEL_DEVICETREE = "acme/board.dtb"''',
    'layers/meta-acme/recipes-core/busybox/busybox_%.bbappend': 'FILESEXTRAPATHS:prepend := "${THISDIR}/files:"',
    'layers/meta-acme/recipes-app/foo/foo_1.0.bb': '''
        SRC_URI = "git://git.example.com/acme/foo-app.git;protocol=ssh;branch=main \\
                   file://fix.patch"''',

    # CMake app
    'src/foo-app/CMakeLists.txt': 'project(foo C)\nadd_subdirectory(lib)\nadd_executable(foo main.c)\n',
    'src/foo-app/lib/CMakeLists.txt': 'add_library(foolib STATIC a.c)\n',

    # Linux kernel
    'kernel/linux/Kconfig': 'source "arch/Kconfig"\n',
    'kernel/linux/arch/Kconfig': 'config FOO\n\tbool "foo"\n',
    'kernel/linux/Makefile': 'VERSION = 6\nPATCHLEVEL = 1\nSUBLEVEL = 20\nEXTRAVERSION =\nNAME = Test\n',
    'kernel/linux/kernel/fork.c': '',
    'kernel/linux/mm/mmap.c': '',
    'kernel/linux/arch/arm64/configs/acme_defconfig': 'CONFIG_ARM64=y\n# CONFIG_X is not set\n',
    'kernel/linux/arch/arm64/boot/dts/acme/board.dts': '#include "board.dtsi"\n/ {\n\tmodel = "Acme Board";\n\tcompatible = "acme,board";\n};\n',
    'kernel/linux/arch/arm64/boot/dts/acme/board.dtsi': '/ { };\n',

    # Android device
    'device/acme/board/AndroidProducts.mk': 'PRODUCT_MAKEFILES := $(LOCAL_DIR)/acme.mk\nCOMMON_LUNCH_CHOICES := acme-userdebug\n',
    'device/acme/board/BoardConfig.mk': 'TARGET_ARCH := arm64\nTARGET_CPU_VARIANT := cortex-a76\n',
    'device/acme/board/acme.mk': '$(call inherit-product, device/acme/common/common.mk)\nPRODUCT_NAME := acme\n',
    'device/acme/board/Android.bp': '''
        // comment { not a module
        cc_library {
            name: "libacme",
            shared_libs: ["liblog", "libcutils"],
        }
        cc_binary { name: "acmed", static_libs: ["libacme"] }''',
    'device/acme/common/common.mk': 'PRODUCT_BRAND := acme\n',

    # QNX
    'qnx/drivers/spi/Makefile': 'LIST=VARIANT\ninclude recurse.mk\n',
    'qnx/drivers/spi/common.mk': 'NAME=spi-acme\nINSTALLDIR=usr/lib\ninclude $(MKFILES_ROOT)/qtargets.mk\n',
    'qnx/drivers/spi/nto/aarch64/so.le/Makefile': 'include ../../../common.mk\n',

    # C/C++ source (cho full source + #include)
    'src/foo-app/main.c': '#include <stdio.h>\n#include "foo.h"\n#include "inner/deep.h"\n#include "deep2.h"\n'
                          'int main(void) { return 0; }\n',
    'src/foo-app/include/foo.h': '#pragma once\n',
    'src/foo-app/lib/inner/deep.h': '#pragma once\n',
    'src/foo-app/lib/inner/deep2.h': '#pragma once\n',
    'src/foo-app/lib/a.c': 'int a;\n',

    # Python + DevOps repo
    'tools/extra/README.md': '# extra',
    'tools/extra/pyproject.toml': '''
        [project]
        name = "pyapp"
        version = "0.3.1"
        requires-python = ">=3.8"
        dependencies = [
          "requests>=2",
          "click",
        ]
        [project.scripts]
        pyapp = "pyapp.cli:main"
        [build-system]
        build-backend = "setuptools.build_meta"''',
    'tools/extra/requirements-dev.txt': 'pytest>=7\n-r base.txt\n',
    'tools/extra/src/pyapp/__init__.py': '',
    'tools/extra/src/pyapp/cli.py': '''
        import os
        from .core import run
        from . import util
        from pyapp.util import helper

        def main():
            run()''',
    'tools/extra/src/pyapp/core.py': 'class Runner:\n    def go(self):\n        pass\n\ndef run():\n    Runner().go()\n',
    'tools/extra/src/pyapp/util.py': 'def helper():\n    return 1\n',
    'tools/extra/tests/test_core.py': 'from pyapp.core import run\n\ndef test_run():\n    run()\n',
    'tools/extra/Dockerfile': 'FROM python:3.11-slim AS base\nCOPY . /app\nEXPOSE 8080\nCMD ["pyapp"]\n',
    'tools/extra/docker-compose.yml': 'services:\n  web:\n    build: .\n  db:\n    image: postgres\n',
    'tools/extra/.gitlab-ci.yml': '''
        stages: [build, test, deploy]
        include:
          - local: '/ci/common.yml'
        .base:
          image: python
        build:
          stage: build
        unit-test:
          stage: test''',
    'tools/extra/ci/common.yml': 'variables:\n  X: 1\n',
    'tools/extra/.github/workflows/ci.yml': 'name: CI\non: [push]\njobs:\n  lint:\n    runs-on: ubuntu\n  test:\n    runs-on: ubuntu\n',
    'tools/extra/Jenkinsfile': "pipeline {\n  stages {\n    stage('Build') { steps { sh 'make' } }\n    stage('Deploy') {}\n  }\n}\n",
    'tools/extra/main.tf': 'provider "aws" {}\nresource "aws_instance" "web" {}\nmodule "net" {\n  source = "./modules/net"\n}\n',
    'tools/extra/modules/net/main.tf': 'resource "aws_vpc" "main" {}\n',
    'tools/extra/ansible.cfg': '[defaults]\n',
    'tools/extra/site.yml': '- hosts: webservers\n  roles:\n    - common\n    - role: nginx\n',
    'tools/extra/roles/common/tasks/main.yml': '- name: x\n  debug: msg=1\n',
    'tools/extra/roles/nginx/tasks/main.yml': '- include_tasks: extra.yml\n',
    'tools/extra/roles/nginx/tasks/extra.yml': '- debug: msg=2\n',
    'tools/extra/deploy.sh': '#!/bin/sh\necho deploy\n',
}


def make_workspace(root):
    for rel, content in FILES.items():
        path = os.path.join(root, *rel.split('/'))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(textwrap.dedent(content).lstrip('\n'))
    return root

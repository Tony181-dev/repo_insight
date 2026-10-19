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

    'tools/extra/README.md': '# extra',
}


def make_workspace(root):
    for rel, content in FILES.items():
        path = os.path.join(root, *rel.split('/'))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(textwrap.dedent(content).lstrip('\n'))
    return root

# -*- coding: utf-8 -*-
#
# (C) Copyright 2022 Karellen, Inc. (https://www.karellen.co/)
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

import os
import shutil
import subprocess
import sys
import unittest
import zipfile
from email import message_from_string
from glob import glob
from importlib.metadata import PackageNotFoundError
from os.path import dirname, join as jp, exists, isdir, normpath
from tempfile import TemporaryDirectory
from sysconfig import get_platform, get_path
from unittest import mock

from packaging.requirements import Requirement
from setuptools import Extension
from setuptools.command.build_ext import build_ext as _build_ext
from setuptools.dist import Distribution

from karellen.clang_build_ext import (ClangBuildExt, LLVM_CORE_DISTRIBUTION,
                                      LLVM_CORE_RUNTIME, ORIGIN, ext_runtime_library_dirs,
                                      llvm_core_lib_dirs, llvm_core_requirement,
                                      origin_relative, pin_llvm_core)

PLATFORM = f"{get_platform()}-cpython-{sys.version_info[0]}{sys.version_info[1]}"

# Whether the LLVM toolchain is part of this Python environment rather than the host's.
# The two configurations have different contracts, so the tests select between them
# instead of assuming the one CI happens to install.
LLVM_IN_ENV = bool(llvm_core_lib_dirs())


def cxx_runtime_lib():
    """Absolute path of the C++ standard library clang++ links extensions against."""
    return subprocess.run(["clang++", "-print-file-name=libc++.so"],
                          capture_output=True, text=True, check=True).stdout.strip()


def read_runpath(elf_path):
    """DT_RUNPATH entries of an object, empty when it records none.

    Read with the toolchain's own `llvm-objdump`, which is present exactly when the
    toolchain that produced the object is.
    """
    dynamic = subprocess.run(["llvm-objdump", "-p", elf_path],
                             capture_output=True, text=True, check=True).stdout

    for line in dynamic.splitlines():
        tag, _, value = line.strip().partition(" ")
        if tag == "RUNPATH":
            return value.strip().split(":")

    return []


class ClangBuildExtTest(unittest.TestCase):
    def setUp(self) -> None:
        self.test_dir = jp(dirname(dirname(__file__)), "resources")
        self.target_dir = TemporaryDirectory()
        self.src_dir = jp(self.target_dir.name, "src")

    def tearDown(self) -> None:
        self.target_dir.cleanup()

    @property
    def build_temp(self):
        return jp(self.src_dir, "build", f"temp.{PLATFORM}")

    def copy_fixture(self, dir_name, setup_cfg=None):
        src_dir = jp(self.test_dir, dir_name)
        shutil.copytree(src_dir, self.src_dir, symlinks=True, ignore_dangling_symlinks=True)

        if setup_cfg:
            with open(jp(self.src_dir, "setup.cfg"), "w") as f:
                f.write(setup_cfg)

    def build_test(self, dir_name, setup_cfg=None, **env):
        self.copy_fixture(dir_name, setup_cfg)

        cmd = [sys.executable, "-m", "build", "--wheel", "--no-isolation"]

        full_env = dict(os.environ)
        full_env.update(env)

        result = subprocess.run(cmd, cwd=self.src_dir, env=full_env,
                                capture_output=True, text=True)
        if result.returncode != 0:
            self.fail(f"Build failed:\nstdout: {result.stdout}\nstderr: {result.stderr}")

        return result

    def assert_bc_files(self, present=True):
        check = self.assertTrue if present else self.assertFalse
        t = self.build_temp
        check(exists(jp(t, "src", "alib", "subdir1.bc")))
        check(exists(jp(t, "src", "alib", "alib.bc")))
        check(exists(jp(t, "src", "alib", "subdir", "subdir1.bc")))

        check(exists(jp(t, "src", "shlib", "shlib.bc")))

        check(exists(jp(t, "src", "module", "module.bc")))
        check(exists(jp(t, "src", "module", "subdir", "module1.bc")))

        check(exists(jp(t, "src", "alib", "deep", "deeper", "alib_deep.bc")))
        check(exists(jp(t, "src", "shlib", "deep", "deeper", "shlib_deep.bc")))
        check(exists(jp(t, "src", "module", "deep", "deeper", "module_deep.bc")))

    def assert_deep_sources_compiled(self, *src_dirs_and_stems):
        """`**` must be arbitrary-depth, not one level.

        Each of these sources lives three directories below its glob root, so it is
        matched only when glob() runs with recursive=True. Without it `**` collapses
        to a single `*` and the source is dropped from the build with no error.
        """
        t = self.build_temp
        for src_dir, stem in src_dirs_and_stems:
            obj = jp(t, "src", src_dir, "deep", "deeper", f"{stem}.o")
            self.assertTrue(exists(obj), f"{obj} missing: ** did not recurse")

    def assert_cxx_bc_files(self, present=True):
        check = self.assertTrue if present else self.assertFalse
        t = self.build_temp
        check(exists(jp(t, "src", "cxxalib", "alib.bc")))
        check(exists(jp(t, "src", "cxxalib", "subdir", "alib_sub.bc")))

        check(exists(jp(t, "src", "cxxshlib", "shlib.bc")))
        check(exists(jp(t, "src", "cxxshlib", "subdir", "shlib_sub.bc")))

        check(exists(jp(t, "src", "cxxmodule", "module.bc")))
        check(exists(jp(t, "src", "cxxmodule", "subdir", "module_sub.bc")))

        check(exists(jp(t, "src", "cxxalib", "deep", "deeper", "alib_deep.bc")))
        check(exists(jp(t, "src", "cxxshlib", "deep", "deeper", "shlib_deep.bc")))
        check(exists(jp(t, "src", "cxxmodule", "deep", "deeper", "module_deep.bc")))

    def assert_cxx_driver(self, result):
        """C++ sources must go through the clang++ driver, never the clang-cpp preprocessor."""
        output = result.stdout + result.stderr
        self.assertIn("clang++ ", output)
        self.assertNotIn("clang-cpp", output)

    def assert_links_with_lld(self, result):
        """Every link must name lld, which `executables` declares and sysconfig erases.

        `configure_system` rebuilds the linker commands from `LDSHARED`/`LDCXXSHARED` and
        keeps only the program name, so the declared `-fuse-ld=lld` reaches the command
        line only if it is merged back in. Asserted on the link lines rather than on the
        binary because a toolchain configured with lld as its default linker produces an
        lld-linked object either way, and would hide the flag going missing.
        """
        output = result.stdout + result.stderr
        links = [line for line in output.splitlines()
                 if " -shared " in line and " -o " in line]
        self.assertTrue(links, "no link command found in the build output")
        for link in links:
            self.assertIn("-fuse-ld=lld", link)

    def assert_cxx_module_works(self):
        lib_dir = jp(self.src_dir, "build", f"lib.{PLATFORM}")

        # clang++ defaults to libc++, which the LLVM toolchain ships outside the
        # loader's default search path and which the extension records as a plain
        # DT_NEEDED, so the runtime location has to be handed to the loader here.
        env = dict(os.environ)
        env["LD_LIBRARY_PATH"] = os.pathsep.join(
            filter(None, [dirname(cxx_runtime_lib()), env.get("LD_LIBRARY_PATH")]))

        result = subprocess.run([sys.executable, "-c",
                                 "import test_cxx; print(test_cxx.test())"],
                                cwd=lib_dir, env=env, capture_output=True, text=True)
        if result.returncode != 0:
            self.fail(f"Importing the built C++ extension failed:\n"
                      f"stdout: {result.stdout}\nstderr: {result.stderr}")
        self.assertEqual("hello, module (deep)", result.stdout.strip())

    def built_extension(self, ext_name):
        return jp(self.src_dir, "build", f"lib.{PLATFORM}",
                  f"{ext_name}.cpython-{sys.version_info[0]}{sys.version_info[1]}-"
                  f"{os.uname().machine}-linux-gnu.so")

    def assert_llvm_runpath(self, ext_name):
        """An extension built against an in-environment LLVM must record how to find it.

        Asserted as a property of where the entries land rather than against a literal
        string, so the test states the requirement -- the installed extension resolves
        its runtime relative to itself -- instead of restating the implementation.

        Only the origin-relative entries are ours. An interpreter linked with its own
        `-Wl,-rpath` contributes absolute ones to the same DT_RUNPATH (GitHub's hosted
        Python contributes `/opt/hostedtoolcache/.../lib`), and those are none of this
        feature's business.
        """
        prefix = f"{ORIGIN}{os.sep}"
        runpath = read_runpath(self.built_extension(ext_name))
        ours = [entry for entry in runpath if entry.startswith(prefix)]
        self.assertTrue(ours, f"no origin-relative RUNPATH entry among {runpath}")

        install_dir = get_path("platlib")
        resolved = []
        for entry in ours:
            directory = normpath(jp(install_dir, entry[len(prefix):]))
            self.assertTrue(isdir(directory), f"{entry} resolves to {directory}, missing")
            resolved.append(directory)

        self.assertTrue(any(exists(jp(d, LLVM_CORE_RUNTIME)) for d in resolved),
                        f"no RUNPATH entry of {ours} holds {LLVM_CORE_RUNTIME}")

    def pip(self, *args):
        result = subprocess.run([sys.executable, "-m", "pip", *args],
                                capture_output=True, text=True)
        if result.returncode != 0:
            self.fail(f"pip {' '.join(args)} failed:\n"
                      f"stdout: {result.stdout}\nstderr: {result.stderr}")

    def built_wheel(self):
        wheels = glob(jp(self.src_dir, "dist", "*.whl"))
        self.assertEqual(1, len(wheels), f"expected exactly one wheel, got {wheels}")
        return wheels[0]

    def wheel_metadata(self):
        with zipfile.ZipFile(self.built_wheel()) as wheel:
            name = next(n for n in wheel.namelist() if n.endswith(".dist-info/METADATA"))
            return message_from_string(wheel.read(name).decode())

    def prepared_metadata(self):
        """The metadata a front end reads before anything has been built.

        pip resolves dependencies out of this and then hands the same directory back to
        `build_wheel`, which copies it into the wheel rather than regenerating it. A pin
        that only appeared once an extension had been linked would therefore be invisible
        to the resolver and missing from the wheel pip goes on to install.
        """
        out_dir = jp(self.target_dir.name, "metadata")
        os.makedirs(out_dir)

        result = subprocess.run(
            [sys.executable, "-c",
             "import sys\n"
             "from setuptools.build_meta import prepare_metadata_for_build_wheel\n"
             "prepare_metadata_for_build_wheel(sys.argv[1])\n",
             out_dir],
            cwd=self.src_dir, capture_output=True, text=True)
        if result.returncode != 0:
            self.fail(f"Preparing metadata failed:\n"
                      f"stdout: {result.stdout}\nstderr: {result.stderr}")

        dist_infos = glob(jp(out_dir, "*.dist-info"))
        self.assertEqual(1, len(dist_infos), f"expected one dist-info, got {dist_infos}")
        with open(jp(dist_infos[0], "METADATA")) as metadata:
            return message_from_string(metadata.read())

    def assert_pins_llvm_core(self, metadata):
        """The package must require the LLVM whose runtime its extensions resolve to.

        Compared as a parsed requirement: setuptools reorders the clauses of a specifier
        on its way into the metadata, and the order is not the point.
        """
        requirements = [Requirement(req) for req in metadata.get_all("Requires-Dist") or ()]
        pins = [req for req in requirements if req.name == LLVM_CORE_DISTRIBUTION]
        self.assertEqual(1, len(pins), f"expected one {LLVM_CORE_DISTRIBUTION} requirement "
                                       f"among {[str(req) for req in requirements]}")
        self.assertEqual(Requirement(llvm_core_requirement()).specifier, pins[0].specifier)

    def assert_wheel_installs_and_imports(self, ext_name, expected):
        """The wheel's real contract: install it, import it, with nothing to lean on.

        Installed with pip rather than by copying the extension to a computed location.
        The production code decides where the RUNPATH points by asking `platlib`, so a
        test that placed the file by asking `platlib` too would agree with it even if
        `platlib` were the wrong answer -- pip is the independent authority on where a
        wheel actually lands.

        `assert_cxx_module_works` cannot cover this: it hands the loader an
        LD_LIBRARY_PATH and imports out of the build tree, where an origin-relative
        RUNPATH cannot resolve by construction. Here the environment is stripped, so
        only the RUNPATH can satisfy the import.
        """
        env = {k: v for k, v in os.environ.items() if k != "LD_LIBRARY_PATH"}

        self.pip("install", "--no-index", "--no-deps", "--force-reinstall", self.built_wheel())
        try:
            result = subprocess.run([sys.executable, "-c",
                                     f"import {ext_name}; print({ext_name}.test())"],
                                    cwd=self.target_dir.name, env=env,
                                    capture_output=True, text=True)
        finally:
            self.pip("uninstall", "-y", ext_name)

        if result.returncode != 0:
            self.fail(f"Installed wheel did not import without LD_LIBRARY_PATH:\n"
                      f"stdout: {result.stdout}\nstderr: {result.stderr}")
        self.assertEqual(expected, result.stdout.strip())

    def test_with_env_drakon(self):
        self.build_test("extension_1", DRAKON="1")
        self.assert_bc_files(present=True)

    def test_with_env_drakon_thin(self):
        self.build_test("extension_1", DRAKON="1", THIN="1")
        self.assert_bc_files(present=True)

    def test_with_env_thin(self):
        self.build_test("extension_1", THIN="1")
        self.assert_bc_files(present=False)

    def test_with_env_no_drakon_no_thin(self):
        result = self.build_test("extension_1")
        self.assert_bc_files(present=False)
        self.assert_links_with_lld(result)
        self.assert_deep_sources_compiled(("alib", "alib_deep"),
                                          ("shlib", "shlib_deep"),
                                          ("module", "module_deep"))

    @unittest.skipUnless(LLVM_IN_ENV, "LLVM is not installed in this environment")
    def test_runpath_recorded_for_c_extension(self):
        self.build_test("extension_1")
        self.assert_llvm_runpath("test")

    @unittest.skipUnless(LLVM_IN_ENV, "LLVM is not installed in this environment")
    def test_wheel_requires_the_llvm_it_was_built_against(self):
        self.build_test("extension_1")
        self.assert_pins_llvm_core(self.wheel_metadata())

    @unittest.skipUnless(LLVM_IN_ENV, "LLVM is not installed in this environment")
    def test_prepared_metadata_requires_the_llvm_it_was_built_against(self):
        self.copy_fixture("extension_1")
        self.assert_pins_llvm_core(self.prepared_metadata())

    def test_with_setup_cfg_drakon(self):
        self.build_test("extension_1", setup_cfg="[build_ext]\ndrakon = 1\n")
        self.assert_bc_files(present=True)

    def test_with_setup_cfg_drakon_thin(self):
        self.build_test("extension_1", setup_cfg="[build_ext]\ndrakon = 1\nthin = 1\n")
        self.assert_bc_files(present=True)

    def test_with_compiler_override(self):
        self.build_test("extension_1", setup_cfg="[build]\ncompiler = unix\n")
        self.assert_bc_files(present=False)

    # The extension_2 fixture guards its own build_info: its headers live in an
    # include_dirs-only directory and #error out unless the "macros" and "cflags"
    # entries of the build_clib library reach the compiler. A successful build is
    # therefore proof that build_info is passed through in full.
    def test_cxx_with_env_no_drakon_no_thin(self):
        result = self.build_test("extension_2")
        self.assert_cxx_driver(result)
        self.assert_links_with_lld(result)
        self.assert_cxx_bc_files(present=False)
        self.assert_deep_sources_compiled(("cxxalib", "alib_deep"),
                                          ("cxxshlib", "shlib_deep"),
                                          ("cxxmodule", "module_deep"))
        self.assert_cxx_module_works()

    @unittest.skipUnless(LLVM_IN_ENV, "LLVM is not installed in this environment")
    def test_cxx_runpath_resolves_libcxx_without_ld_library_path(self):
        self.build_test("extension_2")
        self.assert_llvm_runpath("test_cxx")
        self.assert_wheel_installs_and_imports("test_cxx", "hello, module (deep)")

    def test_cxx_with_env_drakon(self):
        result = self.build_test("extension_2", DRAKON="1")
        self.assert_cxx_driver(result)
        self.assert_cxx_bc_files(present=True)
        self.assert_cxx_module_works()

    def test_cxx_with_env_drakon_thin(self):
        result = self.build_test("extension_2", DRAKON="1", THIN="1")
        self.assert_cxx_driver(result)
        self.assert_cxx_bc_files(present=True)
        self.assert_cxx_module_works()

    def test_cxx_with_setup_cfg_drakon(self):
        result = self.build_test("extension_2", setup_cfg="[build_ext]\ndrakon = 1\n")
        self.assert_cxx_driver(result)
        self.assert_cxx_bc_files(present=True)
        self.assert_cxx_module_works()


class LlvmRunpathTest(unittest.TestCase):
    """The RUNPATH decision itself, exercised without spawning a build.

    Covers the branch the integration builds cannot reach on a machine that has LLVM
    installed: a host toolchain, where the C++ runtime is already on the loader's
    default search path and the spec is to emit nothing at all.
    """

    def test_no_lib_dirs_when_llvm_is_not_in_the_environment(self):
        with mock.patch("karellen.clang_build_ext.distribution",
                        side_effect=PackageNotFoundError(LLVM_CORE_DISTRIBUTION)):
            self.assertEqual([], llvm_core_lib_dirs())
            self.assertEqual([], ext_runtime_library_dirs("test"))
            self.assertEqual([], ext_runtime_library_dirs("pkg.sub.test"))

    def test_no_lib_dirs_when_the_runtime_is_missing_from_the_distribution(self):
        unrelated = mock.Mock()
        unrelated.files = [mock.Mock(name="a"), mock.Mock(name="b")]
        for file in unrelated.files:
            file.name = "libLLVM.so.23.1"
        with mock.patch("karellen.clang_build_ext.distribution", return_value=unrelated):
            self.assertEqual([], llvm_core_lib_dirs())

    def test_lib_dirs_pair_the_plain_and_per_target_directories(self):
        if not LLVM_IN_ENV:
            self.skipTest("LLVM is not installed in this environment")

        lib_dir, target_dir = llvm_core_lib_dirs()
        self.assertEqual(lib_dir, dirname(target_dir))
        self.assertTrue(exists(jp(target_dir, LLVM_CORE_RUNTIME)))

    def test_origin_relative_measures_from_the_given_directory(self):
        dirs = ["/opt/env/lib", "/opt/env/lib/x86_64-unknown-linux-gnu"]
        self.assertEqual([f"{ORIGIN}/../../../lib",
                          f"{ORIGIN}/../../../lib/x86_64-unknown-linux-gnu"],
                         origin_relative(dirs, "/opt/env/lib64/python3.14/site-packages"))

    def test_origin_relative_accounts_for_package_depth(self):
        """A dotted extension name installs deeper, so it needs to climb further."""
        if not LLVM_IN_ENV:
            self.skipTest("LLVM is not installed in this environment")

        top_level = ext_runtime_library_dirs("test")
        nested = ext_runtime_library_dirs("pkg.sub.test")

        self.assertEqual(len(top_level), len(nested))
        for shallow, deep in zip(top_level, nested):
            # Two package directories deeper, so two more levels to climb back out of.
            self.assertEqual(jp(ORIGIN, "..", "..", shallow[len(ORIGIN) + 1:]), deep)


class LlvmCorePinTest(unittest.TestCase):
    """The dependency a package declares on the LLVM sitting behind its RUNPATH.

    Exercised against a real `Distribution`, because what is under test is where the
    requirement has to land for setuptools to write it out, and a stub would agree with
    whatever this module happened to assign.
    """

    @staticmethod
    def installed_llvm_core(version):
        """Stand-in for the installed distribution, the runtime among its files."""
        files = [mock.Mock(), mock.Mock()]
        files[0].name = "libLLVM.so.23.1"
        files[1].name = LLVM_CORE_RUNTIME

        dist = mock.Mock()
        dist.version = version
        dist.files = files
        dist.locate_file = lambda file: f"/opt/env/lib/x86_64-unknown-linux-gnu/{file.name}"
        return dist

    @staticmethod
    def distribution(**attrs):
        attrs.setdefault("name", "ext")
        attrs.setdefault("version", "1.0.0")
        attrs.setdefault("ext_modules", [Extension("one", ["one.c"]),
                                         Extension("two", ["two.c"])])
        attrs.setdefault("cmdclass", {"build_ext": ClangBuildExt})
        attrs.setdefault("install_requires", ["certifi", "idna"])
        return Distribution(attrs)

    def pin(self, dist, version="23.1.0.post10"):
        with mock.patch("karellen.clang_build_ext.distribution",
                        return_value=self.installed_llvm_core(version)):
            pin_llvm_core(dist)

    def test_requirement_floors_at_the_installed_version_and_stops_below_the_next_major(self):
        """A `.postN` counts commits of compiler source, so it belongs in the floor."""
        for version in ("23.1.0.post10", "23.1.0"):
            with mock.patch("karellen.clang_build_ext.distribution",
                            return_value=self.installed_llvm_core(version)):
                self.assertEqual(f"{LLVM_CORE_DISTRIBUTION}>={version},<24",
                                 llvm_core_requirement())

    def test_no_requirement_when_llvm_is_not_in_the_environment(self):
        with mock.patch("karellen.clang_build_ext.distribution",
                        side_effect=PackageNotFoundError(LLVM_CORE_DISTRIBUTION)):
            self.assertIsNone(llvm_core_requirement())

    def test_pin_joins_the_declared_requirements_and_is_applied_once(self):
        dist = self.distribution()
        self.pin(dist)
        self.pin(dist)

        expected = ["certifi", "idna", f"{LLVM_CORE_DISTRIBUTION}>=23.1.0.post10,<24"]
        self.assertEqual(expected, dist.install_requires)
        # PKG-INFO -- and so the wheel's METADATA -- is written from the metadata copy,
        # which by this point is a separate object from the list above.
        self.assertEqual(expected, dist.metadata.install_requires)

    def test_no_pin_for_a_distribution_without_extensions(self):
        dist = self.distribution(ext_modules=[])
        self.pin(dist)
        self.assertEqual(["certifi", "idna"], dist.install_requires)

    def test_no_pin_when_the_extensions_are_built_by_something_else(self):
        dist = self.distribution(cmdclass={"build_ext": _build_ext})
        self.pin(dist)
        self.assertEqual(["certifi", "idna"], dist.install_requires)


if __name__ == "__main__":
    unittest.main()

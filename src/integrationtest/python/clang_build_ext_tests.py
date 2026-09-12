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

import io
import logging
import os
import shutil
import subprocess
import sys
import traceback
import unittest
from contextlib import contextmanager, redirect_stdout, redirect_stderr
from os.path import dirname, join as jp, exists
from tempfile import TemporaryDirectory
from sysconfig import get_platform

PLATFORM = f"{get_platform()}-cpython-{sys.version_info[0]}{sys.version_info[1]}"


def cxx_runtime_lib():
    """Absolute path of the C++ standard library clang++ links extensions against."""
    return subprocess.run(["clang++", "-print-file-name=libc++.so"],
                          capture_output=True, text=True, check=True).stdout.strip()


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

    @contextmanager
    def build_context(self, env):
        """Run a build in this process with `env` applied, capturing everything it emits.

        The build has to happen in-process: driving it through a `python -m build`
        subprocess puts the plugin in a grandchild that the coverage tracer never
        sees, which reports every line of karellen.clang_build_ext as unexecuted.

        Output arrives two ways depending on the setuptools version -- older ones
        write straight to stdout, newer ones log the compiler command lines through
        the root logger (`_distutils/_log.py` is `logging.getLogger()`) -- so both
        are funnelled into one buffer.
        """
        output = io.StringIO()
        handler = logging.StreamHandler(output)
        root_logger = logging.getLogger()
        old_level = root_logger.level
        root_logger.addHandler(handler)
        root_logger.setLevel(logging.DEBUG)

        old_cwd = os.getcwd()
        old_env = dict(os.environ)
        old_argv = list(sys.argv)
        old_path = list(sys.path)
        try:
            os.chdir(self.src_dir)
            os.environ.update(env)
            with redirect_stdout(output), redirect_stderr(output):
                yield output
        finally:
            root_logger.removeHandler(handler)
            root_logger.setLevel(old_level)
            os.chdir(old_cwd)
            os.environ.clear()
            os.environ.update(old_env)
            sys.argv[:] = old_argv
            sys.path[:] = old_path

    def build_test(self, dir_name, setup_cfg=None, **env):
        src_dir = jp(self.test_dir, dir_name)
        shutil.copytree(src_dir, self.src_dir, symlinks=True, ignore_dangling_symlinks=True)

        if setup_cfg:
            with open(jp(self.src_dir, "setup.cfg"), "w") as f:
                f.write(setup_cfg)

        wheel_dir = jp(self.target_dir.name, "wheel")
        os.makedirs(wheel_dir, exist_ok=True)

        # Call the PEP 517 hook directly rather than shelling out to a front end:
        # same entry point and the same env/setup.cfg-only configuration surface,
        # but it executes where coverage can observe it.
        from setuptools import build_meta

        try:
            with self.build_context(env) as output:
                build_meta.build_wheel(wheel_dir)
        except Exception:
            self.fail(f"Build failed:\n{output.getvalue()}\n"
                      f"{traceback.format_exc()}")

        return output.getvalue()

    def assert_bc_files(self, present=True):
        check = self.assertTrue if present else self.assertFalse
        t = self.build_temp
        check(exists(jp(t, "src", "alib", "subdir1.bc")))
        check(exists(jp(t, "src", "alib", "alib.bc")))
        check(exists(jp(t, "src", "alib", "subdir", "subdir1.bc")))

        check(exists(jp(t, "src", "shlib", "shlib.bc")))

        check(exists(jp(t, "src", "module", "module.bc")))
        check(exists(jp(t, "src", "module", "subdir", "module1.bc")))

    def assert_cxx_bc_files(self, present=True):
        check = self.assertTrue if present else self.assertFalse
        t = self.build_temp
        check(exists(jp(t, "src", "cxxalib", "alib.bc")))
        check(exists(jp(t, "src", "cxxalib", "subdir", "alib_sub.bc")))

        check(exists(jp(t, "src", "cxxshlib", "shlib.bc")))
        check(exists(jp(t, "src", "cxxshlib", "subdir", "shlib_sub.bc")))

        check(exists(jp(t, "src", "cxxmodule", "module.bc")))
        check(exists(jp(t, "src", "cxxmodule", "subdir", "module_sub.bc")))

    def assert_cxx_driver(self, output):
        """C++ sources must go through the clang++ driver, never the clang-cpp preprocessor."""
        self.assertIn("clang++ ", output)
        self.assertNotIn("clang-cpp", output)

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
        self.assertEqual("hello, module", result.stdout.strip())

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
        self.build_test("extension_1")
        self.assert_bc_files(present=False)

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
        self.assert_cxx_bc_files(present=False)
        self.assert_cxx_module_works()

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


if __name__ == "__main__":
    unittest.main()

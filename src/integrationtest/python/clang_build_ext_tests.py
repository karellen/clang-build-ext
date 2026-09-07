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

    def build_test(self, dir_name, setup_cfg=None, **env):
        src_dir = jp(self.test_dir, dir_name)
        shutil.copytree(src_dir, self.src_dir, symlinks=True, ignore_dangling_symlinks=True)

        if setup_cfg:
            with open(jp(self.src_dir, "setup.cfg"), "w") as f:
                f.write(setup_cfg)

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

    def assert_cxx_bc_files(self, present=True):
        check = self.assertTrue if present else self.assertFalse
        t = self.build_temp
        check(exists(jp(t, "src", "cxxalib", "alib.bc")))
        check(exists(jp(t, "src", "cxxalib", "subdir", "alib_sub.bc")))

        check(exists(jp(t, "src", "cxxshlib", "shlib.bc")))
        check(exists(jp(t, "src", "cxxshlib", "subdir", "shlib_sub.bc")))

        check(exists(jp(t, "src", "cxxmodule", "module.bc")))
        check(exists(jp(t, "src", "cxxmodule", "subdir", "module_sub.bc")))

    def assert_cxx_driver(self, result):
        """C++ sources must go through the clang++ driver, never the clang-cpp preprocessor."""
        output = result.stdout + result.stderr
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

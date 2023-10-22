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
import runpy
import shutil
import sys
import unittest
from os.path import dirname, join as jp, exists
from tempfile import TemporaryDirectory
from sysconfig import get_platform, get_python_version

PLATFORM = f"{get_platform()}-cpython-{sys.version_info[0]}{sys.version_info[1]}"


class ClangBuildExtTest(unittest.TestCase):
    def setUp(self) -> None:
        self.test_dir = jp(dirname(dirname(__file__)), "resources")
        self.target_dir = TemporaryDirectory()
        self.src_dir = jp(self.target_dir.name, "src")
        self.build_dir = jp(self.target_dir.name, "build")
        self.temp_dir = jp(self.target_dir.name, "temp")
        self.wheels = set()

    def tearDown(self) -> None:
        self.target_dir.cleanup()
        for wheel_file in list(self.wheels):
            try:
                self.uninstall(wheel_file)
            except Exception:
                sys.excepthook(*sys.exc_info())

    def build_test(self, dir_name, *extra_args, **env):
        src_dir = jp(self.test_dir, dir_name)
        shutil.copytree(src_dir, self.src_dir, symlinks=True, ignore_dangling_symlinks=True)

        old_env = dict(os.environ)
        old_sys_argv = list(sys.argv)
        old_cwd = os.getcwd()
        try:
            script_path = jp(self.src_dir, "setup.py")
            sys.argv.clear()
            sys.argv.extend([script_path] + list(extra_args) +
                            ["-b", self.build_dir,
                             "-t", self.temp_dir])
            os.chdir(self.src_dir)
            os.environ.update(env)
            runpy.run_path(script_path)
        finally:
            os.chdir(old_cwd)
            sys.argv.clear()
            sys.argv.extend(old_sys_argv)
            os.environ.clear()
            os.environ.update(old_env)

    def test_with_cmd_line_drakon(self):
        self.build_test("extension_1", "build_clib", "build_ext", "-d")

        self.assertTrue(exists(f"{self.src_dir}/build/temp.{PLATFORM}/src/alib/subdir1.bc"))
        self.assertTrue(exists(f"{self.src_dir}/build/temp.{PLATFORM}/src/alib/alib.bc"))
        self.assertTrue(exists(f"{self.src_dir}/build/temp.{PLATFORM}/src/alib/subdir/subdir1.bc"))

        self.assertTrue(exists(f"{self.temp_dir}/src/shlib/shlib.bc"))

        self.assertTrue(exists(f"{self.temp_dir}/src/module/module.bc"))
        self.assertTrue(exists(f"{self.temp_dir}/src/module/subdir/module1.bc"))

    def test_with_cmd_line_drakon_thin(self):
        self.build_test("extension_1", "build_clib", "build_ext", "-d", "-T")

        self.assertTrue(exists(f"{self.src_dir}/build/temp.{PLATFORM}/src/alib/subdir1.bc"))
        self.assertTrue(exists(f"{self.src_dir}/build/temp.{PLATFORM}/src/alib/alib.bc"))
        self.assertTrue(exists(f"{self.src_dir}/build/temp.{PLATFORM}/src/alib/subdir/subdir1.bc"))

        self.assertTrue(exists(f"{self.temp_dir}/src/shlib/shlib.bc"))

        self.assertTrue(exists(f"{self.temp_dir}/src/module/module.bc"))
        self.assertTrue(exists(f"{self.temp_dir}/src/module/subdir/module1.bc"))

    def test_with_cmd_line_no_drakon_thin(self):
        self.build_test("extension_1", "build_clib", "build_ext", "-T")

        self.assertFalse(exists(f"{self.src_dir}/build/temp.{PLATFORM}/src/alib/subdir1.bc"))
        self.assertFalse(exists(f"{self.src_dir}/build/temp.{PLATFORM}/src/alib/alib.bc"))
        self.assertFalse(exists(f"{self.src_dir}/build/temp.{PLATFORM}/src/alib/subdir/subdir1.bc"))

        self.assertFalse(exists(f"{self.temp_dir}/src/shlib/shlib.bc"))

        self.assertFalse(exists(f"{self.temp_dir}/src/module/module.bc"))
        self.assertFalse(exists(f"{self.temp_dir}/src/module/subdir/module1.bc"))

    def test_with_env_drakon(self):
        self.build_test("extension_1", "build_clib", "build_ext", DRAKON="1")

        self.assertTrue(exists(f"{self.src_dir}/build/temp.{PLATFORM}/src/alib/subdir1.bc"))
        self.assertTrue(exists(f"{self.src_dir}/build/temp.{PLATFORM}/src/alib/alib.bc"))
        self.assertTrue(exists(f"{self.src_dir}/build/temp.{PLATFORM}/src/alib/subdir/subdir1.bc"))

        self.assertTrue(exists(f"{self.temp_dir}/src/shlib/shlib.bc"))

        self.assertTrue(exists(f"{self.temp_dir}/src/module/module.bc"))
        self.assertTrue(exists(f"{self.temp_dir}/src/module/subdir/module1.bc"))

    def test_with_env_thin(self):
        self.build_test("extension_1", "build_clib", "build_ext", THIN="1")

        self.assertFalse(exists(f"{self.src_dir}/build/temp.{PLATFORM}/src/alib/subdir1.bc"))
        self.assertFalse(exists(f"{self.src_dir}/build/temp.{PLATFORM}/src/alib/alib.bc"))
        self.assertFalse(exists(f"{self.src_dir}/build/temp.{PLATFORM}/src/alib/subdir/subdir1.bc"))

        self.assertFalse(exists(f"{self.temp_dir}/src/shlib/shlib.bc"))

        self.assertFalse(exists(f"{self.temp_dir}/src/module/module.bc"))
        self.assertFalse(exists(f"{self.temp_dir}/src/module/subdir/module1.bc"))

    def test_with_cmd_line_no_drakon_no_thin(self):
        self.build_test("extension_1", "build_clib", "build_ext")

        self.assertFalse(exists(f"{self.src_dir}/build/temp.{PLATFORM}/src/alib/subdir1.bc"))
        self.assertFalse(exists(f"{self.src_dir}/build/temp.{PLATFORM}/src/alib/alib.bc"))
        self.assertFalse(exists(f"{self.src_dir}/build/temp.{PLATFORM}/src/alib/subdir/subdir1.bc"))

        self.assertFalse(exists(f"{self.temp_dir}/src/shlib/shlib.bc"))

        self.assertFalse(exists(f"{self.temp_dir}/src/module/module.bc"))
        self.assertFalse(exists(f"{self.temp_dir}/src/module/subdir/module1.bc"))

    def test_clib_with_cmd_line_drakon(self):
        self.build_test("extension_1", "build_clib", "-d")

        self.assertTrue(exists(f"{self.temp_dir}/src/alib/subdir1.bc"))
        self.assertTrue(exists(f"{self.temp_dir}/src/alib/alib.bc"))
        self.assertTrue(exists(f"{self.temp_dir}/src/alib/subdir/subdir1.bc"))

        self.assertFalse(exists(f"{self.temp_dir}/src/shlib/shlib.bc"))

        self.assertFalse(exists(f"{self.temp_dir}/src/module/module.bc"))
        self.assertFalse(exists(f"{self.temp_dir}/src/module/subdir/module1.bc"))

    def test_clib_with_cmd_line_drakon_thin(self):
        self.build_test("extension_1", "build_clib", "-d", "-T")

        self.assertTrue(exists(f"{self.temp_dir}/src/alib/subdir1.bc"))
        self.assertTrue(exists(f"{self.temp_dir}/src/alib/alib.bc"))
        self.assertTrue(exists(f"{self.temp_dir}/src/alib/subdir/subdir1.bc"))

        self.assertFalse(exists(f"{self.temp_dir}/src/shlib/shlib.bc"))

        self.assertFalse(exists(f"{self.temp_dir}/src/module/module.bc"))
        self.assertFalse(exists(f"{self.temp_dir}/src/module/subdir/module1.bc"))

    def test_with_compiler_override(self):
        self.build_test("extension_1", "build_clib", "build_ext", "-c", "unix")

        self.assertFalse(exists(f"{self.src_dir}/build/temp.{PLATFORM}/src/alib/subdir1.bc"))
        self.assertFalse(exists(f"{self.src_dir}/build/temp.{PLATFORM}/src/alib/alib.bc"))
        self.assertFalse(exists(f"{self.src_dir}/build/temp.{PLATFORM}/src/alib/subdir/subdir1.bc"))

        self.assertFalse(exists(f"{self.temp_dir}/src/shlib/shlib.bc"))

        self.assertFalse(exists(f"{self.temp_dir}/src/module/module.bc"))
        self.assertFalse(exists(f"{self.temp_dir}/src/module/subdir/module1.bc"))
if __name__ == "__main__":
    unittest.main()

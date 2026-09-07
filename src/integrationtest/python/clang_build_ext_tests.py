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

    def assert_bc_files(self, present=True):
        check = self.assertTrue if present else self.assertFalse
        t = self.build_temp
        check(exists(jp(t, "src", "alib", "subdir1.bc")))
        check(exists(jp(t, "src", "alib", "alib.bc")))
        check(exists(jp(t, "src", "alib", "subdir", "subdir1.bc")))

        check(exists(jp(t, "src", "shlib", "shlib.bc")))

        check(exists(jp(t, "src", "module", "module.bc")))
        check(exists(jp(t, "src", "module", "subdir", "module1.bc")))

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


if __name__ == "__main__":
    unittest.main()

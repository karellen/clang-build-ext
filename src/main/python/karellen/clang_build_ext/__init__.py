# -*- coding: utf-8 -*-
#
# (C) Copyright 2023 Karellen, Inc. (https://www.karellen.co/)
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
import subprocess
import sys
from contextlib import contextmanager
from distutils import ccompiler
from distutils import log
from distutils.debug import DEBUG
from distutils.errors import DistutilsExecError
from distutils.spawn import find_executable
from distutils.unixccompiler import UnixCCompiler
from distutils.util import split_quoted
from glob import glob
from os.path import exists, dirname, commonpath
from tempfile import TemporaryDirectory

from setuptools.command.build_clib import build_clib as _build_clib
from setuptools.command.build_ext import build_ext as _build_ext

COMMON_OPTIONS = [
    ("drakon", "d",
     "build extension with Drakon enhancements"),
    ("thin", "T",
     "build thin static libraries")
]

COMMON_BOOLEAN_OPTIONS = [
    "drakon", "thin"
]


class ClangCCompiler(UnixCCompiler):
    executables = {
        'preprocessor': ["clang", "-E"],
        'compiler': ["clang"],
        'compiler_so': ["clang"],
        'compiler_cxx': ["clang-cpp"],
        'linker_so': ["clang", "-shared", "-fuse-ld=lld"],
        'linker_exe': ["clang", "-fuse-ld=lld"],
        'archiver': ["llvm-ar", "rcs"],
        'ranlib': None,
        'objcopy': ["llvm-objcopy"],
        'readelf': ["llvm-readelf"]
    }

    def __init__(self, verbose=0, dry_run=0, force=0, drakon=False, thin=False):
        self.drakon = drakon
        self.thin = thin
        super().__init__(verbose, dry_run, force)
        self.verbose = verbose or False

    def link(
            self,
            target_desc,
            objects,
            output_filename,
            output_dir=None,
            libraries=None,
            library_dirs=None,
            runtime_library_dirs=None,
            export_symbols=None,
            debug=0,
            extra_preargs=None,
            extra_postargs=None,
            build_temp=None,
            target_lang=None,
    ):
        super().link(target_desc, objects, output_filename, output_dir, libraries, library_dirs, runtime_library_dirs,
                     export_symbols, debug, extra_preargs, extra_postargs, build_temp, target_lang)

        if not self.drakon:
            return

        libraries, library_dirs, runtime_library_dirs = self._fix_lib_args(libraries,
                                                                           library_dirs,
                                                                           runtime_library_dirs)

        def get_section_name(lib_name, lib_file, source_lib):
            common_path = commonpath((lib_file, source_lib))
            if common_path and not common_path.endswith(os.sep):
                common_path += os.sep
            return f"{lib_name}//{lib_file[len(common_path):]}"

        add_bc_files = {}
        for obj in objects:
            bc_file = f"{obj[:-2]}.bc"
            log.debug("Adding %s", bc_file)
            add_bc_files[bc_file] = get_section_name("", bc_file, commonpath(objects))

        lib_extract_paths = {}
        try:
            archiver = self.archiver[0]
            for lib in libraries:
                for lib_dir in library_dirs:
                    lib_path = f"{lib_dir}{os.sep}lib{lib}.a"
                    if exists(lib_path):
                        thin_lib = False
                        with open(lib_path, "rb") as f:
                            if f.read(7) == b"!<thin>":
                                thin_lib = True

                        lib_extract_paths[lib_path] = lib_extract_dir = TemporaryDirectory()
                        log.debug(f"Processing {'thin ' if thin_lib else ''}library %s", lib_path)
                        lib_files = self.spawn_out([archiver, "t", lib_path]).splitlines()
                        if thin_lib:
                            for lib_file in lib_files:
                                if not lib_file.endswith(".bc"):
                                    continue
                                log.debug("Adding %s", lib_file)
                                add_bc_files[lib_file] = get_section_name(lib, lib_file, lib_path)
                        else:
                            files_in_ar = {}
                            for l in lib_files:
                                if l in files_in_ar:
                                    files_in_ar[l] += 1
                                else:
                                    files_in_ar[l] = 1

                            for file in files_in_ar.keys():
                                if not file.endswith(".bc"):
                                    continue

                                count = files_in_ar[file]
                                for i in range(1, count + 1):
                                    extracted_name = f"{lib_extract_dir.name}{os.sep}{file[0:-3]}" \
                                                     f"{f'.{i!s}' if count > 1 else ''}.bc"
                                    log.debug("Extracting %s", extracted_name)
                                    parents_dir = dirname(file)
                                    os.makedirs(f"{lib_extract_dir.name}{os.sep}{parents_dir}", exist_ok=True)
                                    self.spawn(
                                        [archiver, "--output", lib_extract_dir.name, "xN", str(i), lib_path, file])
                                    if count > 1:
                                        self.move_file(f"{lib_extract_dir.name}{os.sep}{file}", extracted_name)
                                    add_bc_files[extracted_name] = get_section_name(lib,
                                                                                    extracted_name[len(
                                                                                        lib_extract_dir.name) + 1:],
                                                                                    lib_path)

                        break

            cmd_line = self.objcopy[:]
            for bc_file, bc_name in add_bc_files.items():
                section_name = f".drakon.{bc_name}"
                cmd_line.extend(["--add-section", f"{section_name}={bc_file}",
                                 "--set-section-flags", f"{section_name}=noload,readonly,contents"])
            cmd_line.append(output_filename)
            self.spawn(cmd_line)

        finally:
            for lib, path in lib_extract_paths.items():
                path.cleanup()

    def create_static_lib(self, objects, output_libname, output_dir=None, debug=0, target_lang=None):
        # Add all the bytecode into the ar library
        if self.drakon:
            new_objects = objects[:]
            for obj in objects:
                new_objects.append(f"{obj[:-2]}.bc")
            objects = new_objects

        super().create_static_lib(objects, output_libname, output_dir, debug, target_lang)

    def _get_cc_args(self, pp_opts, debug, before):
        cc_args = super()._get_cc_args(pp_opts, debug, before)
        if self.drakon:
            cc_args = ["--save-temps=obj", "-fno-discard-value-names"] + cc_args
        return cc_args

    def set_executable(self, key, value):
        if isinstance(value, str):
            value = split_quoted(value)

            default_executable = self.executables.get(key)
            if default_executable:
                value[0] = default_executable[0]

        if self.thin and key == "archiver":
            value = value[:]
            value.append("--thin")

        setattr(self, key, value)

    def spawn_out(self, cmd, search_path=1, verbose=0, dry_run=0,
                  env=None, text=True, stdout=subprocess.PIPE):  # pragma: no cover
        """Run another program, specified as a command list 'cmd', in a new process.

        'cmd' is just the argument list for the new process, ie.
        cmd[0] is the program to run and cmd[1:] are the rest of its arguments.
        There is no way to run a program with a name different from that of its
        executable.

        If 'search_path' is true (the default), the system's executable
        search path will be used to find the program; otherwise, cmd[0]
        must be the exact path to the executable.  If 'dry_run' is true,
        the command will not actually be run.

        Raise DistutilsExecError if running the program fails in any way; just
        return on success.
        """
        # cmd is documented as a list, but just in case some code passes a tuple
        # in, protect our %-formatting code against horrible death
        cmd = list(cmd)

        log.info(subprocess.list2cmdline(cmd))
        if dry_run:
            return

        if search_path:
            executable = find_executable(cmd[0])
            if executable is not None:
                cmd[0] = executable

        env = env if env is not None else dict(os.environ)

        if sys.platform == 'darwin':
            from distutils.util import MACOSX_VERSION_VAR, get_macosx_target_ver

            macosx_target_ver = get_macosx_target_ver()
            if macosx_target_ver:
                env[MACOSX_VERSION_VAR] = macosx_target_ver

        try:
            proc = subprocess.run(cmd, env=env,
                                  stdout=stdout, check=False, universal_newlines=text)
            exitcode = proc.returncode
        except OSError as exc:
            if not DEBUG:
                cmd = cmd[0]
            raise DistutilsExecError(
                "command {!r} failed: {}".format(cmd, exc.args[-1])
            ) from exc

        if exitcode:
            if not DEBUG:
                cmd = cmd[0]
            raise DistutilsExecError(
                "command {!r} failed with exit code {}".format(cmd, exitcode)
            )
        if stdout == subprocess.PIPE:
            return proc.stdout


class ClangBuildExt(_build_ext):
    user_options = list(_build_ext.user_options) + COMMON_OPTIONS
    boolean_options = list(_build_ext.boolean_options) + COMMON_BOOLEAN_OPTIONS

    def initialize_options(self) -> None:
        super().initialize_options()

        self.drakon = None
        self.thin = None

    def finalize_options(self) -> None:
        with self.customized_compiler():
            self.set_undefined_options(
                'build',
                ('compiler', 'compiler'),
            )

            if self.compiler is None:
                self.compiler = "clang"

            if self.drakon is None:
                self.drakon = os.environ.get("DRAKON", False)

            if self.thin is None:
                self.thin = os.environ.get("THIN", False)

            super().finalize_options()

    def run(self):
        with self.customized_compiler():
            super().run()

    def build_extension(self, ext):
        sources = ext.sources
        try:
            ext.sources = []
            for src in sources:
                ext.sources.extend(glob(src))
            super().build_extension(ext)
        finally:
            ext.source = sources

    def new_compiler(self, plat=None, compiler=None, verbose=0, dry_run=0, force=0):
        if compiler == "clang":
            return ClangCCompiler(None, dry_run, force, drakon=self.drakon, thin=self.thin)
        return self._old_new_compiler(plat, compiler, verbose, dry_run, force)

    @contextmanager
    def customized_compiler(self):
        self._old_new_compiler = _old_new_compiler = ccompiler.new_compiler
        ccompiler.new_compiler = self.new_compiler
        from setuptools.command import build_ext
        _old_build_ext_new_compiler = build_ext.new_compiler
        build_ext.new_compiler = self.new_compiler

        try:
            yield
        finally:
            ccompiler.new_compiler = _old_new_compiler
            build_ext.new_compiler = _old_build_ext_new_compiler


class ClangBuildClib(_build_clib):
    user_options = list(_build_clib.user_options) + COMMON_OPTIONS
    boolean_options = list(_build_clib.boolean_options) + COMMON_BOOLEAN_OPTIONS

    def initialize_options(self) -> None:
        super().initialize_options()
        self.drakon = None
        self.thin = None

    def finalize_options(self) -> None:
        self.set_undefined_options(
            'build_ext',
            ('drakon', 'drakon'),
            ('thin', 'thin'),
            ('compiler', 'compiler')
        )

        with self.customized_compiler():
            super().finalize_options()

    def new_compiler(self, plat=None, compiler=None, verbose=0, dry_run=0, force=0):
        if compiler == "clang":
            return ClangCCompiler(None, dry_run, force, drakon=self.drakon, thin=self.thin)
        return self._old_new_compiler(plat, compiler, verbose, dry_run, force)

    @contextmanager
    def customized_compiler(self):
        from distutils import ccompiler
        self._old_new_compiler = _old_new_compiler = ccompiler.new_compiler
        ccompiler.new_compiler = self.new_compiler
        try:
            yield
        finally:
            ccompiler.new_compiler = _old_new_compiler

    def run(self):
        with self.customized_compiler():
            super().run()

    def build_libraries(self, libraries):
        new_libraries = []
        for lib_name, sources_map in libraries:
            sources = sources_map.get("sources")
            if sources:
                new_sources = []
                for src in sources:
                    new_sources.extend(glob(src))
                new_libraries.append((lib_name, {"sources": new_sources}))

        super().build_libraries(new_libraries)

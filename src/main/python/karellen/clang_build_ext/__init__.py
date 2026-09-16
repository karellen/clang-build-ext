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

import inspect
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
from importlib.metadata import PackageNotFoundError, distribution
from os.path import exists, dirname, commonpath, normpath, join, relpath
from sysconfig import get_path
from tempfile import TemporaryDirectory

from setuptools.command.build_clib import build_clib as _build_clib
from setuptools.command.build_ext import build_ext as _build_ext

_has_dry_run = 'dry_run' in inspect.signature(ccompiler.new_compiler).parameters

COMMON_OPTIONS = [
    ("drakon", "d",
     "build extension with Drakon enhancements"),
    ("thin", "T",
     "build thin static libraries")
]

COMMON_BOOLEAN_OPTIONS = [
    "drakon", "thin"
]

# The distribution shipping the LLVM shared libraries, and the runtime whose location
# within it anchors the search for their directory.
LLVM_CORE_DISTRIBUTION = "karellen-llvm-core"
LLVM_CORE_RUNTIME = "libc++.so.1"

# The loader's token for "the directory this object was loaded from". ld.so accepts it
# either bare or braced, and braced is the deliberate choice: PyBuilder's
# `filter_resources` runs `string.Template.safe_substitute` over this module on its way
# into the distribution, and rewrites the bare form into exactly this one. Spelling it
# braced here keeps the shipped file byte-identical to the one the tests exercise --
# which is also why no bare form appears anywhere above, comments included.
ORIGIN = "${ORIGIN}"


def expand_sources(sources):
    """Expand source glob patterns, with `**` matching any depth.

    Duplicates are dropped, keeping first-seen order. Recursive `**` also matches the
    glob root, so the customary `["src/*.c", "src/**/*.c"]` pairing reports every
    top-level source twice, and compiling one source into the same object twice makes
    the link fail with duplicate symbols.
    """
    expanded = []
    seen = set()
    for src in sources:
        for path in glob(src, recursive=True):
            key = normpath(path)
            if key not in seen:
                seen.add(key)
                expanded.append(path)
    return expanded


def llvm_core_lib_dirs():
    """Directories holding the LLVM shared libraries, when LLVM lives in this environment.

    An empty result means a system toolchain will be used. Its C++ runtime is already on
    the loader's default search path, so linking against it needs no help from us.
    """
    try:
        dist = distribution(LLVM_CORE_DISTRIBUTION)
    except PackageNotFoundError:
        return []

    for file in dist.files or ():
        if file.name == LLVM_CORE_RUNTIME:
            # `locate_file` returns the RECORD's site-packages-relative path uncollapsed.
            # Keep the collapsing lexical: resolving symlinks here would rewrite a `lib64`
            # environment as `lib` and leave the path incomparable to `platlib` below.
            runtime_dir = dirname(normpath(str(dist.locate_file(file))))
            # Mirrors the RUNPATH karellen-llvm records in its own binaries: the plain
            # library directory first, then the per-target one.
            return [dirname(runtime_dir), runtime_dir]

    return []


def origin_relative(dirs, base_dir):
    """Rewrite absolute `dirs` as loader-origin-relative, as seen from `base_dir`."""
    return [join(ORIGIN, relpath(lib_dir, base_dir)) for lib_dir in dirs]


def ext_runtime_library_dirs(ext_name):
    """RUNPATH entries letting an installed extension find an in-environment LLVM.

    An extension is linked in the build tree but has to resolve its runtime from wherever
    it ends up installed, so the offset is measured from the directory the extension's
    package occupies under `platlib`. That offset is a property of the environment layout
    rather than of this particular environment, which is what makes it safe to bake into
    a wheel: any environment holding the same LLVM satisfies it.
    """
    install_dir = join(get_path("platlib"), *ext_name.split(".")[:-1])
    return origin_relative(llvm_core_lib_dirs(), install_dir)


def llvm_core_requirement():
    """Dependency pinning a package to the LLVM its extensions were linked against.

    `None` when there is nothing to pin to, under exactly the condition that suppresses
    the RUNPATH: the two go together, because a RUNPATH is only a path. Whatever LLVM
    happens to sit at the end of it in the installed environment supplies the C++
    runtime, and it has to be the one the extensions were compiled and linked against.

    The floor is the installed version verbatim. A `.postN` in it is not packaging
    bookkeeping -- it counts commits after the release tag, so two posts of one release
    are two different compilers, and an extension is linked against exactly one of them.
    The ceiling is the next major, which is why neither form of `~=` works here:
    `~=23.1.0` means `>=23.1.0, ==23.1.*` and excludes 23.2, while `~=23.1` means
    `==23.*` and drops the floor.
    """
    if not llvm_core_lib_dirs():
        return None

    version = distribution(LLVM_CORE_DISTRIBUTION).version
    return f"{LLVM_CORE_DISTRIBUTION}>={version},<{int(version.split('.')[0]) + 1}"


def pin_llvm_core(dist):
    """Record the pin on a distribution whose extensions this plugin is going to build.

    Scoped to distributions that have extensions and hand them to `ClangBuildExt`, since
    the `distutils.commands` entry point makes this plugin's `build_ext` the default for
    everything built in the environment, most of which has no extensions at all.
    """
    if not dist.ext_modules:
        return

    build_ext = dist.cmdclass.get("build_ext") or dist.get_command_class("build_ext")
    if not (isinstance(build_ext, type) and issubclass(build_ext, ClangBuildExt)):
        return

    requirement = llvm_core_requirement()
    if not requirement:
        return

    install_requires = list(dist.install_requires or [])
    if requirement in install_requires:
        return

    install_requires.append(requirement)
    # `_finalize_requires` copies the requirements onto the metadata, and PKG-INFO -- the
    # file that becomes the wheel's METADATA -- is written from that copy. Both are
    # assigned rather than one of them appended to: the two are the same list object only
    # by accident of which setuptools is in play.
    dist.install_requires = install_requires
    dist.metadata.install_requires = install_requires


def finalize_distribution_options(dist):
    """setuptools hook, invoked for every distribution built in this environment.

    The pin is deferred to after `parse_config_files` instead of being applied here.
    Hooks in this group run from `Distribution.__init__`, before `setup.cfg` and
    `pyproject.toml` have been read, and a `[project] dependencies` table replaces
    `install_requires` wholesale when it is read.

    Deferring also puts the pin on both routes into a wheel. `build_wheel` reaches it
    through `bdist_wheel`, but a front end that asks for metadata first -- as pip does,
    to resolve dependencies -- gets there through `dist_info`, which builds nothing and
    so never runs a command of ours.
    """
    parse_config_files = dist.parse_config_files

    def parse_config_files_and_pin(*args, **kwargs):
        parse_config_files(*args, **kwargs)
        pin_llvm_core(dist)

    dist.parse_config_files = parse_config_files_and_pin


class ClangCCompiler(UnixCCompiler):
    executables = {
        'preprocessor': ["clang", "-E"],
        'compiler': ["clang"],
        'compiler_so': ["clang"],
        'compiler_cxx': ["clang++"],
        'compiler_so_cxx': ["clang++"],
        'linker_so': ["clang", "-shared", "-fuse-ld=lld"],
        'linker_so_cxx': ["clang++", "-shared", "-fuse-ld=lld"],
        'linker_exe': ["clang", "-fuse-ld=lld"],
        'linker_exe_cxx': ["clang++", "-fuse-ld=lld"],
        'archiver': ["llvm-ar", "rcs"],
        'ranlib': None,
        'objcopy': ["llvm-objcopy"],
        'readelf': ["llvm-readelf"]
    }

    # Flags that have to outlive `configure_system`, which rebuilds every one of the
    # commands above from sysconfig strings (`LDSHARED='gcc -shared'`) and so keeps only
    # the program name. Scoped to the linkers on purpose: `archiver` carries an operation
    # code rather than a flag, and re-adding the declared one on top of sysconfig's would
    # leave `llvm-ar` reading the second as the archive name.
    required_flags = {
        'linker_so': ["-fuse-ld=lld"],
        'linker_so_cxx': ["-fuse-ld=lld"],
        'linker_exe': ["-fuse-ld=lld"],
        'linker_exe_cxx': ["-fuse-ld=lld"]
    }

    def __init__(self, verbose=0, dry_run=0, force=0, drakon=False, thin=False):
        self.drakon = drakon
        self.thin = thin
        if _has_dry_run:
            super().__init__(verbose, dry_run, force)
        else:
            super().__init__(verbose, force)
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

        missing = [flag for flag in self.required_flags.get(key, ()) if flag not in value]
        if missing:
            value = value + missing

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
        runtime_library_dirs = ext.runtime_library_dirs
        try:
            ext.sources = expand_sources(sources)
            ext.runtime_library_dirs = runtime_library_dirs + ext_runtime_library_dirs(ext.name)
            super().build_extension(ext)
        finally:
            ext.sources = sources
            ext.runtime_library_dirs = runtime_library_dirs

    def new_compiler(self, plat=None, compiler=None, verbose=0, dry_run=0, force=0):
        if compiler == "clang":
            if _has_dry_run:
                return ClangCCompiler(None, dry_run, force, drakon=self.drakon, thin=self.thin)
            else:
                return ClangCCompiler(None, force, drakon=self.drakon, thin=self.thin)
        if _has_dry_run:
            return self._old_new_compiler(plat, compiler, verbose, dry_run, force)
        else:
            return self._old_new_compiler(plat, compiler, verbose, force)

    @contextmanager
    def customized_compiler(self):
        self._old_new_compiler = _old_new_compiler = ccompiler.new_compiler
        ccompiler.new_compiler = self.new_compiler
        from setuptools.command import build_ext
        _old_build_ext_new_compiler = build_ext.new_compiler
        build_ext.new_compiler = self.new_compiler

        from distutils.command import build_ext as _d_build_ext
        _old_d_build_ext_new_compiler = getattr(_d_build_ext, 'new_compiler', None)
        if _old_d_build_ext_new_compiler is not None:
            _d_build_ext.new_compiler = self.new_compiler

        try:
            yield
        finally:
            ccompiler.new_compiler = _old_new_compiler
            build_ext.new_compiler = _old_build_ext_new_compiler
            if _old_d_build_ext_new_compiler is not None:
                _d_build_ext.new_compiler = _old_d_build_ext_new_compiler


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
            if _has_dry_run:
                return ClangCCompiler(None, dry_run, force, drakon=self.drakon, thin=self.thin)
            else:
                return ClangCCompiler(None, force, drakon=self.drakon, thin=self.thin)
        if _has_dry_run:
            return self._old_new_compiler(plat, compiler, verbose, dry_run, force)
        else:
            return self._old_new_compiler(plat, compiler, verbose, force)

    @contextmanager
    def customized_compiler(self):
        from distutils import ccompiler
        self._old_new_compiler = _old_new_compiler = ccompiler.new_compiler
        ccompiler.new_compiler = self.new_compiler

        from distutils.command import build_clib as _d_build_clib
        _old_d_build_clib_new_compiler = getattr(_d_build_clib, 'new_compiler', None)
        if _old_d_build_clib_new_compiler is not None:
            _d_build_clib.new_compiler = self.new_compiler

        try:
            yield
        finally:
            ccompiler.new_compiler = _old_new_compiler
            if _old_d_build_clib_new_compiler is not None:
                _d_build_clib.new_compiler = _old_d_build_clib_new_compiler

    def run(self):
        with self.customized_compiler():
            super().run()

    def build_libraries(self, libraries):
        new_libraries = []
        for lib_name, build_info in libraries:
            sources = build_info.get("sources")
            if sources:
                # Only "sources" is glob-expanded: everything else in build_info
                # (macros, include_dirs, cflags, obj_deps) must be passed through
                build_info = dict(build_info, sources=expand_sources(sources))
            new_libraries.append((lib_name, build_info))

        super().build_libraries(new_libraries)

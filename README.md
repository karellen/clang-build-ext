# Clang Build Extension

[![Gitter](https://img.shields.io/gitter/room/karellen/lobby?logo=gitter)](https://gitter.im/karellen/Lobby)
[![Build Status](https://img.shields.io/github/actions/workflow/status/karellen/clang-build-ext/build.yml?branch=master)](https://github.com/karellen/clang-build-ext/actions/workflows/build.yml)
[![Coverage Status](https://img.shields.io/coveralls/github/karellen/clang-build-ext/master?logo=coveralls)](https://coveralls.io/r/karellen/clang-build-ext?branch=master)

[![clang-build-ext Version](https://img.shields.io/pypi/v/clang-build-ext?logo=pypi)](https://pypi.org/project/clang-build-ext/)
[![clang-build-ext Python Versions](https://img.shields.io/pypi/pyversions/clang-build-ext?logo=pypi)](https://pypi.org/project/clang-build-ext/)
[![clang-build-ext Downloads Per Day](https://img.shields.io/pypi/dd/clang-build-ext?logo=pypi)](https://pypi.org/project/clang-build-ext/)
[![clang-build-ext Downloads Per Week](https://img.shields.io/pypi/dw/clang-build-ext?logo=pypi)](https://pypi.org/project/clang-build-ext/)
[![clang-build-ext Downloads Per Month](https://img.shields.io/pypi/dm/clang-build-ext?logo=pypi)](https://pypi.org/project/clang-build-ext/)

## Overview

`clang-build-ext` is a setuptools plugin that builds Python C/C++ extensions using the
[LLVM/Clang](https://clang.llvm.org/) compiler toolchain instead of the system default compiler.
Either a system-installed LLVM/Clang or the
[`karellen-llvm-clang`](https://pypi.org/project/karellen-llvm-clang/) package can be used.

The plugin provides drop-in replacements for setuptools' `build_ext` and `build_clib` commands,
along with features such as glob pattern expansion in source lists, Drakon IR bytecode embedding,
and thin static library support.

## Basic Usage

Add `clang-build-ext` to your build dependencies in `pyproject.toml`:

```toml
[build-system]
requires = ["setuptools", "clang-build-ext"]
build-backend = "setuptools.build_meta"
```

Register the custom build commands in `setup.py`:

```python
from setuptools import setup, Extension
from karellen.clang_build_ext import ClangBuildExt, ClangBuildClib

setup(
    ...,
    ext_modules=[Extension("myext", ["src/*.c"])],
    cmdclass={
        "build_ext": ClangBuildExt,
        "build_clib": ClangBuildClib,
    },
)
```

Then build with:

```shell
python -m build
```

## LLVM Toolchain

The compiler uses the full LLVM toolchain:

| Tool         | Command                          |
|--------------|----------------------------------|
| C compiler   | `clang`                          |
| C++ compiler | `clang++`                        |
| Linker       | `clang -fuse-ld=lld` / `clang++ -fuse-ld=lld` |
| Archiver     | `llvm-ar`                        |
| Objcopy      | `llvm-objcopy`                   |
| Readelf      | `llvm-readelf`                   |

`lld` is required, not merely preferred. Python's own build configuration supplies the
linker command (`LDSHARED`, typically `gcc -shared`), and the plugin substitutes the
toolchain's driver into it while re-applying `-fuse-ld=lld`, so every link goes through
lld regardless of what the interpreter was built with — the same assumption already made
for `llvm-ar`.

## Features

### Glob Pattern Expansion

Source file lists in both extensions and libraries support shell glob patterns. Patterns are
expanded at build time, so you don't need to enumerate every source file in `setup.py`.

`*` matches within a single directory, while `**` recurses to any depth. Given this tree:

```
src/module/
├── module.c
├── util/
│   └── util.c
└── codec/
    └── formats/
        └── png.c
```

| Pattern | Matches |
|---------|---------|
| `src/module/*.c` | `module.c` |
| `src/module/*/*.c` | `util/util.c` |
| `src/module/**/*.c` | `module.c`, `util/util.c`, `codec/formats/png.c` |

Use `*` when a flat directory is exactly what you want — it will not silently pick up
sources added in a subdirectory later:

```python
setup(
    ...,
    ext_modules=[
        Extension("myext", ["src/module/*.c"]),
    ],
    libraries=[
        ("mylib", {"sources": ["src/lib/*.c"]}),
    ],
    cmdclass={
        "build_ext": ClangBuildExt,
        "build_clib": ClangBuildClib,
    },
)
```

Use `**` to pick up an entire source tree regardless of how deeply it is nested:

```python
setup(
    ...,
    ext_modules=[
        Extension("myext", ["src/module/**/*.c"]),
    ],
    libraries=[
        ("mylib", {"sources": ["src/lib/**/*.c"]}),
    ],
    cmdclass={
        "build_ext": ClangBuildExt,
        "build_clib": ClangBuildClib,
    },
)
```

`**/*.c` already covers the top level, so pairing it with `*.c` is unnecessary. Should you
list overlapping patterns anyway, each source is compiled once: duplicates are removed,
keeping the order in which they were first matched.

Only the `sources` entry is glob-expanded. Every other `build_info` key of a `build_clib`
library (`macros`, `include_dirs`, `cflags`, `obj_deps`) is passed through to setuptools
unchanged.

### C++ Extensions

Sources that setuptools detects as C++ (`.cc`, `.cpp`, `.cxx`) are compiled with the
`clang++` driver, and any extension containing one is linked with it too, so no extra
configuration is required to mix C and C++ in one project:

```python
setup(
    ...,
    ext_modules=[
        Extension("myext", ["src/module/*.cpp"],
                  include_dirs=["include"],
                  extra_compile_args=["-std=c++20"]),
    ],
    libraries=[
        ("mylib", {"sources": ["src/lib/*.cpp"],
                   "include_dirs": ["include"],
                   "macros": [("MYLIB_BUILD", "1")],
                   "cflags": ["-std=c++20"]}),
    ],
    cmdclass={
        "build_ext": ClangBuildExt,
        "build_clib": ClangBuildClib,
    },
)
```

### Toolchain Location and `RUNPATH`

The two supported toolchains resolve their runtime libraries differently, and the build
adapts to whichever one is in use.

A **system LLVM/Clang** keeps its libraries where the dynamic loader already looks, so
nothing extra is required and nothing extra is emitted. Extensions link directly against
the host's C++ runtime, which on most distributions is `libstdc++` — distribution builds
of Clang are generally configured to prefer it.

An **in-environment LLVM/Clang**, installed through `karellen-llvm-clang`, defaults to
`libc++` and ships its libraries inside the Python environment — `libc++.so.1` and
`libc++abi.so.1` and `libunwind.so.1` land in
`<prefix>/lib/<target-triple>/`, which is on no default search path. Extensions built
against it would record a bare `DT_NEEDED` and fail at import with:

```
ImportError: libc++.so.1: cannot open shared object file: No such file or directory
```

To prevent that, `clang-build-ext` detects `karellen-llvm-core` in the current environment
and records an origin-relative `RUNPATH` on every extension it links:

```
${ORIGIN}/../../../lib:${ORIGIN}/../../../lib/x86_64-unknown-linux-gnu
```

This mirrors the `RUNPATH` `karellen-llvm` records in its own binaries. Because the offset
is measured from the extension's install directory to the environment prefix, it holds in
any environment with the same layout, so a wheel built this way imports without
`LD_LIBRARY_PATH` wherever `karellen-llvm-clang` is installed alongside it.

Detection is on the distribution, not on `PATH`: if `karellen-llvm-core` is absent from the
environment, the build is treated as a system-toolchain build and the link line is left
alone. Any `runtime_library_dirs` you set on an `Extension` are preserved and come first.

### Pinning the LLVM Runtime

A `RUNPATH` is only a path. Whichever `karellen-llvm-core` is installed where it points
supplies the C++ runtime at import time, and that has to be the one the extensions were
compiled and linked against — an upgrade or downgrade of LLVM in the environment otherwise
repoints a built extension at a different runtime with nothing in the metadata to object.

So alongside the `RUNPATH`, and under exactly the same condition, the package being built
gains a dependency on the LLVM it was built against:

```
Requires-Dist: karellen-llvm-core>=23.1.1.post6,<24
```

The floor is the installed version as it stands. A `.postN` in it is not packaging
bookkeeping — it counts commits after the release tag, so two posts of one release are two
different compilers. The ceiling is the next major version. Nothing is added to a package
built against a system toolchain.

The dependency is contributed through a `setuptools.finalize_distribution_options` entry
point, so it appears both in the wheel and in the metadata a front end prepares ahead of
building, which is what pip resolves against. Dependencies you declare yourself, in
`setup.py` or in `[project]`, are untouched.

### Drakon Enhancements

Drakon mode embeds LLVM intermediate representation (IR) bytecode into compiled binaries as
custom ELF sections. This enables post-compilation IR analysis and transformation of the
final binary.

When enabled, the build:

1. Compiles with `--save-temps=obj -fno-discard-value-names` to produce `.bc` (bitcode) files
   alongside object files
2. Includes `.bc` files in static libraries created via `build_clib`
3. After linking, extracts `.bc` files from all linked objects and static libraries and embeds
   them into the output binary as `.drakon.<name>` ELF sections (marked `noload,readonly`)

Enable via environment variable or `setup.cfg`:

```shell
DRAKON=1 python -m build
```

```ini
# setup.cfg
[build_ext]
drakon = 1
```

### Thin Static Libraries

Thin static libraries store references to object files rather than copies, reducing build
artifact size during development.

Enable via environment variable or `setup.cfg`:

```shell
THIN=1 python -m build
```

```ini
# setup.cfg
[build_ext]
thin = 1
```

Both options can be combined:

```shell
DRAKON=1 THIN=1 python -m build
```

```ini
# setup.cfg
[build_ext]
drakon = 1
thin = 1
```

The `build_clib` command inherits `drakon` and `thin` settings from `build_ext` automatically.

## Setuptools Compatibility

`clang-build-ext` supports setuptools 68 and newer, and is tested against the versions on
either side of each API change that affects it:

| setuptools | Change |
|------------|--------|
| 70.1       | `build_meta.get_requires_for_build_wheel` stops requiring `wheel`; earlier versions need it installed for a `--no-isolation` build |
| 72.2       | `UnixCCompiler` gains the separate C++ executables (`compiler_cxx`, `compiler_so_cxx`, `linker_so_cxx`, `linker_exe_cxx`) |
| 75.9       | `new_compiler` moves from `distutils.ccompiler` to `distutils.compilers.C.base` |
| 81.0       | `new_compiler` drops the `dry_run` parameter |

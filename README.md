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

Add the following to your `setup.py`:

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

## LLVM Toolchain

The compiler uses the full LLVM toolchain:

| Tool         | Command                          |
|--------------|----------------------------------|
| C compiler   | `clang`                          |
| C++ compiler | `clang-cpp`                      |
| Linker       | `clang -fuse-ld=lld` / `clang-cpp -fuse-ld=lld` |
| Archiver     | `llvm-ar`                        |
| Objcopy      | `llvm-objcopy`                   |
| Readelf      | `llvm-readelf`                   |

## Features

### Glob Pattern Expansion

Source file lists in both extensions and libraries support shell glob patterns. Patterns are
expanded at build time, so you don't need to enumerate every source file in `setup.py`:

```python
setup(
    ...,
    ext_modules=[
        Extension("myext", ["src/module/*.c", "src/module/**/*.c"]),
    ],
    libraries=[
        ("mylib", {"sources": ["src/lib/*.c", "src/lib/**/*.c"]}),
    ],
    cmdclass={
        "build_ext": ClangBuildExt,
        "build_clib": ClangBuildClib,
    },
)
```

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

Enable via command line or environment variable:

```shell
# Command line
python setup.py build_ext --drakon
python setup.py build_ext -d

# Environment variable
DRAKON=1 python setup.py build_ext
```

### Thin Static Libraries

Thin static libraries store references to object files rather than copies, reducing build
artifact size during development.

Enable via command line or environment variable:

```shell
# Command line
python setup.py build_ext --thin
python setup.py build_ext -T

# Environment variable
THIN=1 python setup.py build_ext
```

Both options can be combined:

```shell
python setup.py build_ext --drakon --thin
```

The `build_clib` command inherits `drakon` and `thin` settings from `build_ext` automatically.

## Setuptools Compatibility

`clang-build-ext` maintains compatibility across setuptools versions, including the API changes
in setuptools 75+ (new C++ compiler executables) and 82+ (removal of the `dry_run` parameter).

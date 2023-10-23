# Clang Build Extension

[![Gitter](https://img.shields.io/gitter/room/karellen/Lobby?logo=gitter)](https://app.gitter.im/#/room/#karellen_Lobby:gitter.im)
[![Build Status](https://img.shields.io/github/actions/workflow/status/karellen/clang-build-ext/build.yml?branch=master)](https://github.com/karellen/clang-build-ext/actions/workflows/build.yml)
[![Coverage Status](https://img.shields.io/coveralls/github/karellen/clang-build-ext/master?logo=coveralls)](https://coveralls.io/r/karellen/clang-build-ext?branch=master)

[![Clang Build Ext Version](https://img.shields.io/pypi/v/clang-build-ext?logo=pypi)](https://pypi.org/project/clang-build-ext/)
[![Clang Build Ext Python Versions](https://img.shields.io/pypi/pyversions/clang-build-ext?logo=pypi)](https://pypi.org/project/clang-build-ext/)

[![Clang Build Ext Downloads Per Day](https://img.shields.io/pypi/dd/clang-build-ext?logo=pypi)](https://pypistats.org/packages/clang-build-ext)
[![Clang Build Ext Downloads Per Week](https://img.shields.io/pypi/dw/clang-build-ext?logo=pypi)](https://pypistats.org/packages/clang-build-ext)
[![Clang Build Ext Downloads Per Month](https://img.shields.io/pypi/dm/clang-build-ext?logo=pypi)](https://pypistats.org/packages/clang-build-ext)

The `clang-build-ext` extension builds Python extensions using a Clang compiler stack.
Either system LLVM/Clang or `karellen-llvm-clang` package can be used.

Beyond compiler the additional functionality is currently undocumented.

## How to Use

Add the following to the `setup.py` script:

```python 
from setuptools import setup
from karellen.clang_build_ext import ClangBuildExt, ClangBuildClib

...

setup(
..., 
cmdclass={"build_ext": ClangBuildExt,
          "build_clib": ClangBuildClib},)
)

```
from setuptools import setup, Extension
from setuptools.extension import Library

from karellen.clang_build_ext import ClangBuildExt, ClangBuildClib

CXX_STD = ["-std=c++20"]

setup(name="test_cxx",
      version="1.0.0",
      description="Python C++ test module",
      author="Karellen, Inc.",
      author_email="supervisor@karellen.co",
      # `**` alone, with no `*` companion: it already covers the top level.
      # extension_1 uses the overlapping pair instead, to exercise deduplication.
      ext_modules=[Library("cxxshlib",
                           ["src/cxxshlib/**/*.cpp"],
                           include_dirs=["include"],
                           extra_compile_args=CXX_STD),
                   Extension("test_cxx",
                             ["src/cxxmodule/**/*.cpp"],
                             include_dirs=["include"],
                             extra_compile_args=CXX_STD),
                   ],
      libraries=[("cxxalib", {"sources": ["src/cxxalib/**/*.cpp"],
                              "include_dirs": ["include"],
                              "macros": [("CXXALIB_FLAVOR", "1")],
                              "cflags": CXX_STD})
                 ],
      cmdclass={"build_ext": ClangBuildExt,
                "build_clib": ClangBuildClib},
      )

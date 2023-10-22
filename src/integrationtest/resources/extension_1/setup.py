from setuptools import setup, Extension
from setuptools.extension import Library

from karellen.clang_build_ext import ClangBuildExt, ClangBuildClib

setup(name="test",
      version="1.0.0",
      description="Python test module",
      author="Karellen, Inc.",
      author_email="supervisor@karellen.co",
      ext_modules=[Library("shlib",
                           ["src/shlib/*.c", "src/shlib/**/*.c"]),
                   Extension("test",
                             ["src/module/*.c", "src/module/**/*.c"],
                             ),
                   ],
      libraries=[("alib", {"sources": ["src/alib/*.c", "src/alib/**/*.c"]})
                 ],
      cmdclass={"build_ext": ClangBuildExt,
                "build_clib": ClangBuildClib},
      )

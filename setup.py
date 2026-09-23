"""Build hooks: compile the .wasm modules as part of every build (see build_wasm.py).

All metadata lives in pyproject.toml; this file only wires build_wasm.build() into setuptools:

- build_py compiles the modules into src/tiny_bclibc/ before packaging, so wheels, sdist->wheel
  builds and editable installs (`uv sync`) all get them without a separate step;
- sdist records the bclibc submodule's version next to the sources, because the unpacked sdist
  has no git metadata to `git describe`.
"""

import os
import sys

from setuptools import setup
from setuptools.command.build_py import build_py
from setuptools.command.sdist import sdist

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_wasm


class BuildPyWithWasm(build_py):
    def run(self):
        build_wasm.build()
        super().run()


class SdistWithBclibcVersion(sdist):
    def make_release_tree(self, base_dir, files):
        super().make_release_tree(base_dir, files)
        path = os.path.join(base_dir, os.path.relpath(build_wasm.VERSION_FILE, build_wasm.ROOT))
        if os.path.exists(path):  # a hard link to the source tree's copy; don't write through it
            os.remove(path)
        with open(path, "w") as f:
            f.write(build_wasm.bclibc_version() + "\n")


setup(cmdclass={"build_py": BuildPyWithWasm, "sdist": SdistWithBclibcVersion})

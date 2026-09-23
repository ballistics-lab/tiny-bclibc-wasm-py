"""Run micropython-bclibc's natmod test suite (tests/test_bclibc.py) unmodified against this package.

That suite is the natmod's own acceptance test (it runs on real boards and under the rp2040py
emulator); passing it here is the check that `import tiny_bclibc` behaves the same on
CPython/PyPy/Pythonista as on MicroPython.

Usage (from the repo root; `uv sync` has already built the .wasm modules):
    uv run python tests/run_natmod_suite.py [path/to/test_bclibc.py]

The suite path defaults to $BCLIBC_NATMOD_SUITE, else ../micropython-bclibc/tests/test_bclibc.py
(a sibling checkout). Host/precision come from TINY_BCLIBC_HOST / TINY_BCLIBC_PRECISION as usual.
Exit status is the suite's own: the number of failed checks.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import tiny_bclibc

suite = (
    sys.argv[1]
    if len(sys.argv) > 1
    else os.environ.get("BCLIBC_NATMOD_SUITE")
    or os.path.join(os.path.dirname(ROOT), "micropython-bclibc", "tests", "test_bclibc.py")
)
if not os.path.isfile(suite):
    sys.exit(f"natmod suite not found at {suite} (pass its path, or set BCLIBC_NATMOD_SUITE)")

print(f"host: {tiny_bclibc.host()}  module: {tiny_bclibc.version()}")
sys.modules["tiny_bclibc"] = tiny_bclibc
with open(suite) as f:
    src = f.read()
exec(compile(src, suite, "exec"), {"__file__": suite, "__name__": "__main__"})  # noqa: S102 -- runs the suite as a script

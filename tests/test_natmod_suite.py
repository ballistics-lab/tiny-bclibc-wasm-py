"""pytest wrapper around run_natmod_suite.py: micropython-bclibc's tests/test_bclibc.py, both precisions.

Skipped when that suite isn't available (no sibling ../micropython-bclibc checkout and no
$BCLIBC_NATMOD_SUITE). Each precision runs in its own interpreter, because the package picks its
module once per process.
"""

import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
SUITE = os.environ.get("BCLIBC_NATMOD_SUITE") or os.path.join(
    os.path.dirname(os.path.dirname(HERE)), "micropython-bclibc", "tests", "test_bclibc.py"
)


@pytest.mark.skipif(not os.path.isfile(SUITE), reason=f"natmod suite not found at {SUITE}")
@pytest.mark.parametrize("precision", ["double", "single"])
def test_natmod_suite(precision):
    env = dict(os.environ, TINY_BCLIBC_PRECISION=precision)
    proc = subprocess.run(
        [sys.executable, os.path.join(HERE, "run_natmod_suite.py"), SUITE],
        env=env,
        check=False,  # the exit status is the failure count; asserted on below with the output
        capture_output=True,
        text=True,
        timeout=300,
    )
    failures = [line for line in proc.stdout.splitlines() if line.strip().startswith("FAIL")]
    assert proc.returncode == 0 and "=== done ===" in proc.stdout, (
        f"exit {proc.returncode}, failures: {failures}\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}"
    )

"""`--wasm-runtime`: run the suite on one specific WebAssembly host (like py-ballisticcalc's `--engine`).

    uv run pytest                      # automatic pick (see tiny_bclibc._runner.AUTO_ORDER)
    uv run pytest --wasm-runtime wasmtime
    uv run pytest --wasm-runtime node
    uv run pytest --wasm-runtime gi-jsc     # WebKitGTK JavaScriptCore (needs PyGObject; see README)

The choice is applied before any test runs, both in-process (tiny_bclibc.set_host) and for
subprocesses (TINY_BCLIBC_HOST, which the natmod suite inherits). A runtime that can't start stops the
whole run with an error -- it is never silently skipped, so a CI step named after a runtime really
ran on it.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import tiny_bclibc
from tiny_bclibc import _runner


def pytest_addoption(parser):
    parser.addoption(
        "--wasm-runtime",
        action="store",
        default=None,
        choices=sorted(_runner.HOSTS),
        help="WebAssembly host to run tiny_bclibc on (default: automatic pick)",
    )


def pytest_configure(config):
    runtime = config.getoption("--wasm-runtime")
    if runtime:
        os.environ["TINY_BCLIBC_HOST"] = runtime
        tiny_bclibc.set_host(runtime)
    try:
        # probe: start the host and load the module once, up front
        tiny_bclibc.version()
    except Exception as exc:  # noqa: BLE001 -- any failure here means the run can't be meaningful
        pytest.exit(f"Cannot start tests: WebAssembly runtime {runtime or '(auto)'} failed: {exc}", returncode=1)


def pytest_report_header(config):
    return f"tiny_bclibc: {tiny_bclibc.version()} on runtime {tiny_bclibc.host()}"

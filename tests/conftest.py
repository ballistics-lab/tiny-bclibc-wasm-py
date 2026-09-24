"""`--wasm-backend`: run the suite on one specific WebAssembly backend (like py-ballisticcalc's `--engine`).

    uv run pytest                      # automatic pick (see tiny_bclibc._runner.AUTO_ORDER)
    uv run pytest --wasm-backend wasmtime
    uv run pytest --wasm-backend node
    uv run pytest --wasm-backend gi-jsc     # WebKitGTK JavaScriptCore (needs PyGObject; see README)

The choice is applied before any test runs, both in-process (tiny_bclibc.set_host) and for
subprocesses (TINY_BCLIBC_HOST, which the natmod suite inherits). A backend that can't start stops the
whole run with an error -- it is never silently skipped, so a CI step named after a backend really
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
        "--wasm-backend",
        action="store",
        default=None,
        choices=sorted(_runner.HOSTS),
        help="WebAssembly host to run tiny_bclibc on (default: automatic pick)",
    )


def pytest_configure(config):
    backend = config.getoption("--wasm-backend")
    if backend:
        os.environ["TINY_BCLIBC_HOST"] = backend
        tiny_bclibc.set_host(backend)
    try:
        # probe: start the host and load the module once, up front
        tiny_bclibc.version()
    except Exception as exc:  # noqa: BLE001 -- any failure here means the run can't be meaningful
        pytest.exit(f"Cannot start tests: WebAssembly backend {backend or '(auto)'} failed: {exc}", returncode=1)


def pytest_report_header(config):
    return f"tiny_bclibc: {tiny_bclibc.version()} on backend {tiny_bclibc.host()}"

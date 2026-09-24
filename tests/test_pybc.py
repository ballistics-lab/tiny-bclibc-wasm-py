"""tiny_bclibc.pybc is registered with py-ballisticcalc under both entry-point layouts.

The engines' behaviour is tested by py-ballisticcalc's own suite (`uv run pytest py-ballisticcalc/tests
--engine=tiny_bclibc_wasm+tsitouras`); this only checks that each name resolves to the right class.
"""

import warnings

import pytest

pytest.importorskip("py_ballisticcalc")

from py_ballisticcalc.interface import _EngineLoader

from tiny_bclibc import pybc


@pytest.mark.parametrize(
    ("name", "engine"),
    [
        ("tiny_bclibc_wasm+tsitouras", pybc.TinyBclibcWasmTsitourasEngineDP),
        ("tiny_bclibc_wasm.tsitouras", pybc.TinyBclibcWasmTsitourasEngineDP),
        ("tiny_bclibc_wasm+tsitouras-sp", pybc.TinyBclibcWasmTsitourasEngineSP),
        ("tiny_bclibc.pybc:TinyBclibcWasmTsitourasEngineDP", pybc.TinyBclibcWasmTsitourasEngineDP),
        # legacy flat group: the only one py-ballisticcalc 3.0.0b1/b2 read
        ("tiny_bclibc_wasm_engine", pybc.TinyBclibcWasmTsitourasEngineDP),
        ("tiny_bclibc_wasm_sp_engine", pybc.TinyBclibcWasmTsitourasEngineSP),
    ],
)
def test_engine_names(name: str, engine: type) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)  # legacy names
        assert _EngineLoader.load(name) is engine

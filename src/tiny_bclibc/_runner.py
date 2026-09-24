"""WebAssembly hosts for tiny_bclibc's .wasm module.

The module (bclibc's tiny_bclibc/wasm/tiny_bclibc_wasm.c) imports nothing and takes/returns plain
numbers plus two `double` buffers in its linear memory. Every host below exposes the same one call:

    runner.call(export_name, input_doubles, *numeric_args) -> list of output doubles

which writes `input_doubles` into the module's input buffer, calls `export_name(*numeric_args)`,
and returns the output buffer (raising TbwError on a non-zero status).

Hosts (see `default_runner` for how one is picked):
    JSContextRunner         JavaScriptCore via Pythonista's objc_util (iOS). What this exists for.
    GIJavaScriptCoreRunner  WebKitGTK's JavaScriptCore via PyGObject (Linux): the same engine,
                            driven the same way -- the desktop rehearsal of the Pythonista setup.
    NodeRunner              a long-lived `node` subprocess.
    WasmtimeRunner          the `wasmtime` package (pip/uv), no JavaScript at all.
    Wasm3Runner             pywasm3 (the wasm3 interpreter), no JavaScript either; CPython 3.11+,
                            installed from git (its PyPI release is years old).

All five are `wasmhost`'s backends (the JavaScript WebAssembly API over a bare JavaScript engine, or a native
runtime), so what runs under Node or WebKitGTK is exactly what runs in Pythonista. A call is one batch:
allocate the input, write it, call the export, read the output -- one trip to a JavaScript engine.

Plain Python 3.10+ (Pythonista's interpreter), no third-party imports except `wasmhost` (which imports
`wasmtime` / `wasm3` only when their backend is asked for); runs on PyPy too.
"""

from __future__ import annotations

import os
import struct
from collections.abc import Sequence
from typing import Any, Final

__all__ = (
    "AUTO_ORDER",
    "HOSTS",
    "GIJavaScriptCoreRunner",
    "JSContextRunner",
    "NodeRunner",
    "TbwError",
    "Wasm3Runner",
    "WasmRunner",
    "WasmtimeRunner",
    "default_runner",
)


class TbwError(RuntimeError):
    """A tbw_* export returned a non-zero TINY_BCLIBC_Status."""

    def __init__(self, status: int, message: str) -> None:
        RuntimeError.__init__(self, f"status {status}: {message}")
        self.status = status
        self.message = message


class WasmRunner:
    """One loaded module. Subclasses implement `load_bytes` and `call`."""

    name: str = "?"
    sizeof_real: int = 0  # sizeof(real_t) of the loaded module: 8 (double) or 4 (single)
    version: str = ""  # tiny_bclibc version string of the loaded module

    def load_file(self, path: str) -> None:
        with open(path, "rb") as f:
            self.load_bytes(f.read())

    def load_bytes(self, wasm: bytes) -> None:
        raise NotImplementedError

    def call(self, export: str, inputs: Sequence[float], *args: float) -> list[float]:
        raise NotImplementedError

    def call_scalar(self, export: str, *args: int) -> float:
        """Call an export that takes integers and returns one number (no buffers), e.g. tbw_bench_*."""
        raise NotImplementedError


# ── The hosts, through wasmhost ───────────────────────────────────────────────


class _WasmhostRunner(WasmRunner):
    """A backend of `wasmhost` (a JavaScript engine, or wasmtime / wasm3): exports called with numbers, the
    input and output buffers moved through the module's memory. What runs under Node or WebKitGTK is exactly
    what runs in Pythonista."""

    def __init__(self) -> None:
        import wasmhost  # ImportError here means "host not available"

        self._wh: Any = wasmhost  # the module, kept for load_bytes
        self._host = wasmhost.BACKENDS[self.name]()  # starting it is the availability probe
        self._inst: Any = None
        self._ex: Any = None
        self._mem: Any = None

    def close(self) -> None:
        self._host.close()

    def load_bytes(self, wasm: bytes) -> None:
        module = self._wh.Module(bytes(wasm), backend=self._host)
        self._inst = self._wh.Instance(module)
        exports: Any = self._inst.exports  # any export, like the JS object
        self._ex, self._mem = exports, exports.memory
        if "_initialize" in exports:
            exports["_initialize"]()
        self.sizeof_real = int(self._invoke("tbw_sizeof_real"))
        self.version = self._cstr(int(self._invoke("tbw_version")))

    def _invoke(self, name: str, *args: float) -> Any:
        if self._ex is None:
            raise RuntimeError("no module loaded")
        fn = self._ex[name]
        # An integer parameter takes an int (a float there would be a TypeError, as it is for wasmtime).
        return fn(*(int(a) if kind in ("i32", "i64") else a for a, kind in zip(args, fn.params, strict=False)))

    def _cstr(self, ptr: int) -> str:
        out = bytearray()
        while True:
            chunk = self._mem.read(ptr, min(64, len(self._mem) - ptr))
            end = chunk.find(b"\0")
            if end >= 0 or not chunk:
                out += chunk[:end] if end >= 0 else chunk
                return out.decode("latin-1")
            out += chunk
            ptr += len(chunk)

    def call_scalar(self, export: str, *args: int) -> float:
        return float(self._invoke(export, *args))

    def call(self, export: str, inputs: Sequence[float], *args: float) -> list[float]:
        # One trip to the engine: allocate, write, call, read. A failed allocation or a non-zero status
        # ends the batch there, so nothing is written to address 0 and no garbage length is read.
        ex = self._ex
        n = len(inputs)
        b = self._inst.batch()
        ptr = b.call(ex["tbw_input"], n)
        b.stop_if_zero(ptr)
        b.write(self._mem, ptr, struct.pack(f"<{n}d", *inputs))
        fn = ex[export]
        rc = b.call(fn, *(int(a) if kind in ("i32", "i64") else a for a, kind in zip(args, fn.params, strict=False)))
        b.stop_if_nonzero(rc)
        out_n = b.call(ex["tbw_output_len"])
        raw = b.read(self._mem, b.call(ex["tbw_output"]), out_n * 8)
        b.run()
        if not ptr.done or ptr.value == 0:
            raise TbwError(1, "tbw_input: out of memory")
        if rc.value != 0:
            raise TbwError(rc.value, self._cstr(int(self._invoke("tbw_last_error"))))
        return list(struct.unpack(f"<{out_n.value}d", raw.value))


class JSContextRunner(_WasmhostRunner):
    """JavaScriptCore via Pythonista's objc_util."""

    name = "jscontext"


class GIJavaScriptCoreRunner(_WasmhostRunner):
    """WebKitGTK's JavaScriptCore via PyGObject: `apt install gir1.2-javascriptcoregtk-4.1 python3-gi`."""

    name = "gi-jsc"


class WasmtimeRunner(_WasmhostRunner):
    """The `wasmtime` package (`pip install wasmtime`), a JIT."""

    name = "wasmtime"


class Wasm3Runner(_WasmhostRunner):
    """pywasm3 (the wasm3 interpreter); CPython 3.11+, installed from git (its PyPI release is years old)."""

    name = "wasm3"


class NodeRunner(_WasmhostRunner):
    """A long-lived `node` subprocess."""

    name = "node"


# ── Host selection ────────────────────────────────────────────────────────────

HOSTS: Final[dict[str, type[WasmRunner]]] = {
    "jscontext": JSContextRunner,
    "wasmtime": WasmtimeRunner,
    "wasm3": Wasm3Runner,
    "gi-jsc": GIJavaScriptCoreRunner,
    "node": NodeRunner,
}

# Tried in this order when nothing is chosen explicitly. Each constructor is its own availability
# probe: it raises when its runtime isn't there (ImportError for objc_util/wasmtime/gi, a missing
# `node` binary, a JS engine without WebAssembly), so "available" means "could actually start".
AUTO_ORDER: Final[tuple[str, ...]] = ("jscontext", "wasmtime", "wasm3", "gi-jsc", "node")


def default_runner() -> WasmRunner:
    """Start a host: $TINY_BCLIBC_HOST if set, else the first of AUTO_ORDER that starts.

    AUTO_ORDER puts Pythonista's JSContext first (only exists there), then the in-process runtimes
    when installed -- wasmtime (JIT), wasm3 (interpreter) -- then WebKitGTK JavaScriptCore (Linux
    with PyGObject), then Node.
    """
    choice = os.environ.get("TINY_BCLIBC_HOST", "").lower()
    if choice:
        if choice not in HOSTS:
            raise ValueError("TINY_BCLIBC_HOST={!r}: expected one of {}".format(choice, ", ".join(HOSTS)))
        return HOSTS[choice]()
    errors: list[str] = []
    for name in AUTO_ORDER:
        try:
            return HOSTS[name]()
        except Exception as exc:  # noqa: BLE001 -- not available here; try the next one
            errors.append(f"{name}: {exc}")
    raise RuntimeError(
        "No WebAssembly host available (tried {}). Run in Pythonista, install wasmtime "
        "(`uv add wasmtime`), or put node on PATH.".format("; ".join(errors))
    )

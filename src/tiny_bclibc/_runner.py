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
    NodeRunner              a long-lived `node` subprocess (JSON lines).
    WasmtimeRunner          the `wasmtime` package (pip/uv), no JavaScript at all.

The three JS hosts share one glue snippet (`_GLUE`), so what runs under Node or WebKitGTK is exactly
what runs in Pythonista. A call is one script evaluation: the input goes in as an array literal and
the output comes back as one comma-joined string; JS Number.toString is the shortest round-tripping
form, so `float()` recovers every double exactly (NaN/Infinity included).

Plain Python 3.10+ (Pythonista's interpreter), no third-party imports except the optional
`wasmtime` inside WasmtimeRunner; runs on PyPy too.
"""

from __future__ import annotations

import atexit
import importlib
import json
import math
import os
import shutil
import struct
import subprocess
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    import wasmtime
    from wasmtime._instance import InstanceExports  # not re-exported at the package top level

__all__ = (
    "AUTO_ORDER",
    "HOSTS",
    "GIJavaScriptCoreRunner",
    "JSContextRunner",
    "NodeRunner",
    "TbwError",
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


# ── JavaScript hosts ──────────────────────────────────────────────────────────

# ES5 + typed arrays, so any JavaScriptCore with WebAssembly runs it.
_GLUE = r"""
globalThis.__tbw = (function () {
    var inst = null;
    function mem() { return inst.exports.memory.buffer; }
    function cstr(ptr) {
        var u8 = new Uint8Array(mem()), s = '';
        while (u8[ptr]) s += String.fromCharCode(u8[ptr++]);
        return s;
    }
    return {
        load: function (bytes) {
            inst = new WebAssembly.Instance(new WebAssembly.Module(new Uint8Array(bytes)), {});
            if (inst.exports._initialize) inst.exports._initialize();
            return inst.exports.tbw_sizeof_real() + ':' + cstr(inst.exports.tbw_version());
        },
        call: function (name, input, args) {
            var ptr = inst.exports.tbw_input(input.length);
            if (!ptr) throw new Error('tbw_input: out of memory');
            new Float64Array(mem(), ptr, input.length).set(input);
            var rc = inst.exports[name].apply(null, args);
            if (rc !== 0) return 'E' + rc + ':' + cstr(inst.exports.tbw_last_error());
            var out = new Float64Array(mem(), inst.exports.tbw_output(), inst.exports.tbw_output_len());
            return Array.prototype.join.call(out, ',');
        }
    };
})();
'ok'
"""


def _js_number(x: float) -> str:
    x = float(x)
    if math.isnan(x):
        return "NaN"
    if math.isinf(x):
        return "Infinity" if x > 0 else "-Infinity"
    return repr(x)


def _js_array(values: Sequence[float]) -> str:
    return "[" + ",".join(_js_number(v) for v in values) + "]"


class _JSRunner(WasmRunner):
    """A host whose only primitive is `evaluate(js_source) -> str`."""

    def evaluate(self, src: str) -> str:
        raise NotImplementedError

    def load_bytes(self, wasm: bytes) -> None:
        self.evaluate(_GLUE)
        info = self.evaluate("__tbw.load([" + ",".join(map(str, bytearray(wasm))) + "])")
        size, _, self.version = info.partition(":")
        self.sizeof_real = int(size)

    def call(self, export: str, inputs: Sequence[float], *args: float) -> list[float]:
        text = self.evaluate(f"__tbw.call({json.dumps(export)},{_js_array(inputs)},{_js_array(args)})")
        if text.startswith("E"):
            status, _, message = text[1:].partition(":")
            raise TbwError(int(status), message)
        return [float(v) for v in text.split(",")] if text else []


class JSContextRunner(_JSRunner):
    """JavaScriptCore via Pythonista's objc_util."""

    name = "jscontext"

    def __init__(self) -> None:
        # Pythonista only, and untyped (Objective-C proxies): import it as an explicit Any.
        objc_util: Any = importlib.import_module("objc_util")
        self._ctx: Any = objc_util.ObjCClass("JSContext").alloc().init()
        kind = self.evaluate("typeof WebAssembly")
        if kind != "object":
            raise RuntimeError(f"WebAssembly is not available in this JSContext (typeof WebAssembly = {kind})")

    def evaluate(self, src: str) -> str:
        res = self._ctx.evaluateScript_(src)
        exc = self._ctx.exception()
        if exc:
            self._ctx.setException_(None)
            raise RuntimeError(f"[JS] {exc.toString()}")
        return str(res.toString())


class GIJavaScriptCoreRunner(_JSRunner):
    """WebKitGTK's JavaScriptCore via PyGObject: `apt install gir1.2-javascriptcoregtk-4.1 python3-gi`."""

    name = "gi-jsc"

    def __init__(self) -> None:
        # PyGObject (Linux), untyped GObject-introspection proxies: import it as an explicit Any.
        gi: Any = importlib.import_module("gi")
        gi.require_version("JavaScriptCore", "4.1")
        javascriptcore: Any = importlib.import_module("gi.repository.JavaScriptCore")
        self._ctx: Any = javascriptcore.Context()
        kind = self.evaluate("typeof WebAssembly")
        if kind != "object":
            raise RuntimeError(f"WebAssembly is not available in this JSContext (typeof WebAssembly = {kind})")

    def evaluate(self, src: str) -> str:
        res = self._ctx.evaluate(src, -1)
        exc = self._ctx.get_exception()
        if exc:
            self._ctx.clear_exception()
            raise RuntimeError(f"[JS] {exc.to_string()}")
        return str(res.to_string())


_NODE_LOOP = r"""
const rl = require('readline').createInterface({ input: process.stdin });
rl.on('line', (line) => {
    let reply;
    try { reply = { ok: true, value: String((0, eval)(JSON.parse(line))) }; }
    catch (e) { reply = { ok: false, value: String(e && e.stack || e) }; }
    process.stdout.write(JSON.stringify(reply) + '\n');
});
"""


class NodeRunner(_JSRunner):
    """A long-lived `node` process evaluating one JSON-encoded script per line."""

    name = "node"

    def __init__(self, node: str | None = None) -> None:
        node = node or shutil.which("node")
        if not node:
            raise FileNotFoundError("node not found on PATH")
        self._proc = subprocess.Popen(
            [node, "-e", _NODE_LOOP],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            universal_newlines=True,
            encoding="utf-8",
        )
        if self._proc.stdin is None or self._proc.stdout is None:  # can't happen with PIPE; narrows the types
            raise RuntimeError("node started without stdin/stdout pipes")
        self._stdin = self._proc.stdin
        self._stdout = self._proc.stdout
        atexit.register(self.close)

    def evaluate(self, src: str) -> str:
        self._stdin.write(json.dumps(src) + "\n")
        self._stdin.flush()
        line = self._stdout.readline()
        if not line:
            raise RuntimeError(f"node exited (status {self._proc.poll()})")
        reply = json.loads(line)
        if not reply["ok"]:
            raise RuntimeError("[JS] {}".format(reply["value"]))
        return str(reply["value"])

    def close(self) -> None:
        if self._proc.poll() is None:
            self._stdin.close()
            self._proc.wait(timeout=5)
        self._stdout.close()


# ── wasmtime ──────────────────────────────────────────────────────────────────


class WasmtimeRunner(WasmRunner):
    """The `wasmtime` package: calls the exports directly, no JavaScript involved."""

    name = "wasmtime"

    def __init__(self) -> None:
        import wasmtime  # optional dependency: ImportError here means "host not available"

        self._store: wasmtime.Store = wasmtime.Store()
        self._ex: InstanceExports | None = None
        self._mem: wasmtime.Memory | None = None

    def load_bytes(self, wasm: bytes) -> None:
        import wasmtime

        module = wasmtime.Module(self._store.engine, bytes(wasm))
        inst = wasmtime.Instance(self._store, module, [])
        exports = inst.exports(self._store)
        self._ex = exports
        mem = exports["memory"]
        if not isinstance(mem, wasmtime.Memory):
            raise TypeError("export 'memory' is not a memory")
        self._mem = mem
        if "_initialize" in exports:
            self._fn("_initialize")()
        self.sizeof_real = int(self._fn("tbw_sizeof_real")())
        self.version = self._cstr(int(self._fn("tbw_version")()))

    def _fn(self, name: str) -> Callable[..., Any]:
        import wasmtime

        if self._ex is None:
            raise RuntimeError("no module loaded")
        f = self._ex[name]
        if not isinstance(f, wasmtime.Func):
            raise TypeError(f"export {name!r} is not a function")
        store = self._store
        return lambda *a: f(store, *a)

    def _memory(self) -> wasmtime.Memory:
        if self._mem is None:
            raise RuntimeError("no module loaded")
        return self._mem

    def _read(self, ptr: int, n: int) -> bytearray:
        return self._memory().read(self._store, ptr, ptr + n)

    def _cstr(self, ptr: int) -> str:
        out = bytearray()
        while True:
            chunk = self._read(ptr, 64)
            end = chunk.find(b"\0")
            if end >= 0:
                out += chunk[:end]
                return out.decode("latin-1")
            out += chunk
            ptr += 64

    def call(self, export: str, inputs: Sequence[float], *args: float) -> list[float]:
        n = len(inputs)
        ptr = int(self._fn("tbw_input")(n))
        if not ptr:
            raise TbwError(1, "tbw_input: out of memory")
        self._memory().write(self._store, struct.pack(f"<{n}d", *inputs), ptr)
        rc = int(self._fn(export)(*args))
        if rc != 0:
            raise TbwError(rc, self._cstr(int(self._fn("tbw_last_error")())))
        out_n = int(self._fn("tbw_output_len")())
        raw = self._read(int(self._fn("tbw_output")()), out_n * 8)
        return list(struct.unpack(f"<{out_n}d", raw))


# ── Host selection ────────────────────────────────────────────────────────────

HOSTS: Final[dict[str, type[WasmRunner]]] = {
    "jscontext": JSContextRunner,
    "wasmtime": WasmtimeRunner,
    "gi-jsc": GIJavaScriptCoreRunner,
    "node": NodeRunner,
}

# Tried in this order when nothing is chosen explicitly. Each constructor is its own availability
# probe: it raises when its runtime isn't there (ImportError for objc_util/wasmtime/gi, a missing
# `node` binary, a JS engine without WebAssembly), so "available" means "could actually start".
AUTO_ORDER: Final[tuple[str, ...]] = ("jscontext", "wasmtime", "gi-jsc", "node")


def default_runner() -> WasmRunner:
    """Start a host: $TINY_BCLIBC_HOST if set, else the first of AUTO_ORDER that starts.

    AUTO_ORDER puts Pythonista's JSContext first (only exists there), then wasmtime (in-process,
    fastest, when installed), then WebKitGTK JavaScriptCore (Linux with PyGObject), then Node.
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

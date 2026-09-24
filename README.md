# tiny-bclibc-wasm

LGPL WebAssembly build of the tiny_bclibc ballistic solver for CPython, PyPy and Pythonista.

[![license]][LGPL-3]
[![pypi]][PyPiUrl]
[![coverage]][CodecovUrl]
[![py-versions]][sources]
[![Made in Ukraine]][SWUBadge]

[![powered by bclibc]][bclibc]
[![powered by webassembly]][WebAssembly]

[![Tests](https://github.com/ballistics-lab/tiny-bclibc-wasm-py/actions/workflows/tests.yml/badge.svg)](https://github.com/ballistics-lab/tiny-bclibc-wasm-py/actions/workflows/tests.yml)
[![Pre-commit](https://github.com/ballistics-lab/tiny-bclibc-wasm-py/actions/workflows/pre-commit.yml/badge.svg)](https://github.com/ballistics-lab/tiny-bclibc-wasm-py/actions/workflows/pre-commit.yml)

`import tiny_bclibc` for **CPython, PyPy and Pythonista**, with the same API as the natmod `.mpy`
from [micropython-bclibc](https://github.com/ballistics-lab/micropython-bclibc). A script written
for a MicroPython board runs here unchanged.

Inside is [tiny_bclibc](https://github.com/ballistics-lab/bclibc/tree/main/tiny_bclibc) (pure C99)
compiled to WebAssembly: one ~58 KB `.wasm` that imports nothing. A small runner loads it into
whichever WebAssembly host is available. No C extension, no per-platform build.

```python
import tiny_bclibc as bc

shot = bc.Shot(bc=0.310, weight_grain=168.0, diameter_inch=0.308, length_inch=1.2,
               muzzle_velocity_fps=2750.0, sight_height_ft=0.125, twist_inch=11.0)
bc.zero(shot, 100 / 0.3048)                               # 100 m zero
hold_rad, windage_rad, point = bc.aim(shot, 300 / 0.3048)
rows, reason = bc.fire(shot, bc.Request(range_limit_ft=3280.84, range_step_ft=328.084))
for r in rows:
    print(r[bc.T_DISTANCE], r[bc.T_VELOCITY], r[bc.T_HEIGHT])
```

See `examples/basic.py`, and micropython-bclibc's README ("Module API", "Usage examples") for the
full API: `Shot`, `Wind`, `Config`, `Request`, `integrate`, `integrate_stream`, `integrate_at`,
`find_zero_angle`, `zero_point`, `zero`, `aim`, `fire`, `find_apex`, `find_max_range`, `MultiBC`,
and the `TRAJ_FLAG_*`, `T_*` and `INTERP_*` constants.

## Installation

### uv

```shell
uv add tiny-bclibc-wasm

# With wasmtime, the in-process WebAssembly host (otherwise Node or WebKitGTK JavaScriptCore is used)
uv add tiny-bclibc-wasm[wasmtime]

# As a py-ballisticcalc engine (see "py-ballisticcalc engine" below)
uv add tiny-bclibc-wasm[pybc]

# Everything
uv add tiny-bclibc-wasm[wasmtime,pybc]
```

### pip

```shell
pip install tiny-bclibc-wasm

# With wasmtime, the in-process WebAssembly host (otherwise Node or WebKitGTK JavaScriptCore is used)
pip install tiny-bclibc-wasm[wasmtime]

# As a py-ballisticcalc engine (see "py-ballisticcalc engine" below)
pip install tiny-bclibc-wasm[pybc]

# Everything
pip install tiny-bclibc-wasm[wasmtime,pybc]
```

### Pythonista and PythonIDE (iOS)

The ordinary wheel: it is pure Python (`py3-none-any`) with the `.wasm` modules inside. In StaSh (Pythonista) or
PythonIDE's pip, `pip install tiny-bclibc-wasm`; it pulls in [wasmhost](https://github.com/ballistics-lab/py-wasmhost),
which runs the WebAssembly. See [Pythonista](#pythonista).

## Typing

The package is fully typed and ships `py.typed`. The code uses Python 3.10 annotations, which is
what Pythonista runs, and passes pyright in strict mode. `src/tiny_bclibc/__init__.pyi` describes
the public API. In it, a trajectory row is `Row`, a `tuple` of 15 floats followed by the `int` flag.
The `.s` fields of `Shot`, `Wind`, `Config` and `Request` are typed dataclasses (`ShotProps`,
`WindFields`, ...). mypy's `stubtest` (a pre-commit hook) checks that the stub matches the module.

## WebAssembly hosts

| Host | Where | How it is detected |
|---|---|---|
| `jscontext` | Pythonista (iOS) | JavaScriptCore's `JSContext` through `objc_util` |
| `wasmtime` | anywhere with the `wasmtime` package | `import wasmtime` (`uv add tiny-bclibc-wasm[wasmtime]`) |
| `wasm3` | CPython 3.11+ with [pywasm3](https://github.com/wasm3/pywasm3) | `import wasm3`; install it from git: `uv add "pywasm3 @ git+https://github.com/wasm3/pywasm3"` (its PyPI release predates the API used here) |
| `gi-jsc` | Linux | WebKitGTK's JavaScriptCore through PyGObject (`apt install gir1.2-javascriptcoregtk-4.1 python3-gi`) |
| `node` | anywhere with Node.js | `node` on `PATH` |

With nothing configured, the first host that starts wins, in the order shown. Each host's
constructor is its own probe: it fails when its runtime is missing (`objc_util`/`wasmtime`/`gi`
won't import, `node` isn't on `PATH`, the JS engine has no `WebAssembly`). You can override it
with `TINY_BCLIBC_HOST=<name>` or `tiny_bclibc.set_host("<name>")`. `tiny_bclibc.host()` reports
which host is in use.

All the JS hosts run one shared glue snippet, and wasmtime calls the exports directly.
`tests/test_hosts.py` checks that every host available on the machine returns bit-identical
results.

## py-ballisticcalc engine

`tiny_bclibc.pybc` makes tiny_bclibc an integration engine for
[py-ballisticcalc](https://github.com/o-murphy/py-ballisticcalc) 3.0.0b1 or newer (Python 3.11+).
The package registers it as a py-ballisticcalc entry point, so installing it is enough:

```bash
uv add "tiny-bclibc-wasm[pybc]"
```

```python
from py_ballisticcalc import Calculator

calc = Calculator(engine="tiny_bclibc_wasm+tsitouras-dp")
```

| Engine | Class | Name (py-ballisticcalc 3.0.0b3+) | Legacy name (2.2.10+, deprecated in 3.0.0b3) |
|---|---|---|---|
| double precision | `TinyBclibcWasmTsitourasEngineDP` | `tiny_bclibc_wasm+tsitouras-dp` | `tiny_bclibc_wasm_engine` |
| single precision | `TinyBclibcWasmTsitourasEngineSP` | `tiny_bclibc_wasm+tsitouras-sp` | `tiny_bclibc_wasm_sp_engine` |

On Python 3.10, the `[pybc]` extra installs py-ballisticcalc 2.2.10, its last release for 3.10. That
is partial backward compatibility: the engines work, but 3 of 2.2.10's tests fail, because 2.x
merges ZERO/MACH/APEX events onto a nearby RANGE row and these engines keep them as separate rows,
as 3.x does. Use the legacy name there (2.x has no `<engine>+<method>` names).

The engine runs on the same WebAssembly host as the rest of the package (see above). It does not
support `dense_output`. The single-precision engine has float32's limits: 18 of py-ballisticcalc's
tests fail on it. Pythonista has no entry points, so there you pass the import path,
`Calculator(engine="tiny_bclibc.pybc:TinyBclibcWasmTsitourasEngineDP")`; see
`examples/py_ballisticcalc_engine.py`.

The engine is tested with py-ballisticcalc's own suite. The `py-ballisticcalc` submodule pins py-ballisticcalc,
and `uv sync` installs it from there, so the package and its `tests/` always come from one commit:

```bash
git submodule update --init            # bclibc + py-ballisticcalc
uv run pytest py-ballisticcalc/tests --engine=tiny_bclibc_wasm+tsitouras-dp
git -C py-ballisticcalc checkout v3.0.0 && uv lock # move to another py-ballisticcalc release
```

## Build

The build compiles the `.wasm` modules itself: `setup.py` runs `build_wasm.py` on every build.
The compiler is the `ziglang` PyPI package, which is a build requirement, so nothing needs to be
installed beforehand. The package version comes from git tags via `setuptools_scm`.

```bash
git submodule update --init
uv sync                        # editable install; compiles src/tiny_bclibc/tiny_bclibc_{dp,sp}.wasm
uv build                       # sdist + wheel (the wheel is built from the sdist, so it compiles too)
uv run --with ziglang python build_wasm.py    # just recompile the modules (ziglang only exists in the isolated build env)
```

`uv sync` recompiles the modules when the wrapper sources, the headers or the build hooks change.
The sdist carries only `bclibc/tiny_bclibc/{include,wasm}` from the submodule, plus the bclibc
version, so `pip install tiny_bclibc_wasm-*.tar.gz` builds anywhere Python does.
`TINY_BCLIBC_CC="clang --sysroot=<wasi-sysroot>"` switches to another wasm32 compiler.

Precision: double by default. `TINY_BCLIBC_PRECISION=single` loads the float32 build, the same
trade-off as the natmod's single-precision builds. `TINY_BCLIBC_WASM=/path/to/x.wasm` loads a
specific module.

## Test

```bash
uv run pytest                              # everything below, on the automatically picked backend
uv run pytest --wasm-backend node          # ... on one backend: wasmtime | wasm3 | node | gi-jsc
uv run pytest --cov                        # with coverage
uv run python tests/run_natmod_suite.py    # just the natmod suite, current host/precision
uv run pyright && uv run ruff check        # types, lint
uv run pytest py-ballisticcalc/tests --engine=tiny_bclibc_wasm+tsitouras-dp    # py-ballisticcalc's suite (Python 3.11+)
```

If the backend passed to `--wasm-backend` can't start, the run stops with an error; the tests are
never silently skipped. CI (`.github/workflows/tests.yml`) runs the suite on wasmtime and on Node
on Linux, Windows and macOS with CPython 3.10, CPython 3.14 and PyPy 3.11, and on wasm3 wherever
pywasm3 installs (CPython 3.11+). It also runs it on
WebKitGTK JavaScriptCore with and without JIT, then combines coverage from all three runtimes and
uploads it to Codecov. Every leg on Python 3.11+ also runs py-ballisticcalc's suite on the
`tiny_bclibc_wasm+tsitouras-dp` engine.

`pytest` checks that every available host returns identical results (`tests/test_hosts.py`). It
also runs micropython-bclibc's `tests/test_bclibc.py` unmodified in both precisions
(`tests/test_natmod_suite.py`); that test is skipped when the suite can't be found.

`run_natmod_suite.py` runs the natmod's own acceptance suite against this package. By default it
takes the suite from a sibling `../micropython-bclibc` checkout. Current results: 19/19 on CPython
3.8–3.14 under wasmtime, Node and WebKitGTK JavaScriptCore (with and without JIT), in both
precisions. On PyPy, 17/19: the two failing checks are the suite's own memory measurements, which
use `tracemalloc`, and PyPy doesn't have it.

## Pythonista

Install it with pip (see above), then `import tiny_bclibc as bc`. JSContext is picked automatically. The
package is plain Python; its one dependency is `wasmhost`, which has a self-test to run on the device
(`import wasmhost; wasmhost.selftest()`) that reports the Objective-C bridge, `WebAssembly` and `BigInt` and the
cost of a call. It has passed on Pythonista (StaSh, Python 3.10.4) and on PythonIDE (Python 3.14.7).

## Differences from the natmod

- `Shot`/`Wind`/`Config`/`Request` hold Python floats instead of float32 bytearrays. That way the
  double-precision module gets full-precision inputs. They keep the same `(buf, s)` namedtuple
  shape with the fields on `.s` (`shot.s.props.barrel_elevation_rad`, `w.s.velocity_fps`, ...),
  but `buf` is `None`.
- `integrate_stream` runs the callbacks after the trajectory is computed, so the whole
  trajectory costs one host call. An early stop still reports reason 5, but it doesn't skip the
  remaining integration.
- `bench()` measures the WebAssembly host's f32/f64 speed (the same loops, compiled to wasm), not the CPU directly. It is a way to compare hosts.

## License

Copyright (C) 2026 Dmytro Yaroshenko (o-murphy)

This library is free software: you can redistribute it and/or modify it under the terms of the
**GNU Lesser General Public License v3.0** (see [LICENSE](LICENSE)), the same license as
[bclibc](https://github.com/ballistics-lab/bclibc), whose `tiny_bclibc` the shipped `.wasm` modules
are compiled from.

[sources]:
https://github.com/ballistics-lab/tiny-bclibc-wasm-py

[releases]:
https://github.com/ballistics-lab/tiny-bclibc-wasm-py/releases/latest

[license]:
https://img.shields.io/github/license/ballistics-lab/tiny-bclibc-wasm-py?style=flat-square

[LGPL-3]:
https://opensource.org/licenses/LGPL-3.0-only

[pypi]:
https://img.shields.io/pypi/v/tiny-bclibc-wasm?style=flat-square&logo=pypi

[PyPiUrl]:
https://pypi.org/project/tiny-bclibc-wasm/

[coverage]:
https://codecov.io/gh/ballistics-lab/tiny-bclibc-wasm-py/graph/badge.svg

[CodecovUrl]:
https://codecov.io/gh/ballistics-lab/tiny-bclibc-wasm-py

[py-versions]:
https://img.shields.io/pypi/pyversions/tiny-bclibc-wasm?style=flat-square

[Made in Ukraine]:
https://img.shields.io/badge/made_in-Ukraine-ffd700.svg?labelColor=0057b7&style=flat-square

[SWUBadge]:
https://stand-with-ukraine.pp.ua

[bclibc]: https://github.com/ballistics-lab/bclibc

[powered by bclibc]:
https://img.shields.io/badge/bclibc-0d1228?logo=data%3Aimage%2Fsvg%2Bxml%3Bbase64%2CPD94bWwgdmVyc2lvbj0iMS4wIiBzdGFuZGFsb25lPSJubyI%2FPgo8IURPQ1RZUEUgc3ZnIFBVQkxJQyAiLS8vVzNDLy9EVEQgU1ZHIDIwMDEwOTA0Ly9FTiIgImh0dHA6Ly93d3cudzMub3JnL1RSLzIwMDEvUkVDLVNWRy0yMDAxMDkwNC9EVEQvc3ZnMTAuZHRkIj4KPHN2ZyB2ZXJzaW9uPSIxLjAiIHhtbG5zPSJodHRwOi8vd3d3LnczLm9yZy8yMDAwL3N2ZyIgd2lkdGg9IjEwMjQuMDAwMDAwcHQiIGhlaWdodD0iMTAyNC4wMDAwMDBwdCIgdmlld0JveD0iMCAwIDEwMjQuMDAwMDAwIDEwMjQuMDAwMDAwIiBwcmVzZXJ2ZUFzcGVjdFJhdGlvPSJ4TWlkWU1pZCBtZWV0Ij4KCTxjaXJjbGUgY3g9IjUxMiIgY3k9IjUxMiIgcj0iNTEyIiBmaWxsPSIjMGQxMjI4IiAvPgoJPGcgdHJhbnNmb3JtPSJ0cmFuc2xhdGUoLTEwMCwxMTI0KSBzY2FsZSgwLjEyMDAwMCwtMC4xMjAwMDApIiBmaWxsPSIjRkZGRkZGIiBzdHJva2U9Im5vbmUiPgoJCTxwYXRoIGQ9Ik01MDU1IDgwNzEgYy0xNjcgLTMzMyAtMjczIC03NjggLTI5MiAtMTE5OCBsLTYgLTE0MyAzNDYgMCAzNDcgMCAwCjYzIGMwIDI3NSAtODAgNzMxIC0xNzUgMTAwNyAtMzkgMTEyIC0xNDUgMzQzIC0xNjMgMzU0IC03IDQgLTI5IC0yOCAtNTcgLTgzegptLTE1IC0yODkgYy00NiAtMjI1IC05MCAtNjYzIC05MCAtODk0IDAgLTEwNCAtMiAtMTA4IC02MSAtMTA4IGwtNDkgMCAwIDc4CmMxIDE1OSA0OCA0ODIgMTAxIDY5MCAzNCAxMzQgMTE5IDM5NiAxMjUgMzg5IDMgLTMgLTkgLTcyIC0yNiAtMTU1eiIgLz4KCQk8cGF0aCBkPSJNNDcxMCA2NDA2IGwwIC0yNDQgMjMgLTYgYzEyIC0zIDMyIC02IDQ1IC02IGwyMiAwIDAgMjI1IDAgMjI1IDY1IDAKNjUgMCAwIC0yMjUgMCAtMjI1IDI4MyAyIDI4MiAzIDMgMjQ4IDIgMjQ3IC0zOTUgMCAtMzk1IDAgMCAtMjQ0eiIgLz4KCQk8cGF0aCBkPSJNNDQyNCA2MTExIGMtMTggLTUgLTQ4IC0xOCAtNjggLTMwIC0xMzcgLTg1IC0xMjAgLTMwMCAyOSAtMzcwIGw0NgotMjEgLTMgLTUzMyAtMyAtNTMyIC0yMyAtNTggYy0xOCAtNDUgLTU0NSAtODUwIC04NzkgLTEzNDMgLTc2IC0xMTMgLTExMgotMjkxIC04MyAtNDE1IDQxIC0xNzcgMTY5IC0zMTIgMzQwIC0zNTkgNTkgLTE3IDI1OTMgLTE1IDI2NTUgMiAxMTQgMzAgMjMzCjEyMiAyODcgMjI0IDc2IDE0MiA3NyAzNDMgMyA0ODYgLTI4IDU0IC0xMzMgMjEzIC01NzMgODc1IC0xNzYgMjY2IC0zMzEgNTA5Ci0zNDQgNTQwIC0yMyA1OCAtMjMgNjAgLTI2IDU4OCBsLTMgNTMwIDQ1IDE4IGM1MiAyMiAxMDEgODAgMTE3IDE0MSAyNCA5MAotMjMgMTk2IC0xMDYgMjM2IC01NCAyNiAtMTk5IDM1IC0yMDEgMTMgLTEgLTcgLTIgLTE3IC0zIC0yMiAwIC01IC04OCAtNwotMjA4IC0zIC0xNTQgNCAtMjA0IDIgLTE5OSAtNiA0IC03IDE1IC0xMiAyNiAtMTIgMTAgMCA5MiAtMTMgMTgxIC0yOSA5MCAtMTYKMjA2IC0zMyAyNTggLTM3IDEwOSAtNyAxNDEgLTI3IDE0MSAtODYgLTEgLTYwIC00OCAtOTggLTEyNSAtOTggbC00NiAwIDMKLTU5MiAzIC01OTMgMjUgLTcwIGMxOCAtNTIgODAgLTE1NCAyNDEgLTM5NSA0NzYgLTcxNCA2ODkgLTEwNDMgNzEwIC0xMDk3IDE2Ci00NCAyMiAtNzkgMjIgLTE0MyAwIC0xNzQgLTgxIC0yOTMgLTIzNyAtMzQ3IC00OCAtMTcgLTEyNSAtMTggLTEzMzEgLTE4CmwtMTI4MCAwIC02NSAzMSBjLTc5IDM4IC0xMzEgODkgLTE2OCAxNjMgLTI1IDUxIC0yNyA2NiAtMjcgMTcxIDAgOTggMyAxMjIKMjIgMTYzIDEzIDI3IDExNiAxODkgMjI5IDM2MCAxMTQgMTcyIDMwOCA0NjQgNDMyIDY1MCAxMjMgMTg1IDIzNiAzNjMgMjUyCjM5NSA1NCAxMTEgNTUgMTIwIDU1IDc0MiBsMCA1NzUgLTUwIDYgYy0yNyAzIC01OCA5IC02OCAxNCAtMjcgMTEgLTQ5IDYyIC00Mgo5NCAxMCA0OCA0MyA2OSAxMTAgNzMgbDYwIDMgMCA2MCAwIDYwIC01MCAyIGMtMjcgMSAtNjQgLTIgLTgxIC02eiIgLz4KCQk8cGF0aCBkPSJNNDcwMCA1MzY5IGMwIC00MjggLTQgLTcwNyAtMTEgLTc1MiAtMjMgLTE1NyAtNTggLTIzMCAtMjYzIC01NDAKLTg0IC0xMjggLTE5NSAtMjk3IC0yNDggLTM3NyAtNTIgLTgwIC0xNjYgLTI1MyAtMjUzIC0zODUgLTg3IC0xMzIgLTE2OSAtMjYwCi0xODIgLTI4NCAtNDMgLTgyIC0yNiAtMTk3IDM5IC0yNTggNTkgLTU2IC03IC01MyAxMzI3IC01MyBsMTIyOSAwIDUyIDI4IGM5OAo1MSAxMzIgMTc2IDc3IDI4MiAtMjMgNDUgLTI2MSA0MTAgLTYzMyA5NzUgLTIzOCAzNjEgLTI1OCAzOTUgLTMwMiA1NDQgLTE0CjQ5IC0xNyAxMzkgLTIyIDcxNiBsLTUgNjYwIC0xMTUgMTcgYy02MyAxMCAtMTg1IDI5IC0yNzEgNDMgLTIxNyAzNSAtMTk5IDM5Ci0xOTkgLTQ4IDAgLTQxIC00IC0xNTQgLTEwIC0yNTMgLTUgLTk4IC0xNyAtMzEyIC0yNSAtNDc0IC05IC0xNjIgLTIwIC0zNDcKLTI1IC00MTAgLTUgLTYzIC0xMCAtMTQ1IC0xMCAtMTgyIDAgLTM4IC00IC02OCAtOCAtNjggLTggMCAtMjggNTcwIC0zOSAxMTU3CmwtNiAzMzEgLTMwIDYgYy0xNiAzIC0zOCA2IC00OCA2IC0xOCAwIC0xOSAtMjIgLTE5IC02ODF6IG0xMDMyIC0xNDI2IGM5IC0xMAo3NCAtMTA2IDE0NCAtMjE1IDcxIC0xMDggMjAzIC0zMDkgMjk0IC00NDcgMjEwIC0zMTggMjAyIC0zMDUgMjA0IC0zNTMgMSAtMzIKLTUgLTQ1IC0yNyAtNjQgbC0yOCAtMjQgLTEyMTUgMCAtMTIxNSAwIC0yNCAyNSBjLTE5IDE4IC0yNSAzNSAtMjUgNjggMCA0MQoxNyA2OSAyMDYgMzU4IDExNCAxNzMgMjU5IDM5NCAzMjMgNDkxIGwxMTYgMTc4IDYxNiAwIGM1NzQgMCA2MTcgLTEgNjMxIC0xN3oiIC8%2BCgk8L2c%2BCjwvc3ZnPgo%3D&label=powered%20by

[WebAssembly]: https://webassembly.org

[powered by webassembly]:
https://img.shields.io/badge/webassembly-%23654FF0?style=flat-square&logo=webassembly&logoColor=white&label=powered%20by

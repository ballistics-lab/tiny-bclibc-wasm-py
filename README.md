# tiny-bclibc-wasm-py

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

## WebAssembly hosts

| Host | Where | How it is detected |
|---|---|---|
| `jscontext` | Pythonista (iOS) | JavaScriptCore's `JSContext` through `objc_util` |
| `wasmtime` | anywhere with the `wasmtime` package | `import wasmtime` (`uv add tiny-bclibc-wasm-py[wasmtime]`) |
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

## Build

The build compiles the `.wasm` modules itself: `setup.py` runs `build_wasm.py` on every build.
The compiler is the `ziglang` PyPI package, which is a build requirement, so nothing needs to be
installed beforehand. The package version comes from git tags via `setuptools_scm`.

```bash
git submodule update --init
uv sync                        # editable install; compiles src/tiny_bclibc/tiny_bclibc_{dp,sp}.wasm
uv build                       # sdist + wheel (the wheel is built from the sdist, so it compiles too)
uv run python build_wasm.py    # just recompile the modules
```

`uv sync` recompiles the modules when the wrapper sources, the headers or the build hooks change.
The sdist carries only `bclibc/tiny_bclibc/{include,wasm}` from the submodule, plus the bclibc
version, so `pip install tiny-bclibc-wasm-py-*.tar.gz` builds anywhere Python does.
`TINY_BCLIBC_CC="clang --sysroot=<wasi-sysroot>"` switches to another wasm32 compiler.

Precision: double by default. `TINY_BCLIBC_PRECISION=single` loads the float32 build, the same
trade-off as the natmod's single-precision builds. `TINY_BCLIBC_WASM=/path/to/x.wasm` loads a
specific module.

## Test

```bash
uv run pytest                              # everything below, on the automatically picked runtime
uv run pytest --wasm-runtime node          # ... on one specific runtime: wasmtime | node | gi-jsc
uv run pytest --cov                        # with coverage
uv run python tests/run_natmod_suite.py    # just the natmod suite, current host/precision
uv run pyright && uv run ruff check        # types, lint
```

If the runtime passed to `--wasm-runtime` can't start, the run stops with an error; the tests are
never silently skipped. CI (`.github/workflows/tests.yml`) runs the suite on wasmtime and on Node
on Linux, Windows and macOS with CPython 3.10, CPython 3.14 and PyPy 3.11. It also runs it on
WebKitGTK JavaScriptCore with and without JIT, then combines coverage from all three runtimes and
uploads it to Codecov.

`pytest` checks that every available host returns identical results (`tests/test_hosts.py`). It
also runs micropython-bclibc's `tests/test_bclibc.py` unmodified in both precisions
(`tests/test_natmod_suite.py`); that test is skipped when the suite can't be found.

`run_natmod_suite.py` runs the natmod's own acceptance suite against this package. By default it
takes the suite from a sibling `../micropython-bclibc` checkout. Current results: 19/19 on CPython
3.8–3.14 under wasmtime, Node and WebKitGTK JavaScriptCore (with and without JIT), in both
precisions. On PyPy, 17/19: the two failing checks are the suite's own memory measurements, which
use `tracemalloc`, and PyPy doesn't have it.

## Pythonista

Copy `src/tiny_bclibc/` (with the built `.wasm` files) into Pythonista, next to your script, then
`import tiny_bclibc as bc`. JSContext is picked automatically. The package is plain Python and needs
no dependencies.

## Differences from the natmod

- `Shot`/`Wind`/`Config`/`Request` hold Python floats instead of float32 bytearrays. That way the
  double-precision module gets full-precision inputs. They keep the same `(buf, s)` namedtuple
  shape with the fields on `.s` (`shot.s.props.barrel_elevation_rad`, `w.s.velocity_fps`, ...),
  but `buf` is `None`.
- `integrate_stream` runs the callbacks after the trajectory is computed, so the whole
  trajectory costs one host call. An early stop still reports reason 5, but it doesn't skip the
  remaining integration.
- No `bench()`: a native FPU benchmark means nothing through a WebAssembly host.

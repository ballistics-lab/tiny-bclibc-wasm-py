"""Zero, aim and fire with tiny_bclibc -- the same script runs under MicroPython (natmod .mpy),
CPython, PyPy and Pythonista (this repo's WebAssembly-backed package).

    uv run python examples/basic.py     # from the repo root (`uv sync` builds the .wasm modules)

In Pythonista: copy the `tiny_bclibc/` folder (with its .wasm) next to this file and run it.
"""

import os
import sys
import time

# Run from a checkout without installing: make src/ importable. (Pythonista: tiny_bclibc/ sits
# next to this file, which is already on sys.path.)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import tiny_bclibc as bc

M = 1 / 0.3048  # metres -> feet

shot = bc.Shot(
    bc=0.310,
    weight_grain=168.0,
    diameter_inch=0.308,
    length_inch=1.2,
    muzzle_velocity_fps=2750.0,
    sight_height_ft=0.125,  # 1.5 in
    twist_inch=11.0,
    winds=[bc.Wind(velocity_fps=3 * M, direction_from_rad=1.5708)],  # 3 m/s from the right
)

t0 = time.time()
bc.zero(shot, 100 * M)
hold, windage, point = bc.aim(shot, 300 * M)
rows, reason = bc.fire(shot, bc.Request(range_limit_ft=1000 * M, range_step_ft=100 * M))
dt = time.time() - t0

print(f"tiny_bclibc {bc.version()} via {bc.host()}  ({dt * 1000:.1f} ms)")
print(f"aim @ 300 m: hold={hold * 1000:+.2f} mil  windage={windage * 1000:+.2f} mil")
for r in rows:
    print(
        f"{r[bc.T_DISTANCE] / M:6.0f} m  {r[bc.T_VELOCITY] * 0.3048:7.1f} m/s  drop={r[bc.T_HEIGHT] * 30.48:8.1f} cm  wind={r[bc.T_WINDAGE] * 30.48:7.1f} cm"
    )

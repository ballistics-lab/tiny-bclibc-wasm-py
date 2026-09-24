"""Example: tiny_bclibc as a py-ballisticcalc engine -- zero, aim and fire.

Runs as-is on a desktop (wasmtime, wasm3, Node or WebKitGTK JavaScriptCore, whichever the
`tiny_bclibc` package finds) and in Pythonista on iOS (JavaScriptCore's JSContext).

Desktop:
    uv pip install "tiny-bclibc-wasm[pybc,wasmtime]"     # wasmtime is optional
    python examples/py_ballisticcalc_engine.py
    TINY_BCLIBC_HOST=node python examples/py_ballisticcalc_engine.py    # pick the host yourself

With the package installed, py-ballisticcalc also finds the engine by name:
    Calculator(engine="tiny_bclibc_wasm+tsitouras")

Pythonista has no entry points, so this names the class by its import path, which py-ballisticcalc
loads directly. Copy these into Pythonista's files, next to this script:
    py_ballisticcalc/    (the package; needs typing_extensions importable too)
    tiny_bclibc/         (from the tiny-bclibc-wasm wheel, with its .wasm files)
"""

import time

from py_ballisticcalc import Ammo, Calculator, DragModel, Shot, TableG7, Unit, Weapon, Wind


def main() -> None:
    dm = DragModel(bc=0.310, drag_table=TableG7, weight=Unit.Grain(168), diameter=Unit.Inch(0.308))
    ammo = Ammo(dm=dm, mv=Unit.FPS(2750))
    weapon = Weapon(sight_height=Unit.Inch(1.5), twist=Unit.Inch(11))

    t0 = time.perf_counter()
    calc = Calculator(engine="tiny_bclibc.pybc:TinyBclibcWasmTsitourasEngineDP")
    shot = Shot(ammo=ammo, weapon=weapon, winds=[Wind(Unit.MPS(3), Unit.Degree(90))])
    calc.set_weapon_zero(shot, Unit.Meter(100))
    t1 = time.perf_counter()
    hold, windage, aim_point = calc.aim(shot, Unit.Meter(300))
    hit = calc.fire(shot, trajectory_range=Unit.Meter(1000), trajectory_step=Unit.Meter(100))
    t2 = time.perf_counter()

    print(f"load + zero: {(t1 - t0) * 1000:.1f} ms, aim + fire: {(t2 - t1) * 1000:.1f} ms")
    print(
        f"aim @ 300 m: hold={hold >> Unit.Mil:+.2f} mil  windage={windage >> Unit.Mil:+.2f} mil  "
        f"velocity={aim_point.velocity >> Unit.MPS:.1f} m/s"
    )
    for row in hit.samples:
        print(
            f"{row.distance >> Unit.Meter:6.0f} m  {row.velocity >> Unit.MPS:7.1f} m/s  "
            f"drop={row.height >> Unit.Centimeter:8.1f} cm  wind={row.windage >> Unit.Centimeter:7.1f} cm"
        )


if __name__ == "__main__":
    main()

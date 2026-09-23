"""Every available WebAssembly host must return bit-identical results for the same module.

Runs under pytest or plainly (`python tests/test_hosts.py`); hosts that aren't available here are
skipped. The JS hosts share one glue snippet and wasmtime calls the exports directly, so agreement
also checks the JS number round trip (shortest-repr strings) against raw memory reads.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import tiny_bclibc as bc
from tiny_bclibc import _runner

HOSTS = ("wasmtime", "node", "gi-jsc", "jscontext")


def _available():
    out = {}
    path = os.path.join(ROOT, "src", "tiny_bclibc", "tiny_bclibc_dp.wasm")
    for name in HOSTS:
        try:
            r = _runner.HOSTS[name]()
            r.load_file(path)
        except Exception:  # noqa: BLE001, S112 -- host not available here; skip it
            continue
        out[name] = r
    return out


def _results():
    m = 1 / 0.3048
    shot = bc.Shot(
        bc=0.310,
        weight_grain=168.0,
        diameter_inch=0.308,
        length_inch=1.2,
        muzzle_velocity_fps=2750.0,
        sight_height_ft=0.125,
        twist_inch=11.0,
        latitude_deg=50.0,
        azimuth_deg=90.0,
        winds=[bc.Wind(4 * m, 1.2, 500 * m), bc.Wind(2 * m, -0.5)],
    )
    zero = bc.zero(shot, 300 * m)
    rows, reason = bc.fire(shot, bc.Request(2000 * m, 50 * m, filter_flags=bc.TRAJ_FLAG_ALL))
    return (
        zero,
        rows,
        reason,
        bc.aim(shot, 800 * m),
        bc.find_apex(shot),
        bc.integrate_at(shot, bc.INTERP_TIME, 1.0),
        bc.find_max_range(shot, 0.0, 90.0),
    )


def test_hosts_agree():
    hosts = _available()
    if len(hosts) < 2:
        try:
            import pytest
        except ImportError:
            print(f"fewer than two hosts available ({list(hosts)}), nothing to compare")
            return
        pytest.skip(f"fewer than two hosts available: {list(hosts)}")
    results = {}
    for name, runner in hosts.items():
        bc.set_host(runner)
        results[name] = _results()
    bc.set_host(None)
    names = list(results)
    for other in names[1:]:
        assert results[other] == results[names[0]], f"{other} differs from {names[0]}"
    print("identical across: " + ", ".join(names))


if __name__ == "__main__":
    test_hosts_agree()

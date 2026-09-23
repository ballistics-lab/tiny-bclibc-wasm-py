"""In-process API checks on the runtime selected by --wasm-runtime (see conftest.py).

The natmod suite (test_natmod_suite.py) covers the API broadly but in a subprocess; these run inside
pytest's own interpreter, against reference values from py-ballisticcalc / the natmod suite.
"""

import math

import pytest

import tiny_bclibc as bc

M = 1 / 0.3048  # metres -> feet
ZERO_300M_REF = 0.002502  # rad; natmod suite's py-ballisticcalc reference


@pytest.fixture
def shot():
    return bc.Shot(
        bc=0.310,
        weight_grain=168.0,
        diameter_inch=0.308,
        length_inch=1.2,
        muzzle_velocity_fps=2750.0,
        sight_height_ft=0.125,
        twist_inch=11.0,
    )


def test_runtime_is_the_requested_one(pytestconfig):
    runtime = pytestconfig.getoption("--wasm-runtime")
    if runtime:
        assert bc.host() == runtime


def test_zero_matches_reference(shot):
    assert bc.zero(shot, 300 * M) == pytest.approx(ZERO_300M_REF, abs=1e-5)
    assert shot.s.props.barrel_elevation_rad == pytest.approx(ZERO_300M_REF, abs=1e-5)


def test_aim_after_zero_needs_no_hold(shot):
    bc.zero(shot, 300 * M)
    hold, windage, point = bc.aim(shot, 300 * M)
    assert hold == pytest.approx(0.0, abs=1e-6)
    # No wind, but a right-hand 11" twist: spin drift alone pushes it slightly right (~0.06 mil).
    assert 0.0 < windage < 1e-3
    assert point[bc.T_DISTANCE] == pytest.approx(300 * M, abs=1e-3)


def test_fire_rows_are_range_steps(shot):
    bc.zero(shot, 100 * M)
    rows, reason = bc.fire(shot, bc.Request(range_limit_ft=1000 * M, range_step_ft=100 * M))
    assert reason == 1  # target range reached
    assert [round(r[bc.T_DISTANCE] / M) for r in rows] == list(range(0, 1001, 100))
    assert all(isinstance(r[bc.T_FLAG], int) for r in rows)
    velocities = [r[bc.T_VELOCITY] for r in rows]
    assert velocities == sorted(velocities, reverse=True)


def test_wind_pushes_downwind(shot):
    calm = bc.fire(shot, bc.Request(range_limit_ft=500 * M, range_step_ft=500 * M))[0][-1]
    windy_shot = bc.Shot(**_kwargs(shot), winds=[bc.Wind(velocity_fps=10.0, direction_from_rad=math.pi / 2)])
    windy = bc.fire(windy_shot, bc.Request(range_limit_ft=500 * M, range_step_ft=500 * M))[0][-1]
    assert abs(windy[bc.T_WINDAGE]) > abs(calm[bc.T_WINDAGE]) + 0.1


def test_find_apex_and_integrate_at(shot):
    bc.zero(shot, 300 * M)
    apex = bc.find_apex(shot)
    assert apex[bc.T_DISTANCE] > 0
    raw, row = bc.integrate_at(shot, bc.INTERP_POS_X, 1000.0)
    assert raw[1] == pytest.approx(1000.0, abs=1e-6)
    assert row[bc.T_DISTANCE] == pytest.approx(1000.0, abs=1e-6)


def test_multibc_single_point_is_the_reference_table(shot):
    mach, cd, n = bc.MultiBC([(1.0, 1.0)])
    custom = bc.Shot(**_kwargs(shot, bc=1.0), drag_type=bc.DRAG_CUSTOM, drag_mach=mach, drag_cd=cd, drag_count=n)
    g7_bc1 = bc.Shot(**_kwargs(shot, bc=1.0))
    req = bc.Request(range_limit_ft=1000.0, range_step_ft=500.0)
    for a, b in zip(bc.fire(custom, req)[0], bc.fire(g7_bc1, req)[0]):
        assert a[bc.T_VELOCITY] == pytest.approx(b[bc.T_VELOCITY], rel=1e-6)


def test_errors_are_value_errors(shot):
    bad = bc.Shot(**_kwargs(shot), drag_type=bc.DRAG_CUSTOM, drag_mach=[1.0, 2.0], drag_cd=[0.3, 0.3], drag_count=1)
    # one-point custom drag table is too short for tiny_bclibc -> it would fall back to G7 here,
    # so instead provoke a real failure: an intercept that is never reached
    with pytest.raises(ValueError, match="integrate_at"):
        bc.integrate_at(bad, bc.INTERP_POS_X, 1e9)


def _kwargs(shot, **override):
    p = shot.s.props
    kw = {
        "bc": p.bc,
        "weight_grain": p.weight_grain,
        "diameter_inch": p.diameter_inch,
        "length_inch": p.length_inch,
        "muzzle_velocity_fps": p.muzzle_velocity_fps,
        "sight_height_ft": p.sight_height_ft,
        "twist_inch": p.twist_inch,
    }
    kw.update(override)
    return kw


def test_integrate_ex_returns_terminal_state(shot):
    bc.zero(shot, 100 * M)
    traj = bc.integrate_ex(shot, bc.Request(range_limit_ft=500 * M, range_step_ft=100 * M))
    rows, reason = bc.integrate(shot, bc.Request(range_limit_ft=500 * M, range_step_ft=100 * M))
    assert traj.rows == rows and traj.reason == reason
    assert traj.total == len(rows)
    # The last accepted adaptive step: at or just past the requested range, and no earlier than the last row.
    assert traj.final[1] >= 500 * M
    assert traj.final[0] >= traj.rows[-1][bc.T_TIME]


def test_precision_switch(shot):
    try:
        bc.set_precision("single")
        assert bc.precision() == "single" and bc.version().endswith("-sp")
        bc.set_precision("double")
        assert bc.version().endswith("-dp")
    finally:
        bc.set_precision("double")
    with pytest.raises(ValueError):
        bc.set_precision("half")


def test_bench_runs_every_loop(capsys):
    bc.bench()
    out = capsys.readouterr().out
    assert out.count("MFLOPS") == 6
    assert "wasm on " + bc.host() in out

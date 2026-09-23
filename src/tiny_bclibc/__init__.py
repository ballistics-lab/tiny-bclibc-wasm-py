"""tiny_bclibc -- micropython-bclibc's tiny_bclibc API for CPython, PyPy and Pythonista.

The same module a MicroPython board gets as a native .mpy (micropython-bclibc's natmod/usermod),
here backed by tiny_bclibc compiled to WebAssembly and run in whatever WebAssembly host is
available: JavaScriptCore's JSContext in Pythonista, `wasmtime` or Node on a desktop (see
`_runner.py`). A script written against the natmod runs here unchanged:

    import tiny_bclibc as bc

    shot = bc.Shot(bc=0.310, weight_grain=168.0, diameter_inch=0.308, length_inch=1.2,
                   muzzle_velocity_fps=2750.0, sight_height_ft=0.125, twist_inch=11.0)
    bc.zero(shot, 300 * 3.28084)
    rows, reason = bc.fire(shot, bc.Request(range_limit_ft=3000.0, range_step_ft=300.0))
    for r in rows:
        print(r[bc.T_DISTANCE], r[bc.T_HEIGHT])

Differences from the natmod, all at the storage level (results use the same C code):
    - Shot/Wind/Config/Request keep their fields as Python floats, not float32 bytearrays, so a
      double-precision module sees full-precision inputs. They are still `(buf, s)`-shaped
      namedtuples with the fields on `.s` (`shot.s.props.barrel_elevation_rad`, `w.s.velocity_fps`,
      `cfg.s.max_iterations`, ...); `buf` is None.
    - No bench() (a native FPU benchmark means nothing through a WebAssembly host).

Configuration (environment variables, read on first use):
    TINY_BCLIBC_PRECISION   double (default) | single -- which .wasm to load
    TINY_BCLIBC_WASM        explicit path to a .wasm (overrides the one next to this file)
    TINY_BCLIBC_HOST        jscontext | gi-jsc | node | wasmtime (default: first available,
                            see _runner.default_runner); set_host() does the same from code
"""

import os
import struct as _struct
from collections import namedtuple as _namedtuple

from . import _drag_tables
from ._runner import HOSTS, TbwError, WasmRunner, default_runner

__all__ = [
    "DRAG_CUSTOM",
    "DRAG_G1",
    "DRAG_G7",
    "INTERP_MACH",
    "INTERP_POS_X",
    "INTERP_POS_Y",
    "INTERP_POS_Z",
    "INTERP_TIME",
    "INTERP_VEL_X",
    "INTERP_VEL_Y",
    "INTERP_VEL_Z",
    "TRAJ_FLAG_ALL",
    "TRAJ_FLAG_APEX",
    "TRAJ_FLAG_MACH",
    "TRAJ_FLAG_MRT",
    "TRAJ_FLAG_NONE",
    "TRAJ_FLAG_RANGE",
    "TRAJ_FLAG_ZERO",
    "TRAJ_FLAG_ZERO_DOWN",
    "TRAJ_FLAG_ZERO_UP",
    "T_ANGLE",
    "T_DENSITY_RATIO",
    "T_DISTANCE",
    "T_DRAG",
    "T_DROP_ANGLE",
    "T_ENERGY",
    "T_FLAG",
    "T_HEIGHT",
    "T_MACH",
    "T_OGW",
    "T_SLANT_DISTANCE",
    "T_SLANT_HEIGHT",
    "T_TIME",
    "T_VELOCITY",
    "T_WINDAGE",
    "T_WINDAGE_ANGLE",
    "Config",
    "MultiBC",
    "Request",
    "Shot",
    "Wind",
    "aim",
    "build_multibc",
    "find_apex",
    "find_max_range",
    "find_zero_angle",
    "fire",
    "host",
    "integrate",
    "integrate_at",
    "integrate_stream",
    "set_host",
    "version",
    "zero",
    "zero_point",
]

# ── Constants (same values as natmod's _tiny_bclibc) ──────────────────────────
DRAG_G1 = 0
DRAG_G7 = 1
DRAG_CUSTOM = 2

TRAJ_FLAG_NONE = 0
TRAJ_FLAG_ZERO_UP = 1
TRAJ_FLAG_ZERO_DOWN = 2
TRAJ_FLAG_ZERO = 3
TRAJ_FLAG_MACH = 4
TRAJ_FLAG_RANGE = 8
TRAJ_FLAG_APEX = 16
TRAJ_FLAG_MRT = 32
TRAJ_FLAG_ALL = 31

T_TIME = 0
T_DISTANCE = 1
T_VELOCITY = 2
T_MACH = 3
T_HEIGHT = 4
T_SLANT_HEIGHT = 5
T_DROP_ANGLE = 6
T_WINDAGE = 7
T_WINDAGE_ANGLE = 8
T_SLANT_DISTANCE = 9
T_ANGLE = 10
T_DENSITY_RATIO = 11
T_DRAG = 12
T_ENERGY = 13
T_OGW = 14
T_FLAG = 15

INTERP_TIME = 0
INTERP_MACH = 1
INTERP_POS_X = 2
INTERP_POS_Y = 3
INTERP_POS_Z = 4
INTERP_VEL_X = 5
INTERP_VEL_Y = 6
INTERP_VEL_Z = 7

_NaN = float("nan")
_INF = 1e8  # TINY_BCLIBC_MAX_WIND_DIST_FT
_MAX_WINDS = 16
_MAX_DRAG_PTS = 200
_TERM_HANDLER_STOP = 5

# tiny_bclibc_wasm.c output layout
_ROW = 16
_INTEGRATE_HEADER = 12
_AT_HEADER = 9

# ── Module loading ────────────────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
_active = None  # the loaded WasmRunner (named so it can't shadow the _runner submodule)


def _get_runner():
    global _active
    if _active is None:
        single = os.environ.get("TINY_BCLIBC_PRECISION", "double").lower().startswith("s")
        path = os.environ.get("TINY_BCLIBC_WASM") or os.path.join(
            _HERE, "tiny_bclibc_sp.wasm" if single else "tiny_bclibc_dp.wasm"
        )
        if not os.path.isfile(path):
            raise FileNotFoundError(
                f"tiny_bclibc wasm module not found at '{path}'. Build it with `uv sync` or "
                "`python build_wasm.py` in the tiny-bclibc-wasm-py repo, or set "
                "TINY_BCLIBC_WASM."
            )
        runner = _pick_host()
        runner.load_file(path)
        _active = runner
    return _active


def _call(what, export, inputs, *args):
    try:
        return _get_runner().call(export, inputs, *args)
    except TbwError as exc:
        raise ValueError(f"{what} rc={exc.status}: {exc.message}") from None


_host_choice = None


def _pick_host():
    if _host_choice is None:
        return default_runner()
    if isinstance(_host_choice, WasmRunner):
        return _host_choice
    return HOSTS[_host_choice]()


def set_host(host):
    """Choose the WebAssembly host before (or instead of) the automatic pick.

    ``host`` is a name ("jscontext", "gi-jsc", "node", "wasmtime"), a ready WasmRunner instance, or
    None to go back to automatic selection. Takes effect on the next call (the module is reloaded).
    """
    global _host_choice, _active
    if host is not None and not isinstance(host, WasmRunner) and host not in HOSTS:
        raise ValueError("unknown host {!r}: expected one of {}".format(host, ", ".join(HOSTS)))
    _host_choice = host
    _active = None


def version():
    """tiny_bclibc version of the loaded module, e.g. "2.0.0-rc.1-dp" (natmod: "<ver>-sp")."""
    return _get_runner().version


def host():
    """Name of the WebAssembly host in use: jscontext, gi-jsc, node or wasmtime."""
    return _get_runner().name


# ── Value objects ─────────────────────────────────────────────────────────────


class _Fields:
    """Attribute bag standing in for natmod's uctypes struct views (`.s`)."""

    def __init__(self, **kw):
        self.__dict__.update(kw)

    def __repr__(self):
        return "{}({})".format(type(self).__name__, ", ".join(f"{k}={v!r}" for k, v in self.__dict__.items()))


_Wind = _namedtuple("Wind", ("buf", "s"))
_Config = _namedtuple("Config", ("buf", "s"))
_Shot = _namedtuple("Shot", ("buf", "s", "holder"))
_Request = _namedtuple("Request", ("buf", "s", "traj"))


def Wind(velocity_fps=0.0, direction_from_rad=0.0, until_distance_ft=_INF, max_distance_ft=_INF):
    return _Wind(
        None,
        _Fields(
            velocity_fps=float(velocity_fps),
            direction_from_rad=float(direction_from_rad),
            until_distance_ft=float(until_distance_ft),
            max_distance_ft=float(max_distance_ft),
        ),
    )


def Config(
    step_multiplier=0.5,
    zero_finding_accuracy=0.001,
    minimum_velocity=50.0,
    maximum_drop=-15000.0,
    max_iterations=50,
    gravity_constant=-32.17405,
    minimum_altitude=-1500.0,
):
    return _Config(
        None,
        _Fields(
            step_multiplier=float(step_multiplier),
            zero_finding_accuracy=float(zero_finding_accuracy),
            minimum_velocity=float(minimum_velocity),
            maximum_drop=float(maximum_drop),
            max_iterations=int(max_iterations),
            gravity_constant=float(gravity_constant),
            minimum_altitude=float(minimum_altitude),
        ),
    )


def _floats(seq, count):
    """A drag column: a packed float32 buffer (what MultiBC() returns) or a plain sequence."""
    if isinstance(seq, (bytes, bytearray, memoryview)):
        return list(_struct.unpack_from(f"<{count}f", seq))
    return [float(seq[i]) for i in range(count)]


def Shot(
    bc=0.0,
    weight_grain=0.0,
    diameter_inch=0.0,
    length_inch=0.0,
    muzzle_velocity_fps=0.0,
    sight_height_ft=0.0,
    twist_inch=0.0,
    temp_c=15.0,
    pressure_hpa=1013.25,
    altitude_ft=0.0,
    humidity=0.5,
    look_angle_rad=0.0,
    barrel_elevation_rad=0.0,
    barrel_azimuth_rad=0.0,
    cant_angle_rad=0.0,
    latitude_deg=_NaN,
    azimuth_deg=_NaN,
    drag_type=DRAG_G7,
    drag_mach=None,
    drag_cd=None,
    drag_count=None,
    winds=None,
    config=None,
):
    cfg = config if config is not None else Config()
    winds = list(winds or [])[:_MAX_WINDS]
    mach = cd = None
    if drag_type == DRAG_CUSTOM and drag_mach and drag_cd:
        if drag_count is not None:
            dc = drag_count
        elif isinstance(drag_mach, (bytes, bytearray, memoryview)):
            dc = min(len(drag_mach) // 4, len(drag_cd) // 4)
        else:
            dc = min(len(drag_mach), len(drag_cd))
        dc = min(dc, _MAX_DRAG_PTS)
        mach, cd = _floats(drag_mach, dc), _floats(drag_cd, dc)
    props = _Fields(
        bc=float(bc),
        weight_grain=float(weight_grain),
        diameter_inch=float(diameter_inch),
        length_inch=float(length_inch),
        muzzle_velocity_fps=float(muzzle_velocity_fps),
        sight_height_ft=float(sight_height_ft),
        twist_inch=float(twist_inch),
        temp_c=float(temp_c),
        pressure_hpa=float(pressure_hpa),
        altitude_ft=float(altitude_ft),
        humidity=float(humidity),
        look_angle_rad=float(look_angle_rad),
        barrel_elevation_rad=float(barrel_elevation_rad),
        barrel_azimuth_rad=float(barrel_azimuth_rad),
        cant_angle_rad=float(cant_angle_rad),
        latitude_deg=float(latitude_deg),
        azimuth_deg=float(azimuth_deg),
    )
    s = _Fields(
        props=props,
        cfg=cfg.s,
        drag_type=drag_type,
        wind_count=len(winds),
        drag_count=len(mach) if mach else 0,
        winds=[w.s for w in winds],
        drag_mach=mach,
        drag_cd=cd,
    )
    return _Shot(None, s, None)


def Request(range_limit_ft=3000.0, range_step_ft=100.0, time_step=0.0, filter_flags=TRAJ_FLAG_RANGE):
    return _Request(
        None,
        _Fields(
            range_limit_ft=float(range_limit_ft),
            range_step_ft=float(range_step_ft),
            time_step=float(time_step),
            filter_flags=int(filter_flags),
        ),
        None,
    )


def _serialize(shot):
    """Flatten a Shot into tiny_bclibc_wasm.c's input layout."""
    s = shot.s
    p = s.props
    c = s.cfg
    if s.drag_type == DRAG_CUSTOM and s.drag_mach:
        mach, cd = s.drag_mach, s.drag_cd
    elif s.drag_type == DRAG_G1:
        mach, cd = _drag_tables.G1_MACH, _drag_tables.G1_CD
    else:
        mach, cd = _drag_tables.G7_MACH, _drag_tables.G7_CD
    values = [
        p.bc,
        p.weight_grain,
        p.diameter_inch,
        p.length_inch,
        p.muzzle_velocity_fps,
        p.sight_height_ft,
        p.twist_inch,
        p.temp_c,
        p.pressure_hpa,
        p.altitude_ft,
        p.humidity,
        p.look_angle_rad,
        p.barrel_elevation_rad,
        p.barrel_azimuth_rad,
        p.cant_angle_rad,
        p.latitude_deg,
        p.azimuth_deg,
        c.step_multiplier,
        c.zero_finding_accuracy,
        c.minimum_velocity,
        c.maximum_drop,
        c.max_iterations,
        c.gravity_constant,
        c.minimum_altitude,
        len(mach),
        len(s.winds),
    ]
    values.extend(mach)
    values.extend(cd)
    for w in s.winds:
        values.extend((w.velocity_fps, w.direction_from_rad, w.until_distance_ft, w.max_distance_ft))
    return values


def _row(v, i):
    """One TrajectoryData row as natmod's 16-tuple (15 floats + int flag)."""
    r = v[i : i + _ROW]
    r[15] = int(r[15])
    return tuple(r)


# ── API ───────────────────────────────────────────────────────────────────────


def integrate(shot, req):
    """Return ``(rows, stop_reason)``; each row is a 16-tuple indexed by the ``T_*`` constants."""
    r = req.s
    out = _call(
        "integrate",
        "tbw_integrate",
        _serialize(shot),
        r.range_limit_ft,
        r.range_step_ft,
        r.time_step,
        r.filter_flags,
    )
    n = int(out[3])
    return [_row(out, _INTEGRATE_HEADER + k * _ROW) for k in range(n)], int(out[1])


def integrate_stream(shot, req, cb):
    """Call ``cb(row)`` per row; a truthy return stops early. Returns ``(count, stop_reason)``.

    The module computes the whole trajectory in one call and the callbacks run afterwards (one
    host round trip instead of one per row); a stop therefore reports reason 5 (handler stop)
    exactly like the natmod, but does not save the remaining integration work.
    """
    rows, reason = integrate(shot, req)
    for i, row in enumerate(rows):
        if cb(row):
            return i + 1, _TERM_HANDLER_STOP
    return len(rows), reason


def integrate_at(shot, interp, val):
    """Return ``(raw, row)`` where the ``INTERP_*`` quantity equals val.

    ``raw`` is (time, px, py, pz, vx, vy, vz, mach); ``row`` a 16-tuple.
    """
    out = _call("integrate_at", "tbw_integrate_at", _serialize(shot), int(interp), float(val))
    return tuple(out[1:_AT_HEADER]), _row(out, _AT_HEADER)


def find_zero_angle(shot, dist_ft):
    """Barrel elevation (rad) that zeroes the shot at dist_ft."""
    return _call("find_zero_angle", "tbw_find_zero_angle", _serialize(shot), float(dist_ft))[1]


def zero_point(shot, dist_ft):
    """Return the solver's ``(zero_angle_rad, terminal_row)`` without re-integration."""
    out = _call("zero_point", "tbw_find_zero_point", _serialize(shot), float(dist_ft))
    return out[1], _row(out, 2)


def zero(shot, dist_ft):
    """Set ``shot``'s barrel elevation for dist_ft and return it in radians."""
    angle, _point = zero_point(shot, dist_ft)
    shot.s.props.barrel_elevation_rad = angle
    return angle


def aim(shot, dist_ft):
    """Return ``(vertical_hold_rad, windage_rad, point)`` for a target distance.

    The hold is relative to the barrel elevation currently stored in ``shot`` (normally set by
    :func:`zero`).
    """
    angle, point = zero_point(shot, dist_ft)
    return angle - shot.s.props.barrel_elevation_rad, point[T_WINDAGE_ANGLE], point


def fire(shot, req):
    """Calculate and return ``(trajectory_rows, stop_reason)``."""
    return integrate(shot, req)


def find_apex(shot):
    """The apex (vertical velocity = 0) as a 16-tuple row."""
    return _row(_call("find_apex", "tbw_find_apex", _serialize(shot)), 1)


def find_max_range(shot, lo, hi):
    """Return ``(max_range_ft, angle_rad)`` searched between lo and hi degrees."""
    out = _call("find_max_range", "tbw_find_max_range", _serialize(shot), float(lo), float(hi))
    return out[1], out[2]


# ── MultiBC ───────────────────────────────────────────────────────────────────
# Same algorithm as natmod's build_multibc (src/tiny_bclibc_mp.c) and ffimod's pure-Python port.


def _interp_bc(bc_mach, bc_val, mach):
    n = len(bc_mach)
    if mach <= bc_mach[0]:
        return bc_val[0]
    if mach >= bc_mach[n - 1]:
        return bc_val[n - 1]
    lo, hi = 0, n - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if bc_mach[mid] <= mach:
            lo = mid
        else:
            hi = mid
    t = (mach - bc_mach[lo]) / (bc_mach[hi] - bc_mach[lo])
    return bc_val[lo] + t * (bc_val[hi] - bc_val[lo])


def build_multibc(drag_type, bc_points_buf, out_mach_buf, out_cd_buf):
    """Low-level primitive with natmod's signature: packed "<ff" (mach, bc) points in, float32 out."""
    n_pts = len(bc_points_buf) // 8
    pts = sorted(
        (_struct.unpack_from("<ff", bc_points_buf, i * 8) for i in range(n_pts)),
        key=lambda p: p[0],
    )
    bc_mach = [p[0] for p in pts]
    bc_val = [p[1] for p in pts]
    # natmod: 0 = G1, anything else = G7
    if drag_type == DRAG_G1:
        ref_mach, ref_cd = _drag_tables.G1_MACH, _drag_tables.G1_CD
    else:
        ref_mach, ref_cd = _drag_tables.G7_MACH, _drag_tables.G7_CD
    n = len(ref_mach)
    for i in range(n):
        bc_at = _interp_bc(bc_mach, bc_val, ref_mach[i])
        _struct.pack_into("<f", out_mach_buf, i * 4, ref_mach[i])
        _struct.pack_into("<f", out_cd_buf, i * 4, ref_cd[i] / bc_at)
    return n


def MultiBC(bc_points, drag_type=DRAG_G7):
    """Fold (mach, bc) points into one custom drag curve: returns ``(mach_buf, cd_buf, count)``.

    Feed it to ``Shot(bc=1.0, drag_type=DRAG_CUSTOM, drag_mach=mach_buf, drag_cd=cd_buf,
    drag_count=count)`` -- same contract as the natmod's MultiBC().
    """
    pts = list(bc_points)
    pts_buf = bytearray(len(pts) * 8)
    for i, (mach, bc_val) in enumerate(pts):
        _struct.pack_into("<ff", pts_buf, i * 8, mach, bc_val)
    mach_buf = bytearray(_MAX_DRAG_PTS * 4)
    cd_buf = bytearray(_MAX_DRAG_PTS * 4)
    count = build_multibc(drag_type, pts_buf, mach_buf, cd_buf)
    return mach_buf, cd_buf, count

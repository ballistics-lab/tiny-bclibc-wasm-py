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
      named tuples with the fields on `.s` (`shot.s.props.barrel_elevation_rad`, `w.s.velocity_fps`,
      `cfg.s.max_iterations`, ...); `buf` is None.
    - No bench() (a native FPU benchmark means nothing through a WebAssembly host).

Typing: fully annotated (Python 3.10 syntax, which Pythonista runs); the public surface is also
described by __init__.pyi, checked against this module with mypy's stubtest.

Configuration (environment variables, read on first use):
    TINY_BCLIBC_PRECISION   double (default) | single -- which .wasm to load
    TINY_BCLIBC_WASM        explicit path to a .wasm (overrides the one next to this file)
    TINY_BCLIBC_HOST        jscontext | gi-jsc | node | wasmtime (default: first available,
                            see _runner.default_runner); set_host() does the same from code
"""

import os
import struct as _struct
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Final, NamedTuple, TypeAlias

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
DRAG_G1: Final = 0
DRAG_G7: Final = 1
DRAG_CUSTOM: Final = 2

TRAJ_FLAG_NONE: Final = 0
TRAJ_FLAG_ZERO_UP: Final = 1
TRAJ_FLAG_ZERO_DOWN: Final = 2
TRAJ_FLAG_ZERO: Final = 3
TRAJ_FLAG_MACH: Final = 4
TRAJ_FLAG_RANGE: Final = 8
TRAJ_FLAG_APEX: Final = 16
TRAJ_FLAG_MRT: Final = 32
TRAJ_FLAG_ALL: Final = 31

T_TIME: Final = 0
T_DISTANCE: Final = 1
T_VELOCITY: Final = 2
T_MACH: Final = 3
T_HEIGHT: Final = 4
T_SLANT_HEIGHT: Final = 5
T_DROP_ANGLE: Final = 6
T_WINDAGE: Final = 7
T_WINDAGE_ANGLE: Final = 8
T_SLANT_DISTANCE: Final = 9
T_ANGLE: Final = 10
T_DENSITY_RATIO: Final = 11
T_DRAG: Final = 12
T_ENERGY: Final = 13
T_OGW: Final = 14
T_FLAG: Final = 15

INTERP_TIME: Final = 0
INTERP_MACH: Final = 1
INTERP_POS_X: Final = 2
INTERP_POS_Y: Final = 3
INTERP_POS_Z: Final = 4
INTERP_VEL_X: Final = 5
INTERP_VEL_Y: Final = 6
INTERP_VEL_Z: Final = 7

_NaN: Final = float("nan")
_INF: Final = 1e8  # TINY_BCLIBC_MAX_WIND_DIST_FT
_MAX_WINDS: Final = 16
_MAX_DRAG_PTS: Final = 200
_TERM_HANDLER_STOP: Final = 5

# tiny_bclibc_wasm.c output layout
_ROW: Final = 16
_INTEGRATE_HEADER: Final = 12
_AT_HEADER: Final = 9

# ── Types ─────────────────────────────────────────────────────────────────────

# One TrajectoryData row, indexed by the T_* constants: 15 floats, then the int TRAJ_FLAG_* flag.
Row: TypeAlias = tuple[
    float, float, float, float, float, float, float, float, float, float, float, float, float, float, float, int
]
# BaseTrajData: (time, px, py, pz, vx, vy, vz, mach).
RawState: TypeAlias = tuple[float, float, float, float, float, float, float, float]
# A custom drag column: a packed float32 buffer (what MultiBC() returns) or a plain sequence.
DragColumn: TypeAlias = Sequence[float] | bytes | bytearray | memoryview


# ── Module loading ────────────────────────────────────────────────────────────
_HERE: Final = os.path.dirname(os.path.abspath(__file__))
_active: WasmRunner | None = None  # the loaded host (named so it can't shadow the _runner submodule)
_host_choice: str | WasmRunner | None = None


def _get_runner() -> WasmRunner:
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


def _call(what: str, export: str, inputs: Sequence[float], *args: float) -> list[float]:
    try:
        return _get_runner().call(export, inputs, *args)
    except TbwError as exc:
        raise ValueError(f"{what} rc={exc.status}: {exc.message}") from None


def _pick_host() -> WasmRunner:
    if _host_choice is None:
        return default_runner()
    if isinstance(_host_choice, WasmRunner):
        return _host_choice
    return HOSTS[_host_choice]()


def set_host(host: str | WasmRunner | None) -> None:
    """Choose the WebAssembly host before (or instead of) the automatic pick.

    ``host`` is a name ("jscontext", "gi-jsc", "node", "wasmtime"), a ready WasmRunner instance, or
    None to go back to automatic selection. Takes effect on the next call (the module is reloaded).
    """
    global _host_choice, _active
    if host is not None and not isinstance(host, WasmRunner) and host not in HOSTS:
        raise ValueError("unknown host {!r}: expected one of {}".format(host, ", ".join(HOSTS)))
    _host_choice = host
    _active = None


def version() -> str:
    """tiny_bclibc version of the loaded module, e.g. "2.0.0-rc.1-dp" (natmod: "<ver>-sp")."""
    return _get_runner().version


def host() -> str:
    """Name of the WebAssembly host in use: jscontext, gi-jsc, node or wasmtime."""
    return _get_runner().name


# ── Value objects ─────────────────────────────────────────────────────────────
# The `.s` side of natmod's `(buf, s)` pairs: there a uctypes struct view over a float32 buffer,
# here a plain typed record with the same field names.


@dataclass(slots=True)
class WindFields:
    velocity_fps: float
    direction_from_rad: float
    until_distance_ft: float
    max_distance_ft: float


@dataclass(slots=True)
class ConfigFields:
    step_multiplier: float
    zero_finding_accuracy: float
    minimum_velocity: float
    maximum_drop: float
    max_iterations: int
    gravity_constant: float
    minimum_altitude: float


@dataclass(slots=True)
class ShotProps:
    bc: float
    weight_grain: float
    diameter_inch: float
    length_inch: float
    muzzle_velocity_fps: float
    sight_height_ft: float
    twist_inch: float
    temp_c: float
    pressure_hpa: float
    altitude_ft: float
    humidity: float
    look_angle_rad: float
    barrel_elevation_rad: float
    barrel_azimuth_rad: float
    cant_angle_rad: float
    latitude_deg: float
    azimuth_deg: float


@dataclass(slots=True)
class ShotFields:
    props: ShotProps
    cfg: ConfigFields
    drag_type: int
    winds: list[WindFields]
    drag_mach: list[float] | None  # custom drag table (DRAG_CUSTOM), else None
    drag_cd: list[float] | None

    @property
    def wind_count(self) -> int:
        return len(self.winds)

    @property
    def drag_count(self) -> int:
        return len(self.drag_mach) if self.drag_mach else 0


@dataclass(slots=True)
class RequestFields:
    range_limit_ft: float
    range_step_ft: float
    time_step: float
    filter_flags: int


class WindData(NamedTuple):
    """What Wind() returns (natmod's `Wind` namedtuple)."""

    buf: None
    s: WindFields


class ConfigData(NamedTuple):
    """What Config() returns (natmod's `Config` namedtuple)."""

    buf: None
    s: ConfigFields


class ShotData(NamedTuple):
    """What Shot() returns (natmod's `Shot` namedtuple)."""

    buf: None
    s: ShotFields
    holder: None


class RequestData(NamedTuple):
    """What Request() returns (natmod's `Request` namedtuple)."""

    buf: None
    s: RequestFields
    traj: None


def Wind(
    velocity_fps: float = 0.0,
    direction_from_rad: float = 0.0,
    until_distance_ft: float = _INF,
    max_distance_ft: float = _INF,
) -> WindData:
    return WindData(
        None,
        WindFields(
            velocity_fps=float(velocity_fps),
            direction_from_rad=float(direction_from_rad),
            until_distance_ft=float(until_distance_ft),
            max_distance_ft=float(max_distance_ft),
        ),
    )


def Config(
    step_multiplier: float = 0.5,
    zero_finding_accuracy: float = 0.001,
    minimum_velocity: float = 50.0,
    maximum_drop: float = -15000.0,
    max_iterations: int = 50,
    gravity_constant: float = -32.17405,
    minimum_altitude: float = -1500.0,
) -> ConfigData:
    return ConfigData(
        None,
        ConfigFields(
            step_multiplier=float(step_multiplier),
            zero_finding_accuracy=float(zero_finding_accuracy),
            minimum_velocity=float(minimum_velocity),
            maximum_drop=float(maximum_drop),
            max_iterations=int(max_iterations),
            gravity_constant=float(gravity_constant),
            minimum_altitude=float(minimum_altitude),
        ),
    )


def _floats(column: DragColumn, count: int) -> list[float]:
    if isinstance(column, (bytes, bytearray, memoryview)):
        return list(_struct.unpack_from(f"<{count}f", column))
    return [float(column[i]) for i in range(count)]


def Shot(
    bc: float = 0.0,
    weight_grain: float = 0.0,
    diameter_inch: float = 0.0,
    length_inch: float = 0.0,
    muzzle_velocity_fps: float = 0.0,
    sight_height_ft: float = 0.0,
    twist_inch: float = 0.0,
    temp_c: float = 15.0,
    pressure_hpa: float = 1013.25,
    altitude_ft: float = 0.0,
    humidity: float = 0.5,
    look_angle_rad: float = 0.0,
    barrel_elevation_rad: float = 0.0,
    barrel_azimuth_rad: float = 0.0,
    cant_angle_rad: float = 0.0,
    latitude_deg: float = _NaN,
    azimuth_deg: float = _NaN,
    drag_type: int = DRAG_G7,
    drag_mach: DragColumn | None = None,
    drag_cd: DragColumn | None = None,
    drag_count: int | None = None,
    winds: Iterable[WindData] | None = None,
    config: ConfigData | None = None,
) -> ShotData:
    cfg = config if config is not None else Config()
    wind_list = list(winds or [])[:_MAX_WINDS]
    mach: list[float] | None = None
    cd: list[float] | None = None
    if drag_type == DRAG_CUSTOM and drag_mach and drag_cd:
        if drag_count is not None:
            dc = drag_count
        elif isinstance(drag_mach, (bytes, bytearray, memoryview)):
            dc = min(len(drag_mach) // 4, len(drag_cd) // 4)
        else:
            dc = min(len(drag_mach), len(drag_cd))
        dc = min(dc, _MAX_DRAG_PTS)
        mach, cd = _floats(drag_mach, dc), _floats(drag_cd, dc)
    props = ShotProps(
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
    s = ShotFields(
        props=props,
        cfg=cfg.s,
        drag_type=drag_type,
        winds=[w.s for w in wind_list],
        drag_mach=mach,
        drag_cd=cd,
    )
    return ShotData(None, s, None)


def Request(
    range_limit_ft: float = 3000.0,
    range_step_ft: float = 100.0,
    time_step: float = 0.0,
    filter_flags: int = TRAJ_FLAG_RANGE,
) -> RequestData:
    return RequestData(
        None,
        RequestFields(
            range_limit_ft=float(range_limit_ft),
            range_step_ft=float(range_step_ft),
            time_step=float(time_step),
            filter_flags=int(filter_flags),
        ),
        None,
    )


def _serialize(shot: ShotData) -> list[float]:
    """Flatten a Shot into tiny_bclibc_wasm.c's input layout."""
    s = shot.s
    p = s.props
    c = s.cfg
    mach: Sequence[float]
    cd: Sequence[float]
    if s.drag_type == DRAG_CUSTOM and s.drag_mach and s.drag_cd:
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
        float(c.max_iterations),
        c.gravity_constant,
        c.minimum_altitude,
        float(len(mach)),
        float(len(s.winds)),
    ]
    values.extend(mach)
    values.extend(cd)
    for w in s.winds:
        values.extend((w.velocity_fps, w.direction_from_rad, w.until_distance_ft, w.max_distance_ft))
    return values


def _row(v: Sequence[float], i: int) -> Row:
    """One TrajectoryData row as natmod's 16-tuple (15 floats + int flag)."""
    return (
        v[i],
        v[i + 1],
        v[i + 2],
        v[i + 3],
        v[i + 4],
        v[i + 5],
        v[i + 6],
        v[i + 7],
        v[i + 8],
        v[i + 9],
        v[i + 10],
        v[i + 11],
        v[i + 12],
        v[i + 13],
        v[i + 14],
        int(v[i + 15]),
    )


# ── API ───────────────────────────────────────────────────────────────────────


def integrate(shot: ShotData, req: RequestData) -> tuple[list[Row], int]:
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


def integrate_stream(shot: ShotData, req: RequestData, cb: Callable[[Row], object]) -> tuple[int, int]:
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


def integrate_at(shot: ShotData, interp: int, val: float) -> tuple[RawState, Row]:
    """Return ``(raw, row)`` where the ``INTERP_*`` quantity equals val.

    ``raw`` is (time, px, py, pz, vx, vy, vz, mach); ``row`` a 16-tuple.
    """
    o = _call("integrate_at", "tbw_integrate_at", _serialize(shot), int(interp), float(val))
    raw: RawState = (o[1], o[2], o[3], o[4], o[5], o[6], o[7], o[8])
    return raw, _row(o, _AT_HEADER)


def find_zero_angle(shot: ShotData, dist_ft: float) -> float:
    """Barrel elevation (rad) that zeroes the shot at dist_ft."""
    return _call("find_zero_angle", "tbw_find_zero_angle", _serialize(shot), float(dist_ft))[1]


def zero_point(shot: ShotData, dist_ft: float) -> tuple[float, Row]:
    """Return the solver's ``(zero_angle_rad, terminal_row)`` without re-integration."""
    out = _call("zero_point", "tbw_find_zero_point", _serialize(shot), float(dist_ft))
    return out[1], _row(out, 2)


def zero(shot: ShotData, dist_ft: float) -> float:
    """Set ``shot``'s barrel elevation for dist_ft and return it in radians."""
    angle, _point = zero_point(shot, dist_ft)
    shot.s.props.barrel_elevation_rad = angle
    return angle


def aim(shot: ShotData, dist_ft: float) -> tuple[float, float, Row]:
    """Return ``(vertical_hold_rad, windage_rad, point)`` for a target distance.

    The hold is relative to the barrel elevation currently stored in ``shot`` (normally set by
    :func:`zero`).
    """
    angle, point = zero_point(shot, dist_ft)
    return angle - shot.s.props.barrel_elevation_rad, point[T_WINDAGE_ANGLE], point


def fire(shot: ShotData, req: RequestData) -> tuple[list[Row], int]:
    """Calculate and return ``(trajectory_rows, stop_reason)``."""
    return integrate(shot, req)


def find_apex(shot: ShotData) -> Row:
    """The apex (vertical velocity = 0) as a 16-tuple row."""
    return _row(_call("find_apex", "tbw_find_apex", _serialize(shot)), 1)


def find_max_range(shot: ShotData, lo: float, hi: float) -> tuple[float, float]:
    """Return ``(max_range_ft, angle_rad)`` searched between lo and hi degrees."""
    out = _call("find_max_range", "tbw_find_max_range", _serialize(shot), float(lo), float(hi))
    return out[1], out[2]


# ── MultiBC ───────────────────────────────────────────────────────────────────
# Same algorithm as natmod's build_multibc (src/tiny_bclibc_mp.c) and ffimod's pure-Python port.


def _interp_bc(bc_mach: Sequence[float], bc_val: Sequence[float], mach: float) -> float:
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


def build_multibc(
    drag_type: int,
    bc_points_buf: bytes | bytearray | memoryview,
    out_mach_buf: bytearray | memoryview,
    out_cd_buf: bytearray | memoryview,
) -> int:
    """Low-level primitive with natmod's signature: packed "<ff" (mach, bc) points in, float32 out."""
    n_pts = len(bc_points_buf) // 8
    pts: list[tuple[float, float]] = sorted(
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


def MultiBC(bc_points: Iterable[tuple[float, float]], drag_type: int = DRAG_G7) -> tuple[bytearray, bytearray, int]:
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

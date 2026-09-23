"""Public API of tiny_bclibc (natmod-compatible). Checked against the module by mypy's stubtest."""

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Final, NamedTuple, TypeAlias

from ._runner import WasmRunner

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
    "bench",
    "build_multibc",
    "find_apex",
    "find_max_range",
    "find_zero_angle",
    "fire",
    "host",
    "integrate",
    "integrate_at",
    "integrate_ex",
    "integrate_stream",
    "precision",
    "set_host",
    "set_precision",
    "version",
    "zero",
    "zero_point",
]

# ── Constants ──────────────────────────────────────────────────────────────────
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

# ── Types ──────────────────────────────────────────────────────────────────────

# One TrajectoryData row, indexed by the T_* constants: 15 floats, then the int TRAJ_FLAG_* flag.
Row: TypeAlias = tuple[
    float, float, float, float, float, float, float, float, float, float, float, float, float, float, float, int
]
# BaseTrajData: (time, px, py, pz, vx, vy, vz, mach).
RawState: TypeAlias = tuple[float, float, float, float, float, float, float, float]
# A custom drag column: a packed float32 buffer (what MultiBC() returns) or a plain sequence.
DragColumn: TypeAlias = Sequence[float] | bytes | bytearray | memoryview

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
    drag_mach: list[float] | None
    drag_cd: list[float] | None
    @property
    def wind_count(self) -> int: ...
    @property
    def drag_count(self) -> int: ...

@dataclass(slots=True)
class RequestFields:
    range_limit_ft: float
    range_step_ft: float
    time_step: float
    filter_flags: int

class WindData(NamedTuple):
    buf: None
    s: WindFields

class ConfigData(NamedTuple):
    buf: None
    s: ConfigFields

class ShotData(NamedTuple):
    buf: None
    s: ShotFields
    holder: None

class RequestData(NamedTuple):
    buf: None
    s: RequestFields
    traj: None

# ── Host ───────────────────────────────────────────────────────────────────────
def set_host(host: str | WasmRunner | None) -> None: ...
def set_precision(precision: str) -> None: ...
def precision() -> str: ...
def host() -> str: ...
def version() -> str: ...

# ── Value factories ────────────────────────────────────────────────────────────
def Wind(
    velocity_fps: float = 0.0,
    direction_from_rad: float = 0.0,
    until_distance_ft: float = ...,
    max_distance_ft: float = ...,
) -> WindData: ...
def Config(
    step_multiplier: float = 0.5,
    zero_finding_accuracy: float = 0.001,
    minimum_velocity: float = 50.0,
    maximum_drop: float = -15000.0,
    max_iterations: int = 50,
    gravity_constant: float = -32.17405,
    minimum_altitude: float = -1500.0,
) -> ConfigData: ...
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
    latitude_deg: float = ...,
    azimuth_deg: float = ...,
    drag_type: int = 1,
    drag_mach: DragColumn | None = None,
    drag_cd: DragColumn | None = None,
    drag_count: int | None = None,
    winds: Iterable[WindData] | None = None,
    config: ConfigData | None = None,
) -> ShotData: ...
def Request(
    range_limit_ft: float = 3000.0,
    range_step_ft: float = 100.0,
    time_step: float = 0.0,
    filter_flags: int = 8,
) -> RequestData: ...

class Trajectory(NamedTuple):
    rows: list[Row]
    reason: int
    total: int
    final: RawState

# ── Solvers ────────────────────────────────────────────────────────────────────
def integrate(shot: ShotData, req: RequestData) -> tuple[list[Row], int]: ...
def integrate_ex(shot: ShotData, req: RequestData) -> Trajectory: ...
def integrate_stream(shot: ShotData, req: RequestData, cb: Callable[[Row], object]) -> tuple[int, int]: ...
def integrate_at(shot: ShotData, interp: int, val: float) -> tuple[RawState, Row]: ...
def find_zero_angle(shot: ShotData, dist_ft: float) -> float: ...
def zero_point(shot: ShotData, dist_ft: float) -> tuple[float, Row]: ...
def zero(shot: ShotData, dist_ft: float) -> float: ...
def aim(shot: ShotData, dist_ft: float) -> tuple[float, float, Row]: ...
def fire(shot: ShotData, req: RequestData) -> tuple[list[Row], int]: ...
def find_apex(shot: ShotData) -> Row: ...
def find_max_range(shot: ShotData, lo: float, hi: float) -> tuple[float, float]: ...

# ── MultiBC ────────────────────────────────────────────────────────────────────
def build_multibc(
    drag_type: int,
    bc_points_buf: bytes | bytearray | memoryview,
    out_mach_buf: bytearray | memoryview,
    out_cd_buf: bytearray | memoryview,
) -> int: ...
def MultiBC(bc_points: Iterable[tuple[float, float]], drag_type: int = 1) -> tuple[bytearray, bytearray, int]: ...

# ── Benchmark ──────────────────────────────────────────────────────────────────
def bench() -> None: ...

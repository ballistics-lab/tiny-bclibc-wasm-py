"""py-ballisticcalc integration engines backed by tiny_bclibc (as WebAssembly).

An adapter only: it turns py_ballisticcalc's `ShotProps` into a `tiny_bclibc.Shot`, calls
`integrate_ex` / `zero_point`, and turns the rows back into `TrajectoryData`. Needs
py-ballisticcalc >= 3.0.0b1 (`pip install tiny-bclibc-wasm[pybc]`); importing `tiny_bclibc` itself
never imports this module.

Engines, registered as py-ballisticcalc entry points (see pyproject.toml):
    TinyBclibcWasmTsitourasEngineDP: real_t = double.
        "tiny_bclibc_wasm+tsitouras-dp" (py-ballisticcalc >= 3.0.0b3), "tiny_bclibc_wasm_engine" (legacy name)
    TinyBclibcWasmTsitourasEngineSP: real_t = float, with a looser default `cZeroFindingAccuracy`
        (1e-3 ft) and float32's limits (18 of py-ballisticcalc's tests fail on it).
        "tiny_bclibc_wasm+tsitouras-sp", "tiny_bclibc_wasm_sp_engine" (legacy name)
    Both can live in one process: each call selects its own precision (`tiny_bclibc.set_precision`).

    from py_ballisticcalc import Calculator
    calc = Calculator(engine="tiny_bclibc_wasm+tsitouras-dp")

The engine runs on whichever WebAssembly host the package picks (`TINY_BCLIBC_HOST`,
`tiny_bclibc.set_host`). In Pythonista, which has no entry points, name it by import path:
`Calculator(engine="tiny_bclibc.pybc:TinyBclibcWasmTsitourasEngineDP")` -- see examples/py_ballisticcalc_engine.py.

Tested against py-ballisticcalc's own suite (the `py-ballisticcalc` submodule):
    uv run pytest py-ballisticcalc/tests --engine=tiny_bclibc_wasm+tsitouras-dp

Returns the same results as py-ballisticcalc's `examples/tiny_bclibc_ctypes` engines (tiny_bclibc as a
native library through ctypes): same tiny_bclibc calls, same Python-side row sorting/finalizing.

dense_output is NOT supported (raises NotImplementedError): the module only ever returns
filtered/interpolated rows, never raw per-step data.
"""

# TrajectoryData._new_* build unit objects without unit conversion -- the fast path py-ballisticcalc's own
# engines take for every row; they are protected members of TrajectoryData.
# pyright: reportPrivateUsage=false

from __future__ import annotations

import math
from typing import Any

from py_ballisticcalc.engines.base_engine import BaseEngineConfigDict, BaseIntegrationEngine
from py_ballisticcalc.exceptions import RangeError, SolverRuntimeError
from py_ballisticcalc.logger import logger
from py_ballisticcalc.shot import Shot, ShotProps
from py_ballisticcalc.trajectory_data import HitResult, TrajectoryData, TrajFlag
from py_ballisticcalc.unit import Angular, Distance, Pressure, Temperature, Velocity
from py_ballisticcalc.vector import Vector
from typing_extensions import override

import tiny_bclibc as tb

__all__ = ("TinyBclibcWasmTsitourasEngineDP", "TinyBclibcWasmTsitourasEngineSP")

# tiny_bclibc/include/tiny_bclibc/base_types.h: TINY_BCLIBC_TerminationReason
_TERM_NO_TERMINATE = 0
_TERM_TARGET_RANGE_REACHED = 1
_TERM_REASON_MAP = {
    2: RangeError.MinimumVelocityReached,
    3: RangeError.MaximumDropReached,
    4: RangeError.MinimumAltitudeReached,
}


def _row_to_trajectory_data(r: tb.Row) -> TrajectoryData:
    return TrajectoryData(
        time=r[tb.T_TIME],
        distance=TrajectoryData._new_feet(r[tb.T_DISTANCE]),
        velocity=TrajectoryData._new_fps(r[tb.T_VELOCITY]),
        mach=r[tb.T_MACH],
        height=TrajectoryData._new_feet(r[tb.T_HEIGHT]),
        slant_height=TrajectoryData._new_feet(r[tb.T_SLANT_HEIGHT]),
        drop_angle=TrajectoryData._new_rad(r[tb.T_DROP_ANGLE]),
        windage=TrajectoryData._new_feet(r[tb.T_WINDAGE]),
        windage_angle=TrajectoryData._new_rad(r[tb.T_WINDAGE_ANGLE]),
        slant_distance=TrajectoryData._new_feet(r[tb.T_SLANT_DISTANCE]),
        angle=TrajectoryData._new_rad(r[tb.T_ANGLE]),
        density_ratio=r[tb.T_DENSITY_RATIO],
        drag=r[tb.T_DRAG],
        energy=TrajectoryData._new_ft_lb(r[tb.T_ENERGY]),
        ogw=TrajectoryData._new_lb(r[tb.T_OGW]),
        flag=r[tb.T_FLAG],
    )


def _maybe_finalize(
    props: ShotProps, records: list[TrajectoryData], reason: int, fin: tb.RawState
) -> list[TrajectoryData]:
    """Append the exact terminal point when the trajectory ended other than by reaching range.

    Same as py-ballisticcalc's examples/tiny_bclibc_ctypes/_common.py `_maybe_finalize` (which mirrors
    BCLIBC_TrajectoryDataFilter's destructor); `fin` is (time, px, py, pz, vx, vy, vz, mach).
    """
    if reason in (_TERM_NO_TERMINATE, _TERM_TARGET_RANGE_REACHED):
        return records
    if records and fin[0] <= records[-1].time:
        return records
    position = Vector(fin[1], fin[2], fin[3])
    velocity = Vector(fin[4], fin[5], fin[6])
    _density_ratio, mach = props.get_density_and_mach_for_altitude(position.y)
    return [*records, TrajectoryData.from_props(props, fin[0], position, velocity, mach, TrajFlag.NONE)]


class TinyBclibcWasmIntegrationEngineBase(BaseIntegrationEngine):
    """Shared implementation; subclasses pick the precision."""

    DEFAULT_TIME_STEP = 0.0025  # matches tiny_bclibc_build_shot_props' calc_step formula

    PRECISION: str  # "double" | "single"
    # See TinyBclibcSingleIntegrationEngine.DEFAULT_ZERO_FINDING_ACCURACY in
    # https://github.com/o-murphy/py-ballisticcalc/tree/master/examples/tiny_bclibc_ctypes
    DEFAULT_ZERO_FINDING_ACCURACY: float | None = None

    def __init__(self, config: BaseEngineConfigDict | None) -> None:
        if self.DEFAULT_ZERO_FINDING_ACCURACY is not None and (config is None or "cZeroFindingAccuracy" not in config):
            config = {**(config or {}), "cZeroFindingAccuracy": self.DEFAULT_ZERO_FINDING_ACCURACY}
        super().__init__(config)
        self._select_module()  # loads it now, so a missing host/module fails at construction
        logger.debug(f"tiny_bclibc {tb.version()} on {tb.host()}")
        self.integration_step_count: int = 0
        self.trajectory_count = 0

    def _select_module(self) -> None:
        """Point the (process-wide) package at this engine's precision; loads it on first use."""
        tb.set_precision(self.PRECISION)
        tb.version()

    @override
    def get_calc_step(self) -> float:
        return super().get_calc_step() * self.DEFAULT_TIME_STEP

    def _tiny_shot(self, props: ShotProps) -> tb.ShotData:
        """Build a tiny_bclibc Shot from py_ballisticcalc's ShotProps.

        The drag table always goes in as a custom curve (the package's built-in G1/G7 tables
        wouldn't match a user's own table); like the natmod it holds at most 200 points.
        """
        cfg = self._config
        drag_table = props.shot.ammo.dm.drag_table
        return tb.Shot(
            bc=props.bc,
            weight_grain=props.weight_grains,
            diameter_inch=props.diameter_inch,
            length_inch=props.length_inch,
            muzzle_velocity_fps=props.muzzle_velocity_fps,
            sight_height_ft=props.sight_height_ft,
            twist_inch=props.twist_inch,
            temp_c=props.shot.atmo.temperature >> Temperature.Celsius,
            pressure_hpa=props.shot.atmo.pressure >> Pressure.hPa,
            altitude_ft=props.alt0_ft,
            humidity=props.shot.atmo.humidity,
            look_angle_rad=props.look_angle_rad,
            barrel_elevation_rad=props.barrel_elevation_rad,
            barrel_azimuth_rad=props.barrel_azimuth_rad,
            cant_angle_rad=props.shot.cant_angle >> Angular.Radian,
            latitude_deg=props.latitude if props.latitude is not None else math.nan,
            azimuth_deg=props.azimuth if props.azimuth is not None else math.nan,
            drag_type=tb.DRAG_CUSTOM,
            drag_mach=[dp.Mach for dp in drag_table],
            drag_cd=[dp.CD for dp in drag_table],
            winds=[
                tb.Wind(
                    velocity_fps=w.velocity >> Velocity.FPS,
                    direction_from_rad=w.direction_from >> Angular.Radian,
                    until_distance_ft=w.until_distance >> Distance.Foot,
                    max_distance_ft=w.MAX_DISTANCE_FEET,
                )
                for w in props.winds
            ],
            config=tb.Config(
                step_multiplier=cfg.cStepMultiplier,
                zero_finding_accuracy=cfg.cZeroFindingAccuracy,
                minimum_velocity=cfg.cMinimumVelocity,
                maximum_drop=cfg.cMaximumDrop,
                max_iterations=cfg.cMaxIterations,
                gravity_constant=cfg.cGravityConstant,
                minimum_altitude=cfg.cMinimumAltitude,
            ),
        )

    def _native_zero_point(self, props: ShotProps, distance: Distance) -> tuple[Angular, TrajectoryData]:
        self._select_module()
        try:
            angle, point = tb.zero_point(self._tiny_shot(props), distance >> Distance.Foot)
        except ValueError as exc:
            raise SolverRuntimeError(f"tiny_bclibc_find_zero_point failed: {exc}") from exc
        return Angular.Radian(angle), _row_to_trajectory_data(point)

    @override
    def zero_angle(self, shot_info: Shot, distance: Distance) -> Angular:
        """Native zero solver, falling back to BaseIntegrationEngine outside its domain.

        Same as the ctypes engines: tiny_bclibc's compact solver has no special cases for a
        vertical shot or a zero at/near the muzzle.
        """
        try:
            return self._native_zero_point(self._init_trajectory(shot_info), distance)[0]
        except SolverRuntimeError:
            return super().zero_angle(shot_info, distance)

    @override
    def zero_point(self, shot_info: Shot, distance: Distance) -> tuple[Angular, TrajectoryData]:
        return self._native_zero_point(self._init_trajectory(shot_info), distance)

    @override
    def find_zero_angle(self, shot_info: Shot, distance: Distance, lofted: bool = False) -> Angular:
        """Same native solver as zero_point, so find_zero_angle and find_zero_point agree."""
        return self._native_zero_point(self._init_trajectory(shot_info), distance)[0]

    @override
    def find_zero_point(
        self, shot_info: Shot, distance: Distance, lofted: bool = False
    ) -> tuple[Angular, TrajectoryData]:
        return self._native_zero_point(self._init_trajectory(shot_info), distance)

    @override
    def _integrate(
        self,
        props: ShotProps,
        range_limit_ft: float,
        range_step_ft: float,
        time_step: float = 0.0,
        filter_flags: TrajFlag | int = TrajFlag.NONE,
        dense_output: bool = False,
        **kwargs: Any,
    ) -> HitResult:
        """Run tiny_bclibc_integrate_stream in the module, then sort/finalize the rows in Python."""
        if dense_output:
            raise NotImplementedError(
                f"{type(self).__name__} doesn't support dense_output (the wasm module only "
                "returns filtered rows, no raw per-step data)."
            )
        self.trajectory_count += 1
        props.filter_flags = filter_flags
        self._select_module()

        try:
            traj = tb.integrate_ex(
                self._tiny_shot(props),
                tb.Request(
                    range_limit_ft=range_limit_ft,
                    range_step_ft=range_step_ft,
                    time_step=time_step,
                    filter_flags=int(filter_flags),
                ),
            )
        except ValueError as exc:
            raise SolverRuntimeError(f"tiny_bclibc_integrate_stream failed: {exc}") from exc

        records = [_row_to_trajectory_data(r) for r in traj.rows]
        # Rows arrive in the module's per-interval emission order; see _sort_rows in
        # https://github.com/o-murphy/py-ballisticcalc/blob/master/examples/tiny_bclibc_ctypes/_common.py
        # for why a stable sort by time (and no merging) is right.
        records.sort(key=lambda row: row.time)
        records = _maybe_finalize(props, records, traj.reason, traj.final)

        logger.debug(f"tiny_bclibc wasm ({self.PRECISION}) emitted {traj.total} rows")
        self.integration_step_count += traj.total
        termination_reason = _TERM_REASON_MAP.get(traj.reason)
        error = RangeError(termination_reason, records) if termination_reason is not None else None
        return HitResult(props, records, [], filter_flags > 0, error)


class TinyBclibcWasmTsitourasEngineDP(TinyBclibcWasmIntegrationEngineBase):
    """tiny_bclibc (real_t = double) as WebAssembly, via the `tiny_bclibc` package.

    Examples:
        >>> from py_ballisticcalc.engines.base_engine import BaseEngineConfigDict
        >>> engine = TinyBclibcWasmTsitourasEngineDP(BaseEngineConfigDict(cMinimumVelocity=0.0))
    """

    PRECISION = "double"


class TinyBclibcWasmTsitourasEngineSP(TinyBclibcWasmIntegrationEngineBase):
    """tiny_bclibc (real_t = float) as WebAssembly, via the `tiny_bclibc` package.

    Exists for parity with py-ballisticcalc's `examples/tiny_bclibc_ctypes` and carries the same float32
    limitations -- see `TinyBclibcSingleIntegrationEngine`'s docstring there.
    """

    PRECISION = "single"
    DEFAULT_ZERO_FINDING_ACCURACY = 1e-3

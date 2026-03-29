from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal

from ..artifacts import collect_provenance, ensure_dir, make_bundle_id, write_json, write_jsonl
from ..config import HostConfig, load_config
from ..instrument_profile import InstrumentMotorProfile, load_instrument_profile
from ..models import PlaybackEventGroup, PlaybackMotorChange, Segment
from ..playback_program import PlaybackPlan
from .models import MeasurementObservation, MeasurementPoint, MeasurementSession

CalibrationTransport = Literal["hardware", "synthetic"]


def _build_measurement_plan(
    *,
    motor_idx: int,
    target_hz: float,
    duration_s: float,
    connected_motors: int,
    flip_before_restart: bool = False,
    reversal_gap_ms: float | None = None,
) -> PlaybackPlan:
    dwell_us = max(1, int(round(duration_s * 1_000_000.0)))
    groups = [
        PlaybackEventGroup(
            delta_us=0,
            changes=(PlaybackMotorChange(motor_idx=motor_idx, target_hz=target_hz, flip_before_restart=flip_before_restart),),
        ),
    ]
    shadow_segments = [
        Segment(
            duration_us=dwell_us,
            motor_freq_hz=tuple(target_hz if idx == motor_idx else 0.0 for idx in range(connected_motors)),
        )
    ]
    if reversal_gap_ms is not None:
        groups.append(
            PlaybackEventGroup(
                delta_us=max(1, int(round(reversal_gap_ms * 1000.0))),
                changes=(PlaybackMotorChange(motor_idx=motor_idx, target_hz=0.0, flip_before_restart=False),),
            )
        )
        groups.append(
            PlaybackEventGroup(
                delta_us=max(1, int(round(reversal_gap_ms * 1000.0))),
                changes=(PlaybackMotorChange(motor_idx=motor_idx, target_hz=target_hz, flip_before_restart=True),),
            )
        )
    else:
        groups.append(
            PlaybackEventGroup(
                delta_us=dwell_us,
                changes=(PlaybackMotorChange(motor_idx=motor_idx, target_hz=0.0, flip_before_restart=False),),
            )
        )
    return PlaybackPlan(
        plan_id=f"calibration-m{motor_idx}-{int(round(target_hz))}",
        display_name=f"Motor {motor_idx} @ {target_hz:.1f} Hz",
        event_groups=tuple(groups),
        shadow_segments=tuple(shadow_segments),
        connected_motors=connected_motors,
        overflow_mode="strict",
        motor_change_count=sum(len(group.changes) for group in groups),
    )


def _metric_observation(profile: InstrumentMotorProfile, target_hz: float) -> tuple[dict[str, int | float], tuple[MeasurementObservation, ...], bool]:
    success = True
    metrics: dict[str, int | float] = {
        "underrun_count": 0,
        "queue_high_water": 2,
        "scheduling_late_max_us": 0,
        "crc_parse_errors": 0,
        "rx_parse_errors": 0,
        "timer_empty_events": 0,
        "timer_restart_count": 0,
        "event_groups_started": 2,
        "scheduler_guard_hits": 0,
        "control_late_max_us": 0,
        "control_overrun_count": 0,
        "wave_period_update_count": 1,
        "motor_start_count": 1,
        "motor_stop_count": 1,
        "flip_restart_count": 0,
        "launch_guard_count": 0,
        "engine_fault_count": 0,
        "engine_fault_mask": 0,
    }
    observations: list[MeasurementObservation] = []
    if target_hz < profile.resolved_min_hz or target_hz > profile.resolved_max_hz:
        success = False
        metrics["engine_fault_count"] = 1
        observations.append(MeasurementObservation(label="out_of_range", severity=1.0))
    if any(band.start_hz <= target_hz <= band.end_hz for band in profile.avoid_bands):
        metrics["control_overrun_count"] = 1
        observations.append(MeasurementObservation(label="avoid_band", severity=0.8))
    if any(band.start_hz <= target_hz <= band.end_hz for band in profile.resonance_bands):
        metrics["scheduling_late_max_us"] = 2500
        observations.append(MeasurementObservation(label="resonance_band", severity=0.5))
    if any(band.start_hz <= target_hz <= band.end_hz for band in profile.stall_prone_bands):
        success = False
        metrics["launch_guard_count"] = 1
        observations.append(MeasurementObservation(label="stall_prone_band", severity=1.0))
    if target_hz < profile.resolved_preferred_min_hz or target_hz > profile.resolved_preferred_max_hz:
        observations.append(MeasurementObservation(label="outside_preferred_band", severity=0.35))
    return metrics, tuple(observations), success


class CalibrationRunner:
    def __init__(
        self,
        *,
        cfg: HostConfig,
        cache_root: str | Path = ".cache/calibration",
        project_root: str | Path | None = None,
    ) -> None:
        self.cfg = cfg
        self.cache_root = ensure_dir(cache_root)
        self.project_root = Path(project_root).resolve() if project_root is not None else Path.cwd().resolve()
        self.instrument_profile = load_instrument_profile(cfg.instrument_profile_path)

    @classmethod
    def from_config(
        cls,
        *,
        config_path: str | Path = "config.toml",
        cache_root: str | Path = ".cache/calibration",
    ) -> "CalibrationRunner":
        return cls(cfg=load_config(config_path), cache_root=cache_root)

    def _bundle_dir(self, label: str) -> Path:
        return ensure_dir(self.cache_root / make_bundle_id("calibration", label))

    def _run_point(
        self,
        *,
        test_id: str,
        mode: str,
        motor_idx: int,
        target_hz: float,
        duration_s: float,
        transport: CalibrationTransport,
        accel_hz_per_s: float | None = None,
        launch_start_hz: float | None = None,
        launch_crossover_hz: float | None = None,
        reversal_gap_ms: float | None = None,
    ) -> tuple[MeasurementPoint, list[dict[str, Any]], list[dict[str, Any]]]:
        motor_profile = self.instrument_profile.ordered_motors[motor_idx]
        if transport == "synthetic":
            metrics, observations, success = _metric_observation(motor_profile, target_hz)
            point = MeasurementPoint(
                test_id=test_id,
                motor_idx=motor_idx,
                mode=mode,
                target_hz=target_hz,
                duration_s=duration_s,
                success=success,
                metrics=metrics,
                observations=observations,
                accel_hz_per_s=accel_hz_per_s,
                launch_start_hz=launch_start_hz,
                launch_crossover_hz=launch_crossover_hz,
                reversal_gap_ms=reversal_gap_ms,
            )
            return point, [], []

        cfg = self.cfg
        if launch_start_hz is not None:
            cfg = HostConfig(**{**cfg.__dict__, "playback_launch_start_hz": launch_start_hz})
        if launch_crossover_hz is not None:
            cfg = HostConfig(**{**cfg.__dict__, "playback_launch_crossover_hz": launch_crossover_hz})
        if accel_hz_per_s is not None:
            cfg = HostConfig(**{**cfg.__dict__, "playback_run_accel_hz_per_s": accel_hz_per_s})
        from ..hardware_capture import execute_playback_plan_capture

        plan = _build_measurement_plan(
            motor_idx=motor_idx,
            target_hz=target_hz,
            duration_s=duration_s,
            connected_motors=cfg.connected_motors,
            reversal_gap_ms=reversal_gap_ms,
        )
        capture = execute_playback_plan_capture(
            cfg=cfg,
            playback_plan=plan,
            min_note=0,
            max_note=127,
            transpose=0,
            auto_home_enabled=False,
        )
        metrics = asdict(capture.execution.metrics)
        success = (
            metrics["underrun_count"] == 0
            and metrics["control_overrun_count"] == 0
            and metrics["engine_fault_count"] == 0
            and metrics["launch_guard_count"] == 0
        )
        point = MeasurementPoint(
            test_id=test_id,
            motor_idx=motor_idx,
            mode=mode,
            target_hz=target_hz,
            duration_s=duration_s,
            success=success,
            metrics=metrics,
            observations=(),
            accel_hz_per_s=accel_hz_per_s,
            launch_start_hz=launch_start_hz,
            launch_crossover_hz=launch_crossover_hz,
            reversal_gap_ms=reversal_gap_ms,
        )
        status_rows = [dict(row, test_id=test_id) for row in capture.status_trace]
        metrics_rows = [dict(row, test_id=test_id) for row in capture.metrics_trace]
        return point, status_rows, metrics_rows

    def _finalize_session(
        self,
        *,
        session_type: str,
        motors: tuple[int, ...],
        points: list[MeasurementPoint],
        status_rows: list[dict[str, Any]],
        metrics_rows: list[dict[str, Any]],
        transport: CalibrationTransport,
    ) -> Path:
        bundle_dir = self._bundle_dir(session_type)
        session = MeasurementSession(
            session_id=bundle_dir.name,
            session_type=session_type,
            motors=motors,
            points=tuple(points),
            operator=self.instrument_profile.calibration_operator,
            firmware_version=self.instrument_profile.calibration_firmware_version,
            hardware_notes=self.instrument_profile.calibration_hardware_notes,
            transport_mode=transport,
        )
        write_json(
            bundle_dir / "manifest.json",
            {
                "bundle_id": bundle_dir.name,
                "bundle_type": "calibration",
                "session_type": session_type,
                "transport_mode": transport,
                "provenance": collect_provenance(
                    cwd=self.project_root,
                    instrument_profile_path=self.cfg.instrument_profile_path,
                ),
            },
        )
        write_json(bundle_dir / "session.json", asdict(session))
        write_jsonl(bundle_dir / "status_trace.jsonl", status_rows)
        write_jsonl(bundle_dir / "metrics_trace.jsonl", metrics_rows)
        write_json(bundle_dir / "annotations.json", {"annotations": []})
        successful = [point for point in points if point.success]
        write_json(
            bundle_dir / "summary.json",
            {
                "point_count": len(points),
                "successful_point_count": len(successful),
                "motors": list(motors),
                "target_hz_min": min((point.target_hz for point in points), default=0.0),
                "target_hz_max": max((point.target_hz for point in points), default=0.0),
            },
        )
        return bundle_dir

    def run_frequency_sweep(
        self,
        *,
        motor_idx: int,
        start_hz: float,
        stop_hz: float,
        step_hz: float,
        duration_s: float = 1.0,
        transport: CalibrationTransport = "hardware",
    ) -> Path:
        points: list[MeasurementPoint] = []
        status_rows: list[dict[str, Any]] = []
        metrics_rows: list[dict[str, Any]] = []
        current = start_hz
        while current <= stop_hz + 1e-9:
            point, point_status, point_metrics = self._run_point(
                test_id=f"frequency-{motor_idx}-{int(round(current * 10))}",
                mode="frequency_sweep",
                motor_idx=motor_idx,
                target_hz=current,
                duration_s=duration_s,
                transport=transport,
            )
            points.append(point)
            status_rows.extend(point_status)
            metrics_rows.extend(point_metrics)
            current += step_hz
        return self._finalize_session(
            session_type="frequency_sweep",
            motors=(motor_idx,),
            points=points,
            status_rows=status_rows,
            metrics_rows=metrics_rows,
            transport=transport,
        )

    def run_launch_sweep(
        self,
        *,
        motor_idx: int,
        target_hz: float,
        launch_starts_hz: tuple[float, ...],
        launch_crossovers_hz: tuple[float, ...],
        duration_s: float = 1.0,
        transport: CalibrationTransport = "hardware",
    ) -> Path:
        points: list[MeasurementPoint] = []
        status_rows: list[dict[str, Any]] = []
        metrics_rows: list[dict[str, Any]] = []
        for start_hz in launch_starts_hz:
            for crossover_hz in launch_crossovers_hz:
                point, point_status, point_metrics = self._run_point(
                    test_id=f"launch-{motor_idx}-{int(round(start_hz))}-{int(round(crossover_hz))}",
                    mode="launch_sweep",
                    motor_idx=motor_idx,
                    target_hz=target_hz,
                    duration_s=duration_s,
                    transport=transport,
                    launch_start_hz=start_hz,
                    launch_crossover_hz=crossover_hz,
                )
                points.append(point)
                status_rows.extend(point_status)
                metrics_rows.extend(point_metrics)
        return self._finalize_session(
            session_type="launch_sweep",
            motors=(motor_idx,),
            points=points,
            status_rows=status_rows,
            metrics_rows=metrics_rows,
            transport=transport,
        )

    def run_reversal_sweep(
        self,
        *,
        motor_idx: int,
        target_hz: float,
        reversal_gaps_ms: tuple[float, ...],
        duration_s: float = 1.0,
        transport: CalibrationTransport = "hardware",
    ) -> Path:
        points: list[MeasurementPoint] = []
        status_rows: list[dict[str, Any]] = []
        metrics_rows: list[dict[str, Any]] = []
        for gap_ms in reversal_gaps_ms:
            point, point_status, point_metrics = self._run_point(
                test_id=f"reversal-{motor_idx}-{int(round(gap_ms))}",
                mode="reversal_sweep",
                motor_idx=motor_idx,
                target_hz=target_hz,
                duration_s=duration_s,
                transport=transport,
                reversal_gap_ms=gap_ms,
            )
            points.append(point)
            status_rows.extend(point_status)
            metrics_rows.extend(point_metrics)
        return self._finalize_session(
            session_type="reversal_sweep",
            motors=(motor_idx,),
            points=points,
            status_rows=status_rows,
            metrics_rows=metrics_rows,
            transport=transport,
        )

    def run_long_run_stability(
        self,
        *,
        motor_idx: int,
        target_hz: float,
        duration_s: float,
        transport: CalibrationTransport = "hardware",
    ) -> Path:
        point, status_rows, metrics_rows = self._run_point(
            test_id=f"long-run-{motor_idx}-{int(round(target_hz))}",
            mode="long_run_stability",
            motor_idx=motor_idx,
            target_hz=target_hz,
            duration_s=duration_s,
            transport=transport,
        )
        return self._finalize_session(
            session_type="long_run_stability",
            motors=(motor_idx,),
            points=[point],
            status_rows=status_rows,
            metrics_rows=metrics_rows,
            transport=transport,
        )

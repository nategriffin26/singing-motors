from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ..artifacts import read_json, utc_now_iso, write_json
from .models import CalibrationBandRecommendation, MeasurementObservation, MeasurementPoint, MotorCalibrationRecommendation, ProfilePatch


def _load_points(bundle_dir: str | Path) -> tuple[MeasurementPoint, ...]:
    session = read_json(Path(bundle_dir) / "session.json")
    out: list[MeasurementPoint] = []
    for raw_point in session.get("points", []):
        observations = tuple(
            MeasurementObservation(
                label=str(item["label"]),
                severity=float(item.get("severity", 1.0)),
                notes=str(item.get("notes", "")),
            )
            for item in raw_point.get("observations", [])
        )
        out.append(
            MeasurementPoint(
                test_id=str(raw_point["test_id"]),
                motor_idx=int(raw_point["motor_idx"]),
                mode=str(raw_point["mode"]),
                target_hz=float(raw_point["target_hz"]),
                duration_s=float(raw_point["duration_s"]),
                success=bool(raw_point["success"]),
                metrics=dict(raw_point.get("metrics", {})),
                observations=observations,
                accel_hz_per_s=float(raw_point["accel_hz_per_s"]) if raw_point.get("accel_hz_per_s") is not None else None,
                launch_start_hz=float(raw_point["launch_start_hz"]) if raw_point.get("launch_start_hz") is not None else None,
                launch_crossover_hz=float(raw_point["launch_crossover_hz"])
                if raw_point.get("launch_crossover_hz") is not None
                else None,
                reversal_gap_ms=float(raw_point["reversal_gap_ms"]) if raw_point.get("reversal_gap_ms") is not None else None,
            )
        )
    return tuple(out)


def _group_points(bundle_dirs: tuple[str | Path, ...]) -> dict[int, list[MeasurementPoint]]:
    grouped: dict[int, list[MeasurementPoint]] = defaultdict(list)
    for bundle_dir in bundle_dirs:
        for point in _load_points(bundle_dir):
            grouped[point.motor_idx].append(point)
    return grouped


def _recommend_bands(points: list[MeasurementPoint], label: str) -> tuple[CalibrationBandRecommendation, ...]:
    flagged = sorted(
        point for point in points if any(obs.label == label for obs in point.observations)
    )
    bands: list[CalibrationBandRecommendation] = []
    for point in flagged:
        bands.append(
            CalibrationBandRecommendation(
                start_hz=max(0.0, point.target_hz - 10.0),
                end_hz=point.target_hz + 10.0,
                severity=max(obs.severity for obs in point.observations if obs.label == label),
                label=label,
                source_measurement_ids=(point.test_id,),
            )
        )
    return tuple(bands)


def fit_profile_from_bundles(
    *,
    instrument_path: str | Path,
    bundle_dirs: tuple[str | Path, ...],
) -> ProfilePatch:
    grouped = _group_points(bundle_dirs)
    recommendations: list[MotorCalibrationRecommendation] = []
    warnings: list[str] = []
    for motor_idx, points in sorted(grouped.items()):
        successful = sorted((point for point in points if point.success), key=lambda point: point.target_hz)
        if not successful:
            recommendations.append(
                MotorCalibrationRecommendation(
                    motor_idx=motor_idx,
                    status="draft",
                    confidence=0.0,
                    warnings=("no successful measurement points",),
                    measurement_ids=tuple(point.test_id for point in points),
                )
            )
            warnings.append(f"motor {motor_idx}: no successful measurement points")
            continue

        target_hz = sorted(point.target_hz for point in successful)
        launch_points = [point for point in successful if point.mode == "launch_sweep" and point.launch_start_hz is not None]
        reversal_points = [point for point in successful if point.mode == "reversal_sweep" and point.reversal_gap_ms is not None]
        confidence = min(1.0, max(0.15, len(successful) / max(1.0, len(points))))
        fitted_preferred_min = target_hz[max(0, len(target_hz) // 4 - 1)]
        fitted_preferred_max = target_hz[min(len(target_hz) - 1, (len(target_hz) * 3) // 4)]
        best_launch = min(
            launch_points,
            key=lambda point: (
                float(point.metrics.get("launch_guard_count", 0)),
                float(point.metrics.get("control_overrun_count", 0)),
            ),
            default=None,
        )
        best_reversal = min(
            reversal_points,
            key=lambda point: float(point.reversal_gap_ms or 0.0),
            default=None,
        )
        recommendations.append(
            MotorCalibrationRecommendation(
                motor_idx=motor_idx,
                status="draft",
                confidence=round(confidence, 3),
                measured_min_hz=target_hz[0],
                measured_max_hz=target_hz[-1],
                fitted_min_hz=target_hz[0],
                fitted_max_hz=target_hz[-1],
                fitted_preferred_min_hz=fitted_preferred_min,
                fitted_preferred_max_hz=fitted_preferred_max,
                fitted_launch_start_hz=best_launch.launch_start_hz if best_launch is not None else None,
                fitted_launch_crossover_hz=best_launch.launch_crossover_hz if best_launch is not None else None,
                fitted_safe_reverse_min_gap_ms=best_reversal.reversal_gap_ms if best_reversal is not None else None,
                resonance_bands=_recommend_bands(points, "resonance_band"),
                avoid_bands=_recommend_bands(points, "avoid_band"),
                stall_prone_bands=_recommend_bands(points, "stall_prone_band"),
                measurement_ids=tuple(point.test_id for point in points),
                warnings=tuple(
                    sorted(
                        {
                            obs.label
                            for point in points
                            for obs in point.observations
                            if obs.label in {"out_of_range", "stall_prone_band"}
                        }
                    )
                ),
            )
        )
    return ProfilePatch(
        instrument_path=str(Path(instrument_path).expanduser().resolve()),
        motors=tuple(recommendations),
        generated_at_utc=utc_now_iso(),
        source_session_ids=tuple(Path(bundle_dir).name for bundle_dir in bundle_dirs),
        warnings=tuple(warnings),
    )


def write_profile_patch(path: str | Path, patch: ProfilePatch) -> Path:
    return write_json(path, asdict(patch))

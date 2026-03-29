#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from music2.artifacts import read_json
from music2.instrument_profile import FrequencyBand, load_instrument_profile


def _render_band_list(prefix: str, bands: list[dict[str, object]]) -> list[str]:
    lines: list[str] = []
    for band in bands:
        lines.append(f"[[instrument.motors.{prefix}]]")
        lines.append(f"start_hz = {float(band['start_hz']):.3f}")
        lines.append(f"end_hz = {float(band['end_hz']):.3f}")
        lines.append(f"severity = {float(band['severity']):.3f}")
        lines.append(f"label = \"{str(band['label'])}\"")
        lines.append("")
    return lines


def _render_profile(existing_path: Path, patch_payload: dict[str, object]) -> str:
    existing = load_instrument_profile(existing_path)
    motors_by_idx = {int(motor["motor_idx"]): motor for motor in patch_payload.get("motors", [])}
    lines = [
        "[instrument]",
        f"name = \"{existing.name}\"",
        f"profile_version = {existing.profile_version}",
        f"motor_count = {existing.motor_count}",
        f"description = \"{existing.description}\"",
        f"calibration_schema_version = {existing.calibration_schema_version}",
        f"calibration_measurement_date = \"{existing.calibration_measurement_date}\"",
        f"calibration_firmware_version = \"{existing.calibration_firmware_version}\"",
        f"calibration_operator = \"{existing.calibration_operator}\"",
        f"calibration_hardware_notes = \"{existing.calibration_hardware_notes}\"",
        "",
    ]
    for motor in existing.ordered_motors:
        lines.extend(
            [
                "[[instrument.motors]]",
                f"motor_idx = {motor.motor_idx}",
                f"label = \"{motor.label}\"",
                f"min_hz = {motor.min_hz:.3f}",
                f"max_hz = {motor.max_hz:.3f}",
                f"preferred_min_hz = {motor.preferred_min_hz:.3f}",
                f"preferred_max_hz = {motor.preferred_max_hz:.3f}",
                f"launch_start_hz = {motor.launch_start_hz:.3f}",
                f"launch_crossover_hz = {motor.launch_crossover_hz:.3f}",
                f"safe_reverse_min_gap_ms = {motor.safe_reverse_min_gap_ms:.3f}",
                f"safe_reverse_margin_ms = {motor.safe_reverse_margin_ms:.3f}",
                f"weight_pitch_stability = {motor.weight_pitch_stability:.3f}",
                f"weight_attack_cleanliness = {motor.weight_attack_cleanliness:.3f}",
                f"weight_sustain_quality = {motor.weight_sustain_quality:.3f}",
            ]
        )
        patch = motors_by_idx.get(motor.motor_idx)
        if patch is not None:
            for key in (
                "status",
                "confidence",
                "measured_min_hz",
                "measured_max_hz",
                "fitted_min_hz",
                "fitted_max_hz",
                "fitted_preferred_min_hz",
                "fitted_preferred_max_hz",
                "fitted_launch_start_hz",
                "fitted_launch_crossover_hz",
                "fitted_safe_reverse_min_gap_ms",
                "safe_accel_min_hz_per_s",
                "safe_accel_max_hz_per_s",
            ):
                if patch.get(key) is None:
                    continue
                profile_key = {
                    "status": "calibration_status",
                    "confidence": "calibration_confidence",
                }.get(key, key)
                value = patch[key]
                if isinstance(value, str):
                    lines.append(f"{profile_key} = \"{value}\"")
                else:
                    lines.append(f"{profile_key} = {float(value):.3f}")
            measurement_ids = patch.get("measurement_ids") or []
            if measurement_ids:
                joined = ", ".join(f"\"{str(item)}\"" for item in measurement_ids)
                lines.append(f"calibration_measurement_ids = [{joined}]")
            for band_key in ("resonance_bands", "avoid_bands", "stall_prone_bands"):
                lines.extend(_render_band_list(band_key, list(patch.get(band_key) or [])))
        else:
            for band_name, bands in (
                ("resonance_bands", motor.resonance_bands),
                ("avoid_bands", motor.avoid_bands),
                ("stall_prone_bands", motor.stall_prone_bands),
            ):
                if not bands:
                    continue
                lines.extend(
                    _render_band_list(
                        band_name,
                        [
                            {
                                "start_hz": band.start_hz,
                                "end_hz": band.end_hz,
                                "severity": band.severity,
                                "label": band.label,
                            }
                            for band in bands
                        ],
                    )
                )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--instrument-profile", required=True)
    parser.add_argument("--patch", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    patch_payload = read_json(args.patch)
    rendered = _render_profile(Path(args.instrument_profile).expanduser().resolve(), patch_payload)
    out_path = Path(args.out).expanduser().resolve()
    out_path.write_text(rendered, encoding="utf-8")
    print(json.dumps({"merged_profile_path": str(out_path)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

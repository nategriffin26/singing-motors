from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from music2.calibration.fit import fit_profile_from_bundles
from music2.calibration.runner import CalibrationRunner
from music2.instrument_profile import load_instrument_profile


def test_calibration_frequency_sweep_synthetic_writes_bundle(tmp_path: Path) -> None:
    runner = CalibrationRunner.from_config(cache_root=tmp_path)
    bundle_dir = runner.run_frequency_sweep(
        motor_idx=0,
        start_hz=20.0,
        stop_hz=80.0,
        step_hz=20.0,
        transport="synthetic",
    )
    session = json.loads((bundle_dir / "session.json").read_text(encoding="utf-8"))
    assert session["session_type"] == "frequency_sweep"
    assert len(session["points"]) == 4


def test_fit_profile_from_synthetic_bundles(tmp_path: Path) -> None:
    runner = CalibrationRunner.from_config(cache_root=tmp_path)
    freq_bundle = runner.run_frequency_sweep(
        motor_idx=0,
        start_hz=20.0,
        stop_hz=120.0,
        step_hz=20.0,
        transport="synthetic",
    )
    launch_bundle = runner.run_launch_sweep(
        motor_idx=0,
        target_hz=120.0,
        launch_starts_hz=(40.0, 60.0),
        launch_crossovers_hz=(160.0, 200.0),
        transport="synthetic",
    )
    reversal_bundle = runner.run_reversal_sweep(
        motor_idx=0,
        target_hz=120.0,
        reversal_gaps_ms=(4.0, 8.0, 12.0),
        transport="synthetic",
    )
    patch = fit_profile_from_bundles(
        instrument_path="profiles/default_instrument.toml",
        bundle_dirs=(freq_bundle, launch_bundle, reversal_bundle),
    )
    assert patch.motors
    assert patch.motors[0].measured_min_hz is not None
    assert patch.motors[0].fitted_launch_start_hz is not None


def test_merge_profile_script_writes_calibrated_profile(tmp_path: Path) -> None:
    runner = CalibrationRunner.from_config(cache_root=tmp_path)
    freq_bundle = runner.run_frequency_sweep(
        motor_idx=0,
        start_hz=20.0,
        stop_hz=120.0,
        step_hz=20.0,
        transport="synthetic",
    )
    patch = fit_profile_from_bundles(
        instrument_path="profiles/default_instrument.toml",
        bundle_dirs=(freq_bundle,),
    )
    patch_path = tmp_path / "profile_patch.json"
    patch_path.write_text(json.dumps({
        "instrument_path": patch.instrument_path,
        "motors": [
            {
                "motor_idx": recommendation.motor_idx,
                "status": recommendation.status,
                "confidence": recommendation.confidence,
                "measured_min_hz": recommendation.measured_min_hz,
                "measured_max_hz": recommendation.measured_max_hz,
                "fitted_min_hz": recommendation.fitted_min_hz,
                "fitted_max_hz": recommendation.fitted_max_hz,
                "fitted_preferred_min_hz": recommendation.fitted_preferred_min_hz,
                "fitted_preferred_max_hz": recommendation.fitted_preferred_max_hz,
                "fitted_launch_start_hz": recommendation.fitted_launch_start_hz,
                "fitted_launch_crossover_hz": recommendation.fitted_launch_crossover_hz,
                "fitted_safe_reverse_min_gap_ms": recommendation.fitted_safe_reverse_min_gap_ms,
                "safe_accel_min_hz_per_s": recommendation.safe_accel_min_hz_per_s,
                "safe_accel_max_hz_per_s": recommendation.safe_accel_max_hz_per_s,
                "measurement_ids": list(recommendation.measurement_ids),
                "resonance_bands": [],
                "avoid_bands": [],
                "stall_prone_bands": [],
            }
            for recommendation in patch.motors
        ],
    }, indent=2), encoding="utf-8")
    out_path = tmp_path / "default_instrument.calibrated.toml"
    subprocess.run(
        [
            sys.executable,
            "scripts/calibrate/merge_profile.py",
            "--instrument-profile",
            "profiles/default_instrument.toml",
            "--patch",
            str(patch_path),
            "--out",
            str(out_path),
        ],
        check=True,
        cwd=Path(__file__).resolve().parents[1],
    )
    profile = load_instrument_profile(out_path)
    assert profile.ordered_motors[0].fitted_min_hz is not None

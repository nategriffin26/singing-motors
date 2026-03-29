from __future__ import annotations

from pathlib import Path

import pytest

from music2.instrument_profile import (
    DEFAULT_INSTRUMENT_PROFILE_PATH,
    load_instrument_profile,
    resolve_instrument_profile_path,
)


def test_default_instrument_profile_loads() -> None:
    profile = load_instrument_profile(DEFAULT_INSTRUMENT_PROFILE_PATH)

    assert profile.name == "default_bench_6motor_v2"
    assert profile.motor_count == 6
    assert len(profile.motors) == 6
    assert profile.ordered_motors[0].launch_start_hz == 60.0
    assert profile.ordered_motors[0].preferred_min_hz == 40.0
    assert profile.ordered_motors[0].preferred_max_hz == 800.0
    assert profile.ordered_motors[0].safe_reverse_min_gap_ms == 10.0


def test_resolve_instrument_profile_path_respects_base_dir(tmp_path: Path) -> None:
    target = tmp_path / "profiles" / "x.toml"
    target.parent.mkdir()
    target.write_text("", encoding="utf-8")

    resolved = resolve_instrument_profile_path("profiles/x.toml", base_dir=tmp_path)

    assert resolved == target.resolve()


def test_load_instrument_profile_rejects_motor_count_mismatch(tmp_path: Path) -> None:
    profile_path = tmp_path / "bad.toml"
    profile_path.write_text(
        """
[instrument]
name = "bad"
profile_version = 1
motor_count = 2

[[instrument.motors]]
motor_idx = 0
min_hz = 40.0
max_hz = 400.0
""".strip()
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="motor_count must match the number of motor entries"):
        load_instrument_profile(profile_path)


def test_load_instrument_profile_rejects_invalid_band_severity(tmp_path: Path) -> None:
    profile_path = tmp_path / "bad_band.toml"
    profile_path.write_text(
        """
[instrument]
name = "bad_band"
profile_version = 1
motor_count = 1

[[instrument.motors]]
motor_idx = 0
min_hz = 40.0
max_hz = 400.0
resonance_bands = [{ start_hz = 120.0, end_hz = 140.0, severity = 1.5 }]
""".strip()
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="band severity must be in range \\[0, 1\\]"):
        load_instrument_profile(profile_path)

from __future__ import annotations

from pathlib import Path


def _pulse_engine_source() -> str:
    source_path = Path(__file__).resolve().parents[1] / "firmware" / "esp32" / "src" / "pulse_engine.c"
    return source_path.read_text(encoding="utf-8")


def test_zero_target_uses_decel_ramp_before_stop() -> None:
    source = _pulse_engine_source()
    assert "PULSE_ENGINE_STOP_RAMP_HALF_PERIOD_US" in source
    assert "if (new_target_hp == 0u) {" in source
    assert "m->stop_after_ramp = true;" in source


def test_emergency_stop_path_remains_instant() -> None:
    source = _pulse_engine_source()
    assert "pulse_engine_set_targets(zero_freq, 0u);" in source


def test_flip_wrapper_remains_compatible_and_direction_capable_for_exact_backend() -> None:
    source = _pulse_engine_source()
    assert "void pulse_engine_set_targets_with_flips(" in source
    assert "direction ^= 0x01u;" in source
    assert "void pulse_engine_set_one_target_exact_with_direction(" in source
    assert ".supports_direction_flips = true," in source

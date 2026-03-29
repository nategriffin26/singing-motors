from __future__ import annotations

from pathlib import Path


def _pulse_engine_source() -> str:
    source_path = Path(__file__).resolve().parents[1] / "firmware" / "esp32" / "src" / "pulse_engine.c"
    return source_path.read_text(encoding="utf-8")


def test_exact_target_updates_still_route_through_exact_ramp_path() -> None:
    source = _pulse_engine_source()
    assert "pulse_engine_apply_target_locked(motor_idx, freq_dhz, ramp_us, true);" in source


def test_exact_zero_ramp_lock_in_bypasses_redundant_update_skip() -> None:
    source = _pulse_engine_source()
    assert "const bool exact_zero_ramp_lock_in = exact_ramp && ramp_us == 0u;" in source
    assert "!exact_zero_ramp_lock_in" in source


def test_non_exact_paths_cap_to_requested_ramp_duration() -> None:
    source = _pulse_engine_source()
    # The restored exact-motion engine keeps non-exact updates bounded by the
    # caller-provided ramp budget while exact-ramp callers bypass the cap.
    assert source.count("if (ramp > ramp_us) {") >= 3
    assert source.count("ramp = ramp_us;") >= 3


def test_stop_target_uses_stop_after_ramp_latch() -> None:
    source = _pulse_engine_source()
    assert "m->stop_after_ramp = true;" in source
    assert "force_motor_stop(m, s_step_pins[motor_idx]);" in source

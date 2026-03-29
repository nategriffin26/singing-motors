from __future__ import annotations

import re
from pathlib import Path


def _motion_commands_source() -> str:
    path = Path(__file__).resolve().parents[1] / "firmware" / "esp32" / "src" / "motion_commands.c"
    return path.read_text(encoding="utf-8")


def test_home_loop_uses_fine_mode_microsecond_polling_near_target() -> None:
    source = _motion_commands_source()
    match = re.search(r"static runtime_err_t home_one_motor\([\s\S]*?\n\}", source)
    assert match is not None, "expected home_one_motor definition"
    body = match.group(0)

    assert "esp_rom_delay_us(HOME_FINE_POLL_US);" in body
    assert "steps_remaining <= (uint64_t)HOME_FINE_WINDOW_STEPS" in body
    assert "if (!fine_mode && (accel_hz_per_s_dhz > 0u)" in body
    assert "motion_apply_exact_target(motor_idx, current_freq_dhz, 75000u, true);" in body


def test_home_fine_poll_constants_are_defined_with_reasonable_bounds() -> None:
    source = _motion_commands_source()
    window_match = re.search(r"#define\s+HOME_FINE_WINDOW_STEPS\s+\((\d+)u\)", source)
    poll_match = re.search(r"#define\s+HOME_FINE_POLL_US\s+\((\d+)u\)", source)

    assert window_match is not None, "expected HOME_FINE_WINDOW_STEPS define"
    assert poll_match is not None, "expected HOME_FINE_POLL_US define"

    window_steps = int(window_match.group(1))
    poll_us = int(poll_match.group(1))

    assert window_steps >= 16
    assert poll_us <= 1000

from __future__ import annotations

import re
from pathlib import Path


def _pulse_engine_source() -> str:
    source_path = Path(__file__).resolve().parents[1] / "firmware" / "esp32" / "src" / "pulse_engine.c"
    return source_path.read_text(encoding="utf-8")


def _pulse_engine_header() -> str:
    header_path = Path(__file__).resolve().parents[1] / "firmware" / "esp32" / "src" / "pulse_engine.h"
    return header_path.read_text(encoding="utf-8")


def test_restored_exact_engine_uses_register_fast_path_in_hot_loop() -> None:
    source = _pulse_engine_source()
    assert "static inline void IRAM_ATTR set_step_pin_level" in source
    assert "GPIO.out_w1ts" in source
    assert "GPIO.out_w1tc" in source


def test_isr_callback_and_phase_sync_helpers_exist() -> None:
    source = _pulse_engine_source()
    assert "static bool IRAM_ATTR pulse_timer_on_alarm_cb" in source
    assert "pulse_engine_sync_now_locked" in source
    assert "pulse_engine_advance_elapsed_locked(elapsed_chunk);" in source
    assert "interpolate_half_period" in source


def test_alarm_updates_are_programmed_for_isr_and_scheduler_paths() -> None:
    source = _pulse_engine_source()

    isr_match = re.search(r"static bool IRAM_ATTR pulse_timer_on_alarm_cb\([\s\S]*?\n\}", source)
    assert isr_match is not None, "expected ISR definition"
    isr_body = isr_match.group(0)
    assert "gptimer_set_alarm_action(timer, &alarm_cfg);" in isr_body
    assert "portEXIT_CRITICAL_ISR(&s_engine_lock);" in isr_body

    schedule_match = re.search(r"static void pulse_engine_schedule_from_now\([\s\S]*?\n\}", source)
    assert schedule_match is not None, "expected schedule helper definition"
    schedule_body = schedule_match.group(0)
    assert "gptimer_set_alarm_action(s_pulse_timer, &alarm_cfg);" in schedule_body


def test_position_loss_diagnostic_is_exposed() -> None:
    source = _pulse_engine_source()
    header = _pulse_engine_header()
    assert "s_motor_position_lost[MOTOR_COUNT]" in source
    assert "diag->pulse_position_lost_mask = lost_mask;" in source
    assert "pulse_position_lost_mask" in header

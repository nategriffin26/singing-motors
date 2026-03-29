from __future__ import annotations

import re
from pathlib import Path


def _pulse_engine_source() -> str:
    source_path = Path(__file__).resolve().parents[1] / "firmware" / "esp32" / "src" / "pulse_engine.c"
    return source_path.read_text(encoding="utf-8")


def test_set_targets_advances_elapsed_phase_instead_of_unconditional_rebase() -> None:
    source = _pulse_engine_source()
    wrapper_match = re.search(r"void pulse_engine_set_targets\([\s\S]*?\n\}", source)
    assert wrapper_match is not None, "expected pulse_engine_set_targets definition"
    wrapper_body = wrapper_match.group(0)

    helper_match = re.search(r"void pulse_engine_set_targets_with_flips\([\s\S]*?\n\}", source)
    assert helper_match is not None, "expected pulse_engine_set_targets_with_flips definition"
    helper_body = helper_match.group(0)

    assert "pulse_engine_set_targets_with_flips(freq_dhz, 0u, max_ramp_us);" in wrapper_body
    assert "pulse_engine_sync_now_locked();" in helper_body
    assert "pulse_engine_advance_elapsed_locked(elapsed_chunk);" in source
    assert "Reset the ISR time-base to now without doing the full per-motor advance" not in helper_body


def test_schedule_from_now_no_longer_rebases_last_count() -> None:
    source = _pulse_engine_source()
    match = re.search(r"static void pulse_engine_schedule_from_now\([\s\S]*?\n\}", source)
    assert match is not None, "expected pulse_engine_schedule_from_now definition"
    body = match.group(0)

    assert "if (has_active && now_count_us > s_last_count_us)" not in body


def test_isr_consumes_pending_elapsed_budget() -> None:
    source = _pulse_engine_source()
    assert "s_pending_elapsed_us" not in source

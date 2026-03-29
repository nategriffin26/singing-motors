from __future__ import annotations

from pathlib import Path


def _motion_commands_source() -> str:
    path = Path(__file__).resolve().parents[1] / "firmware" / "esp32" / "src" / "motion_commands.c"
    return path.read_text(encoding="utf-8")


def test_warmup_and_step_motion_use_true_2ms_busy_wait() -> None:
    source = _motion_commands_source()
    assert "esp_rom_delay_us(WARMUP_POLL_MS * 1000u);" in source
    assert "esp_rom_delay_us(STEP_MOTION_POLL_MS * 1000u);" in source
    assert "vTaskDelay(delay_ticks_from_ms(WARMUP_POLL_MS));" not in source
    assert "vTaskDelay(delay_ticks_from_ms(STEP_MOTION_POLL_MS));" not in source


def test_step_motion_uses_fine_window_and_fine_poll_for_exact_stop() -> None:
    source = _motion_commands_source()
    assert "#define STEP_MOTION_MIN_MECHANICAL_DHZ  (150u)" in source
    assert "#define STEP_MOTION_FINE_FREQ_DHZ  (STEP_MOTION_MIN_MECHANICAL_DHZ)" in source
    assert "#define STEP_MOTION_FINE_WINDOW_STEPS (48u)" in source
    assert "#define STEP_MOTION_FINE_POLL_US (250u)" in source
    assert "esp_rom_delay_us(STEP_MOTION_FINE_POLL_US);" in source
    assert "fine_window_steps > STEP_MOTION_FINE_WINDOW_STEPS" in source
    assert "step_motion_fine_freq_dhz" in source
    assert "if (launch_dhz < STEP_MOTION_MIN_MECHANICAL_DHZ) {" in source


def test_warmup_and_step_motion_accel_math_uses_u64_intermediate() -> None:
    source = _motion_commands_source()
    assert source.count("* 1000000ULL") >= 2
    assert "((uint64_t)delta * 1000000ULL) / (uint64_t)rate_dhz_per_s" in source
    assert "step_motion_ramp_steps_abs_between(" in source


def test_step_motion_precomputes_phase_plans_instead_of_retuning_every_poll() -> None:
    source = _motion_commands_source()
    assert "step_motion_phase_plan_t phase_plans[MOTOR_COUNT];" in source
    assert "phase_plans[i] = step_motion_plan_phase(ph);" in source
    assert "phase_hold_ready_us[MOTOR_COUNT]" in source
    assert "step_motion_apply_playback_target_with_direction(" in source
    assert "playback_wave_engine_claim_step_gpio()" in source
    assert "playback_wave_engine_tick(now_us);" in source
    assert "playback_wave_engine_get_step_counts(step_counts);" in source
    assert "plan->decel_start_steps" in source
    assert "plan->stop_start_steps" in source
    assert "desired_freq_dhz" not in source
    assert "last_freq_update_us" not in source


def test_step_motion_idle_or_zero_target_phases_hold_without_spinning() -> None:
    source = _motion_commands_source()
    loop_anchor = source.find("const step_motion_phase_t *ph = &m->phases[m->current_phase];")
    assert loop_anchor != -1

    combined_idx = source.find("if (ph->peak_dhz == 0u || ph->target_steps == 0u) {", loop_anchor)
    assert combined_idx != -1
    assert "idle_elapsed_ms >= (int64_t)ph->hold_ms" in source
    assert "step_motion_apply_playback_target_with_direction(i, 0u, 0u, ph->direction);" in source


def test_step_motion_stall_watchdog_uses_monotonic_step_progress() -> None:
    source = _motion_commands_source()
    assert "uint64_t motion_step_sum = 0u;" in source
    assert "uint64_t last_motion_step_sum = 0u;" in source
    assert "motion_step_sum += step_counts[i];" in source
    assert "if (!progress_initialized || motion_step_sum > last_motion_step_sum) {" in source
    assert "phase_stop_ramp_us" not in source


def test_step_motion_uses_playback_wave_backend_instead_of_pulse_engine() -> None:
    source = _motion_commands_source()
    step_motion_start = source.index("runtime_err_t motion_commands_step_motion(")
    step_motion_end = source.index("const char *motion_commands_error_detail(", step_motion_start)
    step_motion_body = source[step_motion_start:step_motion_end]
    assert "playback_wave_engine_claim_step_gpio()" in step_motion_body
    assert "playback_wave_engine_reset_step_counts();" in step_motion_body
    assert "playback_wave_engine_tick(now_us);" in step_motion_body
    assert "playback_wave_engine_get_step_counts(step_counts);" in step_motion_body
    assert "playback_wave_engine_release_step_gpio();" in step_motion_body
    assert "pulse_engine_get_step_counts(step_counts);" not in step_motion_body

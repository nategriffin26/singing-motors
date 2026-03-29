from __future__ import annotations

from pathlib import Path


def _engine_source() -> str:
    return (
        Path(__file__).resolve().parents[1] / "firmware" / "esp32" / "src" / "playback_wave_engine.c"
    ).read_text(encoding="utf-8")


def _engine_header() -> str:
    return (
        Path(__file__).resolve().parents[1] / "firmware" / "esp32" / "src" / "playback_wave_engine.h"
    ).read_text(encoding="utf-8")


def _main_source() -> str:
    return (Path(__file__).resolve().parents[1] / "firmware" / "esp32" / "src" / "main.c").read_text(
        encoding="utf-8"
    )


def _playback_runtime_source() -> str:
    return (
        Path(__file__).resolve().parents[1] / "firmware" / "esp32" / "src" / "playback_runtime.c"
    ).read_text(encoding="utf-8")


def _motion_commands_source() -> str:
    return (
        Path(__file__).resolve().parents[1] / "firmware" / "esp32" / "src" / "motion_commands.c"
    ).read_text(encoding="utf-8")


def _pulse_engine_source() -> str:
    return (
        Path(__file__).resolve().parents[1] / "firmware" / "esp32" / "src" / "pulse_engine.c"
    ).read_text(encoding="utf-8")


def _motor_event_executor_source() -> str:
    return (
        Path(__file__).resolve().parents[1] / "firmware" / "esp32" / "src" / "motor_event_executor.c"
    ).read_text(encoding="utf-8")


def _pulse_accounting_source() -> str:
    return (
        Path(__file__).resolve().parents[1] / "firmware" / "esp32" / "src" / "pulse_accounting.c"
    ).read_text(encoding="utf-8")


def _motion_backend_header() -> str:
    return (
        Path(__file__).resolve().parents[1] / "firmware" / "esp32" / "src" / "motion_backend.h"
    ).read_text(encoding="utf-8")


def _component_cmake() -> str:
    return (
        Path(__file__).resolve().parents[1] / "firmware" / "esp32" / "src" / "CMakeLists.txt"
    ).read_text(encoding="utf-8")


def test_header_exports_continuous_playback_engine_api() -> None:
    header = _engine_header()
    assert "typedef struct {" in header
    assert "playback_wave_diag_counters_t" in header
    assert "uint32_t control_late_max_us;" in header
    assert "uint32_t wave_period_update_count;" in header
    assert "uint32_t engine_fault_mask;" in header
    assert "uint32_t engine_fault_attach_count;" in header
    assert "uint32_t engine_fault_last_reason;" in header
    assert "uint32_t playback_position_unreliable_mask;" in header
    assert "uint32_t playback_signed_position_drift_total;" in header
    assert "esp_err_t playback_wave_engine_init(" in header
    assert "uint32_t run_accel_dhz_per_s," in header
    assert "uint16_t launch_start_dhz," in header
    assert "uint32_t launch_accel_dhz_per_s," in header
    assert "uint16_t launch_crossover_dhz" in header
    assert "void playback_wave_engine_configure_profile(" in header
    assert "esp_err_t playback_wave_engine_claim_step_gpio(void);" in header
    assert "void playback_wave_engine_release_step_gpio(void);" in header
    assert "void playback_wave_engine_apply_event_group(const stream_event_group_t *event_group);" in header
    assert "void playback_wave_engine_set_one_target_exact(uint8_t motor_idx, uint16_t target_dhz, uint32_t ramp_us);" in header
    assert "void playback_wave_engine_set_one_target_exact_with_direction(" in header
    assert "void playback_wave_engine_tick(int64_t now_us);" in header
    assert "uint8_t playback_wave_engine_active_motor_count(void);" in header
    assert "void playback_wave_engine_reset_step_counts(void);" in header
    assert "void playback_wave_engine_get_step_counts(uint64_t step_counts[PLAYBACK_WAVE_MOTOR_COUNT]);" in header


def test_engine_uses_one_mcpwm_timer_operator_generator_and_comparator_per_motor() -> None:
    source = _engine_source()
    assert "static playback_wave_motor_t s_motors[PLAYBACK_WAVE_MOTOR_COUNT];" in source
    assert "mcpwm_new_timer(&timer_config, &motor->timer)" in source
    assert "mcpwm_new_operator(&operator_config, &motor->oper)" in source
    assert "mcpwm_operator_connect_timer(motor->oper, motor->timer)" in source
    assert "mcpwm_new_comparator(motor->oper, &comparator_config, &motor->comparator)" in source
    assert "mcpwm_new_generator(motor->oper, &generator_config, &motor->generator)" in source
    assert "const int group_id = (i < 3u) ? 0 : 1;" in source


def test_engine_can_claim_and_release_step_gpio_ownership() -> None:
    source = _engine_source()
    assert "static esp_err_t attach_generator_locked(uint8_t motor_idx, playback_wave_motor_t *motor) {" in source
    assert "static void detach_generator_locked(uint8_t motor_idx, playback_wave_motor_t *motor) {" in source
    assert "mcpwm_del_generator(motor->generator)" in source
    assert "gpio_set_direction(s_step_pins[motor_idx], GPIO_MODE_OUTPUT);" in source
    assert "esp_err_t playback_wave_engine_claim_step_gpio(void) {" in source
    assert "void playback_wave_engine_release_step_gpio(void) {" in source


def test_engine_generates_fixed_width_step_pulses_with_safe_boundary_updates() -> None:
    source = _engine_source()
    assert "#define PLAYBACK_WAVE_STEP_HIGH_US (4u)" in source
    assert "#define PLAYBACK_WAVE_MIN_MUSIC_DHZ (10u)" in source
    assert ".update_period_on_empty = 1" in source
    assert ".update_cmp_on_tez = 1" in source
    assert "MCPWM_TIMER_EVENT_EMPTY" in source
    assert "MCPWM_GEN_ACTION_HIGH" in source
    assert "MCPWM_GEN_ACTION_LOW" in source
    assert "mcpwm_timer_set_period(motor->timer, period_ticks)" in source
    assert "mcpwm_comparator_set_compare_value(motor->comparator, PLAYBACK_WAVE_STEP_HIGH_US)" in source


def test_engine_clamps_sub_hardware_tail_frequencies_before_period_updates() -> None:
    source = _engine_source()
    assert "static uint16_t clamp_supported_music_dhz(uint16_t music_dhz) {" in source
    assert "const uint16_t target_dhz = clamp_supported_music_dhz(change->target_dhz);" in source
    assert "if (next_dhz > 0u && next_dhz < PLAYBACK_WAVE_MIN_MUSIC_DHZ) {" in source
    assert "motor->current_dhz = 0u;" in source
    assert "motor->current_dhz = PLAYBACK_WAVE_MIN_MUSIC_DHZ;" in source


def test_engine_has_explicit_launch_run_stop_and_restart_state_machine() -> None:
    source = _engine_source()
    assert "PLAYBACK_WAVE_STATE_STOPPED" in source
    assert "PLAYBACK_WAVE_STATE_LAUNCHING" in source
    assert "PLAYBACK_WAVE_STATE_RUNNING" in source
    assert "PLAYBACK_WAVE_STATE_DECEL_TO_STOP" in source
    assert "PLAYBACK_WAVE_STATE_FLIP_WAIT_STOP" in source
    assert "PLAYBACK_WAVE_STATE_RESTART_PENDING" in source
    assert "motor->pending_restart_target_dhz = target_dhz;" in source
    assert "motor->pending_direction = (uint8_t)(motor->current_direction ^ 1u);" in source
    assert "start_launch_locked(motor_idx, motor, restart_target_dhz, restart_direction);" in source
    assert "s_diag.flip_restart_count++;" in source


def test_engine_adds_exact_mode_without_replacing_song_event_path() -> None:
    source = _engine_source()
    assert "motor->exact_mode = true;" in source
    assert "motor->pending_restart_exact_mode = true;" in source
    assert "static void apply_exact_change_locked(" in source
    assert "static void start_exact_locked(" in source
    assert "void playback_wave_engine_set_one_target_exact_with_direction(" in source
    assert "if (motor->exact_mode) {" in source
    assert "playback_wave_engine_apply_events(const motor_event_batch_t *batch)" in source


def test_engine_runs_a_low_rate_control_loop_instead_of_rmt_refill_bursts() -> None:
    source = _engine_source()
    assert "#define PLAYBACK_WAVE_DEFAULT_CONTROL_INTERVAL_US (1000u)" in source
    assert "static uint32_t s_control_interval_us = PLAYBACK_WAVE_DEFAULT_CONTROL_INTERVAL_US;" in source
    assert "void playback_wave_engine_tick(int64_t now_us) {" in source
    assert "s_diag.control_late_max_us" in source
    assert "s_diag.control_overrun_count++;" in source
    assert "tick_motor_locked(i, &s_motors[i], elapsed_us);" in source


def test_engine_tracks_engine_neutral_diagnostics() -> None:
    source = _engine_source()
    assert "s_diag.wave_period_update_count++;" in source
    assert "s_diag.motor_start_count++;" in source
    assert "s_diag.motor_stop_count++;" in source
    assert "s_diag.launch_guard_count++;" in source
    assert "s_diag.engine_fault_count++;" in source
    assert "s_diag.engine_fault_mask |= (1u << motor_idx);" in source
    assert "s_diag.engine_fault_attach_count++;" in source
    assert "s_diag.engine_fault_last_reason = (uint32_t)reason;" in source


def test_engine_tracks_playback_step_counts_without_per_step_isr() -> None:
    source = _engine_source()
    assert "static uint64_t s_motor_step_counts[PLAYBACK_WAVE_MOTOR_COUNT];" in source
    assert "const uint64_t emitted_steps = total_us / (uint64_t)motor->last_period_ticks;" in source
    assert "s_motor_step_counts[motor_idx] += emitted_steps;" in source
    assert "void playback_wave_engine_reset_step_counts(void) {" in source
    assert "void playback_wave_engine_get_step_counts(uint64_t step_counts[PLAYBACK_WAVE_MOTOR_COUNT]) {" in source


def test_start_timer_keeps_step_forced_low_until_timer_is_running() -> None:
    source = _engine_source()
    start = source.index("static void start_timer_locked")
    end = source.index("static void request_timer_stop_locked", start)
    body = source[start:end]
    assert "mcpwm_timer_start_stop(motor->timer, MCPWM_TIMER_START_NO_STOP)" in body
    assert "release_force_locked(motor_idx, motor);" in body
    assert body.index("mcpwm_timer_start_stop(motor->timer, MCPWM_TIMER_START_NO_STOP)") < body.index(
        "release_force_locked(motor_idx, motor);"
    )


def test_stop_all_flushes_and_waits_for_stop_empty_before_zeroing_state() -> None:
    source = _engine_source()
    assert "#define PLAYBACK_WAVE_STOP_SETTLE_TIMEOUT_US (10000u)" in source
    assert "playback_wave_engine_tick(esp_timer_get_time());" in source
    assert "request_timer_stop_locked(i, motor);" in source
    assert "while ((esp_timer_get_time() - settle_start_us) < (int64_t)PLAYBACK_WAVE_STOP_SETTLE_TIMEOUT_US)" in source
    assert "esp_rom_delay_us(PLAYBACK_WAVE_STOP_POLL_US);" in source
    assert "static bool any_motors_stopping_locked(void) {" in source


def test_main_routes_playback_event_groups_into_wave_engine() -> None:
    main_source = _main_source()
    runtime_source = _playback_runtime_source()
    executor_source = _motor_event_executor_source()
    assert '#include "playback_runtime.h"' in main_source
    assert "playback_runtime_init(" in main_source
    assert 'xTaskCreate(playback_runtime_task, "playback_task"' in main_source
    assert "motor_event_executor_from_stream_event_group(&s_pending_event_group, &batch)" in runtime_source
    assert "motor_event_executor_apply(&batch);" in runtime_source
    assert "playback_wave_engine_apply_events(batch);" in executor_source
    assert "playback_wave_engine_tick(now_us);" in runtime_source


def test_main_hands_step_pin_ownership_between_playback_and_exact_step_commands() -> None:
    main_source = _main_source()
    runtime_source = _playback_runtime_source()
    motion_source = _motion_commands_source()
    assert "playback_runtime_start(scheduled_start_device_us);" in main_source
    assert "playback_wave_engine_claim_step_gpio()" in runtime_source
    assert "playback_wave_engine_release_step_gpio();" in runtime_source
    assert motion_source.count("playback_wave_engine_release_step_gpio();") >= 2
    assert "playback_wave_engine_claim_step_gpio()" in motion_source


def test_playback_runtime_does_not_stop_exact_motion_backend_when_song_is_idle() -> None:
    runtime_source = _playback_runtime_source()
    service_start = runtime_source.index("static void playback_service_runtime(void) {")
    stop_start = runtime_source.index("void playback_runtime_task(void *arg) {")
    service_body = runtime_source[service_start:stop_start]
    assert "playback_wave_engine_stop_all();" not in service_body
    assert "playback_wave_engine_reset();" not in service_body
    assert "pulse_engine_stop_all();" not in service_body


def test_main_home_uses_combined_exact_step_and_playback_position_counts() -> None:
    source = _motion_commands_source()
    assert "int64_t pulse_positions[MOTOR_COUNT] = {0};" in source
    assert "int64_t playback_positions[PLAYBACK_MOTOR_COUNT] = {0};" in source
    assert "playback_wave_engine_get_position_counts(playback_positions);" in source
    assert "start_counts[i] += playback_positions[i];" in source
    assert "playback_wave_engine_reset_step_counts();" in source


def test_main_accepts_setup_payload_with_optional_playback_profile_tail() -> None:
    source = _main_source()
    assert "(frame->payload_len != SETUP_BASE_PAYLOAD_SIZE)" in source
    assert "SETUP_WITH_PLAYBACK_PROFILE_PAYLOAD_SIZE" in source
    assert "SETUP_WITH_SPEECH_ASSIST_PAYLOAD_SIZE" in source
    assert "next_run_accel_dhz_per_s = proto_read_le32(&frame->payload[5]);" in source
    assert "next_launch_start_dhz = proto_read_le16(&frame->payload[9]);" in source
    assert "next_launch_accel_dhz_per_s = proto_read_le32(&frame->payload[11]);" in source
    assert "next_launch_crossover_dhz = proto_read_le16(&frame->payload[15]);" in source
    assert "speech_control_interval_us = proto_read_le16(&frame->payload[17]);" in source
    assert "speech_release_accel_dhz_per_s = proto_read_le32(&frame->payload[19]);" in source
    assert "playback_wave_engine_configure_speech_assist(" in source
    assert "playback_wave_engine_configure_profile(" in source


def test_engine_exports_speech_assist_controls() -> None:
    header = _engine_header()
    source = _engine_source()
    assert "void playback_wave_engine_configure_speech_assist(" in header
    assert "static bool s_speech_assist_enabled = false;" in source
    assert "static uint32_t s_release_accel_dhz_per_s = 50000u;" in source
    assert "accel_dhz_per_s = s_release_accel_dhz_per_s;" in source
    assert "return s_control_interval_us;" in source


def test_main_metrics_payload_surfaces_continuous_engine_counters() -> None:
    source = _main_source()
    assert "uint8_t payload[136] = {0};" in source
    assert "proto_write_le32(&payload[40], playback_diag.control_late_max_us);" in source
    assert "proto_write_le32(&payload[44], playback_diag.control_overrun_count);" in source
    assert "proto_write_le32(&payload[48], playback_diag.wave_period_update_count);" in source
    assert "proto_write_le32(&payload[52], playback_diag.motor_start_count);" in source
    assert "proto_write_le32(&payload[56], playback_diag.motor_stop_count);" in source
    assert "proto_write_le32(&payload[60], playback_diag.flip_restart_count);" in source
    assert "proto_write_le32(&payload[64], playback_diag.launch_guard_count);" in source
    assert "proto_write_le32(&payload[68], playback_diag.engine_fault_count);" in source
    assert "proto_write_le32(&payload[72], playback_diag.engine_fault_mask);" in source
    assert "proto_write_le32(&payload[76], playback_diag.engine_fault_attach_count);" in source
    assert "proto_write_le32(&payload[100], playback_diag.engine_fault_last_reason);" in source
    assert "proto_write_le32(&payload[108], playback_diag.inferred_pulse_total);" in source
    assert "proto_write_le32(&payload[112], playback_diag.measured_pulse_total);" in source
    assert "proto_write_le32(&payload[116], playback_diag.measured_pulse_drift_total);" in source
    assert "proto_write_le32(&payload[120], playback_diag.measured_pulse_active_mask);" in source
    assert "proto_write_le32(&payload[124], exact_diag.pulse_position_lost_mask);" in source
    assert "proto_write_le32(&payload[128], playback_diag.playback_position_unreliable_mask);" in source
    assert "proto_write_le32(&payload[132], playback_diag.playback_signed_position_drift_total);" in source


def test_runtime_uses_timer_driven_boundary_and_control_notifications() -> None:
    source = _playback_runtime_source()
    assert "static esp_timer_handle_t s_boundary_timer = NULL;" in source
    assert "static esp_timer_handle_t s_control_timer = NULL;" in source
    assert "static stream_event_group_t s_pending_event_group = {0};" in source
    assert "static void playback_boundary_timer_cb(void *arg) {" in source
    assert "static void playback_control_timer_cb(void *arg) {" in source
    assert "esp_timer_start_periodic(s_control_timer, playback_wave_engine_control_interval_us())" in source
    assert "playback_notify_runtime_from_timer(PLAYBACK_RUNTIME_NOTIFY_BOUNDARY);" in source
    assert "playback_notify_runtime_from_timer(PLAYBACK_RUNTIME_NOTIFY_CONTROL);" in source


def test_runtime_no_longer_leaves_scheduler_and_motion_loops_in_main() -> None:
    source = _main_source()
    assert "static void playback_timer_cb(void *arg)" not in source
    assert "static void playback_task(void *arg)" not in source
    assert "return motion_commands_warmup(frame, state_get_snapshot);" in source
    assert "return motion_commands_step_motion(frame, state_get_snapshot);" in source


def test_engines_export_signed_position_accounting_for_alignment() -> None:
    wave_header = _engine_header()
    wave_source = _engine_source()
    pulse_source = _pulse_engine_source()
    motion_source = _motion_commands_source()
    assert "void playback_wave_engine_get_position_counts(" in wave_header
    assert "static int64_t s_motor_position_counts[PLAYBACK_WAVE_MOTOR_COUNT];" in wave_source
    assert "void pulse_engine_get_position_counts(" in pulse_source
    assert "playback_wave_engine_get_position_counts(playback_positions);" in motion_source
    assert "pulse_engine_get_position_counts(pulse_positions);" in motion_source


def test_executor_and_backends_make_backend_split_explicit() -> None:
    executor_source = _motor_event_executor_source()
    backend_header = _motion_backend_header()
    pulse_accounting_source = _pulse_accounting_source()
    assert "MOTION_BACKEND_KIND_PLAYBACK_WAVE" in backend_header
    assert "MOTION_BACKEND_KIND_PULSE_EXACT" in backend_header
    assert "motion_backend_playback_capabilities" in backend_header
    assert "motion_backend_exact_capabilities" in backend_header
    assert "playback_wave_engine_apply_events(batch);" in executor_source
    assert "pulse_engine_set_one_target_exact" in executor_source
    assert '#include "driver/pulse_cnt.h"' in pulse_accounting_source
    assert "pcnt_new_unit(&unit_config, &channel->unit)" in pulse_accounting_source
    assert ".level_gpio_num = channel->dir_pin" in pulse_accounting_source
    assert "PCNT_CHANNEL_LEVEL_ACTION_INVERSE" in pulse_accounting_source


def test_step_motion_orchestrates_playback_wave_exact_commands_separately_from_song_batches() -> None:
    motion_source = _motion_commands_source()
    executor_source = _motor_event_executor_source()
    assert "step_motion_apply_playback_target_with_direction(" in motion_source
    assert "playback_wave_engine_set_one_target_exact_with_direction(motor_idx, target_dhz, ramp_us, direction);" in motion_source
    assert "playback_wave_engine_apply_events(batch);" in executor_source


def test_pulse_accounting_uses_dir_signed_measurement_for_playback_position() -> None:
    pulse_accounting_source = _pulse_accounting_source()
    engine_source = _engine_source()
    assert "channel->measured_position_count += (int64_t)delta;" in pulse_accounting_source
    assert "channel->inferred_position_count += (int64_t)emitted_steps;" in pulse_accounting_source
    assert "channel->inferred_position_count -= (int64_t)emitted_steps;" in pulse_accounting_source
    assert "void pulse_accounting_get_measured_positions(" in pulse_accounting_source
    assert "if (pulse_accounting_has_session_data()) {" in engine_source
    assert "pulse_accounting_get_measured_positions(position_counts, PLAYBACK_WAVE_MOTOR_COUNT);" in engine_source
    assert "pcnt_unit_get_count(channel->unit, &raw_count)" in pulse_accounting_source


def test_component_build_includes_mcpwm_driver() -> None:
    cmake = _component_cmake()
    assert '"playback_wave_engine.c"' in cmake
    assert "esp_driver_mcpwm" in cmake

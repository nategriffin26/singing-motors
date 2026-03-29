#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"

#include "motion_backend.h"
#include "motor_event_executor.h"
#include "stream_queue.h"

#define PLAYBACK_WAVE_MOTOR_COUNT (6u)

typedef struct {
  uint32_t control_late_max_us;
  uint32_t control_overrun_count;
  uint32_t wave_period_update_count;
  uint32_t motor_start_count;
  uint32_t motor_stop_count;
  uint32_t flip_restart_count;
  uint32_t launch_guard_count;
  uint32_t engine_fault_count;
  uint32_t engine_fault_mask;
  uint32_t engine_fault_attach_count;
  uint32_t engine_fault_detach_count;
  uint32_t engine_fault_period_count;
  uint32_t engine_fault_force_count;
  uint32_t engine_fault_timer_count;
  uint32_t engine_fault_invalid_change_count;
  uint32_t engine_fault_last_reason;
  uint32_t engine_fault_last_motor;
  uint32_t inferred_pulse_total;
  uint32_t measured_pulse_total;
  uint32_t measured_pulse_drift_total;
  uint32_t measured_pulse_active_mask;
  uint32_t playback_position_unreliable_mask;
  uint32_t playback_signed_position_drift_total;
} playback_wave_diag_counters_t;

typedef struct {
  uint16_t current_dhz;
  uint16_t target_dhz;
  uint16_t final_target_dhz;
  uint32_t exact_accel_dhz_per_s;
  uint32_t last_period_ticks;
  uint32_t pulse_accum_us;
  uint8_t state;
  uint8_t current_direction;
  uint8_t timer_running;
  uint8_t stop_requested;
  uint8_t exact_mode;
} playback_wave_motor_debug_t;

esp_err_t playback_wave_engine_init(
  uint32_t run_accel_dhz_per_s,
  uint16_t launch_start_dhz,
  uint32_t launch_accel_dhz_per_s,
  uint16_t launch_crossover_dhz
);
void playback_wave_engine_configure_profile(
  uint32_t run_accel_dhz_per_s,
  uint16_t launch_start_dhz,
  uint32_t launch_accel_dhz_per_s,
  uint16_t launch_crossover_dhz
);
void playback_wave_engine_configure_speech_assist(
  bool enabled,
  uint16_t control_interval_us,
  uint32_t release_accel_dhz_per_s
);
esp_err_t playback_wave_engine_claim_step_gpio(void);
void playback_wave_engine_release_step_gpio(void);
void playback_wave_engine_reset(void);
void playback_wave_engine_note_stop_reason(uint8_t reason);
uint8_t playback_wave_engine_last_stop_reason(void);
void playback_wave_engine_stop_all(void);
void playback_wave_engine_apply_event_group(const stream_event_group_t *event_group);
void playback_wave_engine_apply_events(const motor_event_batch_t *batch);
void playback_wave_engine_set_one_target_exact(uint8_t motor_idx, uint16_t target_dhz, uint32_t ramp_us);
void playback_wave_engine_set_one_target_exact_with_direction(
  uint8_t motor_idx,
  uint16_t target_dhz,
  uint32_t ramp_us,
  uint8_t direction
);
void playback_wave_engine_tick(int64_t now_us);
uint8_t playback_wave_engine_active_motor_count(void);
void playback_wave_engine_reset_step_counts(void);
void playback_wave_engine_get_step_counts(uint64_t step_counts[PLAYBACK_WAVE_MOTOR_COUNT]);
void playback_wave_engine_get_position_counts(int64_t position_counts[PLAYBACK_WAVE_MOTOR_COUNT]);
void playback_wave_engine_reset_diag_counters(void);
void playback_wave_engine_get_diag_counters(playback_wave_diag_counters_t *diag);
void playback_wave_engine_get_motor_debug(uint8_t motor_idx, playback_wave_motor_debug_t *debug);
uint32_t playback_wave_engine_control_interval_us(void);
motion_backend_capabilities_t playback_wave_engine_capabilities(void);

#pragma once

#include <stdint.h>

#include "esp_err.h"

#include "motion_backend.h"
#include "protocol_defs.h"

#ifndef MUSIC2_PULSE_ENGINE_MIN_HALF_PERIOD_US
#define MUSIC2_PULSE_ENGINE_MIN_HALF_PERIOD_US (100u)
#endif

#define PULSE_ENGINE_MIN_HALF_PERIOD_US (MUSIC2_PULSE_ENGINE_MIN_HALF_PERIOD_US)
#define PULSE_ENGINE_TIMER_RESOLUTION_HZ (1000000u)
// Kept for protocol/UI compatibility as reported scheduler resolution floor.
#define PULSE_ENGINE_TICK_US (PULSE_ENGINE_MIN_HALF_PERIOD_US)

/* Maximum acceleration rate in deci-Hz per second.  Each motor independently
 * ramps at up to this rate.  The ramp duration is computed automatically from
 * the frequency delta so the motor reaches its target as fast as possible
 * without skipping steps.
 *
 * 100000 dHz/s = 10,000 Hz/s.  A 500 Hz jump completes in ~50 ms. */
#ifndef MUSIC2_PULSE_ENGINE_MAX_ACCEL_DHZ_PER_S
#define MUSIC2_PULSE_ENGINE_MAX_ACCEL_DHZ_PER_S (100000u)
#endif
#define PULSE_ENGINE_MAX_ACCEL_DHZ_PER_S (MUSIC2_PULSE_ENGINE_MAX_ACCEL_DHZ_PER_S)

esp_err_t pulse_engine_init(void);

/* Set target frequencies.  Each motor independently computes the fastest
 * safe ramp based on the frequency delta and PULSE_ENGINE_MAX_ACCEL_DHZ_PER_S.
 * The ramp is capped to max_ramp_us (typically the segment duration) so motors
 * never overshoot the segment boundary.  If max_ramp_us is 0, frequencies
 * change instantly. */
void pulse_engine_set_targets(const uint16_t freq_dhz[MOTOR_COUNT], uint32_t max_ramp_us);
void pulse_engine_set_targets_with_flips(
  const uint16_t freq_dhz[MOTOR_COUNT],
  uint8_t direction_flip_mask,
  uint32_t max_ramp_us
);

/* Update a single motor's target without disturbing any other motor.
 * motor_idx must be in [0, MOTOR_COUNT).  Semantics for freq_dhz and
 * max_ramp_us are identical to pulse_engine_set_targets.  Use this from
 * warmup and homing loops that need independent per-motor ramp durations. */
void pulse_engine_set_one_target(uint8_t motor_idx, uint16_t freq_dhz, uint32_t max_ramp_us);

/* Update a single motor's target and treat ramp_us as an exact ramp duration
 * (not a cap).  Used by warmup/step-motion flows that need deterministic
 * accel/decel envelopes. */
void pulse_engine_set_one_target_exact(uint8_t motor_idx, uint16_t freq_dhz, uint32_t ramp_us);
void pulse_engine_set_one_target_with_direction(
  uint8_t motor_idx,
  uint16_t freq_dhz,
  uint32_t max_ramp_us,
  uint8_t direction
);
void pulse_engine_set_one_target_exact_with_direction(
  uint8_t motor_idx,
  uint16_t freq_dhz,
  uint32_t ramp_us,
  uint8_t direction
);

/* Instantly stop all motors (set all frequencies to zero, no ramp). */
void pulse_engine_stop_all(void);

void pulse_engine_reset_step_counts(void);
void pulse_engine_get_step_counts(uint64_t step_counts[MOTOR_COUNT]);
void pulse_engine_get_position_counts(int64_t position_counts[MOTOR_COUNT]);
void pulse_engine_reset_diag_counters(void);

typedef struct {
  uint32_t pulse_late_max_us;
  uint32_t pulse_edge_drop_count;
  uint32_t pulse_timebase_rebase_count;
  uint32_t pulse_timebase_rebase_lost_us;
  uint32_t pulse_target_update_count;
  uint32_t pulse_ramp_change_count;
  uint32_t pulse_stop_after_ramp_count;
  uint32_t pulse_position_lost_mask;
} pulse_engine_diag_counters_t;

typedef struct {
  uint32_t half_period_us;
  uint32_t target_half_period_us;
  uint32_t accum_us;
  uint32_t ramp_total_us;
  uint32_t ramp_elapsed_us;
  uint32_t last_requested_ramp_us;
  uint16_t last_requested_freq_dhz;
  uint8_t level;
  uint8_t current_direction;
  uint8_t stop_after_ramp;
  uint8_t last_requested_exact;
} pulse_engine_motor_debug_t;

void pulse_engine_get_diag_counters(pulse_engine_diag_counters_t *diag);
void pulse_engine_get_motor_debug(uint8_t motor_idx, pulse_engine_motor_debug_t *debug);
motion_backend_capabilities_t pulse_engine_capabilities(void);

#include "motion_commands.h"

#include <math.h>
#include <stdbool.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdint.h>
#include <string.h>

#include "esp_rom_sys.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "playback_wave_engine.h"
#include "protocol_codec.h"
#include "pulse_engine.h"

#ifndef MUSIC2_SAFE_MAX_FREQ_DHZ
#define MUSIC2_SAFE_MAX_FREQ_DHZ (8000u)
#endif
#define PLAYBACK_SAFE_MAX_FREQ_DHZ (MUSIC2_SAFE_MAX_FREQ_DHZ)

#define HOME_STALL_TIMEOUT_US (2000000LL)
#define HOME_POLL_INTERVAL_MS (2u)
#define HOME_SETTLE_DELAY_MS (1u)
#define HOME_FINE_WINDOW_STEPS (48u)
#define HOME_FINE_POLL_US (250u)

#define WARMUP_POLL_MS        (2u)
#define WARMUP_MAX_RUNTIME_MS (30000u)
#define WARMUP_MAX_PHASES     (4u)
#define WARMUP_PHASE_SIZE     (8u)
#define WARMUP_MOTOR_HDR_SIZE (6u)
#define WARMUP_NO_TRIGGER     (0xFFu)

#define STEP_MOTION_POLL_MS        (2u)
#define STEP_MOTION_MAX_RUNTIME_MS (120000u)
#define STEP_MOTION_MAX_PHASES     (8u)
#define STEP_MOTION_PHASE_SIZE     (11u)
#define STEP_MOTION_MOTOR_HDR_SIZE (6u)
#define STEP_MOTION_NO_TRIGGER     (0xFFu)
#define STEP_MOTION_PHASE_FLAG_REVERSE (0x01u)
#define STEP_MOTION_MICROSTEP_RATIO (16u)
#define STEP_MOTION_MIN_MECHANICAL_DHZ  (150u)
#define STEP_MOTION_FINE_FREQ_DHZ  (STEP_MOTION_MIN_MECHANICAL_DHZ)
#define STEP_MOTION_STALL_TIMEOUT_US (3000000LL)
#define STEP_MOTION_FINE_WINDOW_STEPS (48u)
#define STEP_MOTION_FINE_POLL_US (250u)

typedef struct {
  uint16_t peak_dhz;
  uint16_t accel_dhz_per_s;
  uint16_t decel_dhz_per_s;
  uint16_t hold_ms;
} warmup_phase_t;

typedef struct {
  uint16_t start_delay_ms;
  uint8_t trigger_motor;
  uint16_t trigger_steps;
  uint8_t phase_count;
  warmup_phase_t phases[WARMUP_MAX_PHASES];
  uint8_t current_phase;
  bool triggered;
  uint64_t phase_start_count;
  uint32_t phase_elapsed_ms;
  bool in_decel;
} warmup_motor_state_t;

typedef struct {
  uint8_t direction;
  uint16_t target_steps;
  uint16_t peak_dhz;
  uint16_t accel_dhz_per_s;
  uint16_t decel_dhz_per_s;
  uint16_t hold_ms;
} step_motion_phase_t;

typedef struct {
  uint16_t start_delay_ms;
  uint8_t trigger_motor;
  uint16_t trigger_steps;
  uint8_t phase_count;
  step_motion_phase_t phases[STEP_MOTION_MAX_PHASES];
  uint8_t current_phase;
  bool triggered;
  uint64_t phase_start_count;
} step_motion_motor_state_t;

typedef struct {
  uint16_t launch_dhz;
  uint16_t cruise_dhz;
  uint16_t fine_dhz;
  uint32_t accel_ramp_us;
  uint32_t decel_ramp_us;
  uint32_t stop_ramp_us;
  uint32_t decel_start_steps;
  uint32_t stop_start_steps;
} step_motion_phase_plan_t;

static char s_motion_error_detail[96];

static void motion_commands_clear_error_detail(void) {
  s_motion_error_detail[0] = '\0';
}

static void motion_commands_set_error_detail(const char *fmt, ...) {
  if (fmt == NULL) {
    motion_commands_clear_error_detail();
    return;
  }
  va_list args;
  va_start(args, fmt);
  (void)vsnprintf(s_motion_error_detail, sizeof(s_motion_error_detail), fmt, args);
  va_end(args);
}

static TickType_t delay_ticks_from_ms(uint32_t ms) {
  if (ms == 0u) {
    return 0;
  }
  const TickType_t ticks = pdMS_TO_TICKS(ms);
  return (ticks == 0) ? 1 : ticks;
}

static bool runtime_busy(motion_state_snapshot_fn state_get_snapshot) {
  const runtime_state_t snap = state_get_snapshot();
  return snap.playing || snap.stream_open;
}

static void motion_apply_exact_target(uint8_t motor_idx, uint16_t target_dhz, uint32_t ramp_us, bool exact_ramp) {
  if (motor_idx >= MOTOR_COUNT) {
    return;
  }
  if (exact_ramp) {
    pulse_engine_set_one_target_exact(motor_idx, target_dhz, ramp_us);
  } else {
    pulse_engine_set_one_target(motor_idx, target_dhz, ramp_us);
  }
}

static void step_motion_apply_playback_target_with_direction(
  uint8_t motor_idx,
  uint16_t target_dhz,
  uint32_t ramp_us,
  uint8_t direction
) {
  if (motor_idx >= PLAYBACK_MOTOR_COUNT) {
    return;
  }
  playback_wave_engine_set_one_target_exact_with_direction(motor_idx, target_dhz, ramp_us, direction);
}

static runtime_err_t home_one_motor(
  uint8_t motor_idx,
  uint16_t remaining_steps,
  uint16_t start_freq_dhz,
  uint16_t target_freq_dhz,
  uint16_t accel_hz_per_s_dhz
) {
  if (motor_idx >= MOTOR_COUNT) {
    return ERR_BAD_PAYLOAD;
  }
  if (remaining_steps == 0u) {
    return ERR_OK;
  }

  uint16_t current_freq_dhz = start_freq_dhz;
  if ((current_freq_dhz == 0u) || (current_freq_dhz > target_freq_dhz)) {
    current_freq_dhz = target_freq_dhz;
  }

  motion_apply_exact_target(motor_idx, current_freq_dhz, 0u, false);

  uint64_t counts[MOTOR_COUNT] = {0};
  pulse_engine_get_step_counts(counts);
  const uint64_t start_count = counts[motor_idx];
  uint64_t last_count = start_count;
  bool fine_mode = false;

  int64_t last_progress_us = esp_timer_get_time();
  int64_t last_ramp_us = last_progress_us;

  while (true) {
    if (fine_mode) {
      esp_rom_delay_us(HOME_FINE_POLL_US);
    } else {
      vTaskDelay(delay_ticks_from_ms(HOME_POLL_INTERVAL_MS));
    }
    pulse_engine_get_step_counts(counts);
    const uint64_t current_count = counts[motor_idx];
    const uint64_t advanced_steps = current_count - start_count;
    const int64_t now_us = esp_timer_get_time();

    if (current_count > last_count) {
      last_progress_us = now_us;
    }
    last_count = current_count;

    if (advanced_steps >= (uint64_t)remaining_steps) {
      break;
    }

    const uint64_t steps_remaining = (uint64_t)remaining_steps - advanced_steps;
    if (!fine_mode && steps_remaining <= (uint64_t)HOME_FINE_WINDOW_STEPS) {
      fine_mode = true;
      if (current_freq_dhz > start_freq_dhz) {
        current_freq_dhz = start_freq_dhz;
        motion_apply_exact_target(motor_idx, current_freq_dhz, 75000u, true);
      }
    }

    if (!fine_mode && (accel_hz_per_s_dhz > 0u) && (current_freq_dhz < target_freq_dhz)) {
      const int64_t accel_elapsed_us = now_us - last_ramp_us;
      if (accel_elapsed_us > 0) {
        uint64_t delta_freq = ((uint64_t)accel_elapsed_us * (uint64_t)accel_hz_per_s_dhz) / 1000000u;
        if (delta_freq > 0u) {
          uint32_t next_freq = (uint32_t)current_freq_dhz + (uint32_t)delta_freq;
          if (next_freq > (uint32_t)target_freq_dhz) {
            next_freq = target_freq_dhz;
          }
          current_freq_dhz = (uint16_t)next_freq;
          motion_apply_exact_target(motor_idx, current_freq_dhz, 0u, false);
          last_ramp_us = now_us;
        }
      }
    }

    if ((now_us - last_progress_us) > HOME_STALL_TIMEOUT_US) {
      pulse_engine_stop_all();
      return ERR_INTERNAL;
    }
  }

  motion_apply_exact_target(motor_idx, 0u, 0u, false);
  vTaskDelay(delay_ticks_from_ms(HOME_SETTLE_DELAY_MS));
  return ERR_OK;
}

runtime_err_t motion_commands_home(
  const proto_frame_t *frame,
  uint8_t configured_motors,
  motion_state_snapshot_fn state_get_snapshot
) {
  motion_commands_clear_error_detail();
  if ((frame->payload_len != 4u) && (frame->payload_len != 8u)) {
    return ERR_BAD_PAYLOAD;
  }
  if (runtime_busy(state_get_snapshot)) {
    return ERR_BAD_STATE;
  }

  playback_wave_engine_note_stop_reason(6u);
  playback_wave_engine_stop_all();
  playback_wave_engine_release_step_gpio();

  const uint16_t steps_per_rev = proto_read_le16(&frame->payload[0]);
  uint16_t home_start_freq_dhz = 0u;
  uint16_t home_freq_dhz = 0u;
  uint16_t home_accel_hz_per_s_dhz = 0u;
  if (frame->payload_len == 4u) {
    home_freq_dhz = proto_read_le16(&frame->payload[2]);
    home_start_freq_dhz = home_freq_dhz;
  } else {
    home_start_freq_dhz = proto_read_le16(&frame->payload[2]);
    home_freq_dhz = proto_read_le16(&frame->payload[4]);
    home_accel_hz_per_s_dhz = proto_read_le16(&frame->payload[6]);
  }

  if ((steps_per_rev == 0u) || (home_start_freq_dhz == 0u) || (home_freq_dhz == 0u)) {
    return ERR_BAD_PAYLOAD;
  }
  if (home_start_freq_dhz > home_freq_dhz) {
    return ERR_BAD_PAYLOAD;
  }
  if ((home_start_freq_dhz > PLAYBACK_SAFE_MAX_FREQ_DHZ) || (home_freq_dhz > PLAYBACK_SAFE_MAX_FREQ_DHZ)) {
    return ERR_BAD_PAYLOAD;
  }
  if ((configured_motors == 0u) || (configured_motors > MOTOR_COUNT)) {
    return ERR_BAD_STATE;
  }

  int64_t pulse_positions[MOTOR_COUNT] = {0};
  int64_t playback_positions[PLAYBACK_MOTOR_COUNT] = {0};
  int64_t start_counts[MOTOR_COUNT] = {0};
  pulse_engine_get_position_counts(pulse_positions);
  playback_wave_engine_get_position_counts(playback_positions);
  for (uint8_t i = 0; i < configured_motors; ++i) {
    start_counts[i] = pulse_positions[i];
    if (i < PLAYBACK_MOTOR_COUNT) {
      start_counts[i] += playback_positions[i];
    }
  }

  for (uint8_t i = 0; i < configured_motors; ++i) {
    int64_t remainder = start_counts[i] % (int64_t)steps_per_rev;
    if (remainder < 0) {
      remainder += (int64_t)steps_per_rev;
    }
    const uint16_t remaining_steps = (remainder == 0u) ? 0u : (uint16_t)(steps_per_rev - remainder);
    if (remaining_steps == 0u) {
      continue;
    }

    const runtime_err_t home_err = home_one_motor(
      i,
      remaining_steps,
      home_start_freq_dhz,
      home_freq_dhz,
      home_accel_hz_per_s_dhz
    );
    if (home_err != ERR_OK) {
      pulse_engine_stop_all();
      return home_err;
    }
  }

  pulse_engine_stop_all();
  pulse_engine_reset_step_counts();
  playback_wave_engine_reset_step_counts();
  return ERR_OK;
}

runtime_err_t motion_commands_warmup(const proto_frame_t *frame, motion_state_snapshot_fn state_get_snapshot) {
  motion_commands_clear_error_detail();
  if (runtime_busy(state_get_snapshot)) {
    return ERR_BAD_STATE;
  }
  if (frame->payload_len < 2u) {
    return ERR_BAD_PAYLOAD;
  }

  const uint8_t motor_count = frame->payload[0];
  if (motor_count == 0u || motor_count > MOTOR_COUNT) {
    return ERR_BAD_PAYLOAD;
  }

  warmup_motor_state_t motors[MOTOR_COUNT];
  memset(motors, 0, sizeof(motors));

  const uint8_t *cursor = &frame->payload[1];
  const uint8_t *payload_end = &frame->payload[frame->payload_len];

  for (uint8_t i = 0u; i < motor_count; ++i) {
    if (cursor + WARMUP_MOTOR_HDR_SIZE > payload_end) {
      return ERR_BAD_PAYLOAD;
    }
    motors[i].start_delay_ms = proto_read_le16(&cursor[0]);
    motors[i].trigger_motor = cursor[2];
    motors[i].trigger_steps = proto_read_le16(&cursor[3]);
    motors[i].phase_count = cursor[5];
    cursor += WARMUP_MOTOR_HDR_SIZE;

    if (motors[i].phase_count == 0u || motors[i].phase_count > WARMUP_MAX_PHASES) {
      return ERR_BAD_PAYLOAD;
    }
    if (motors[i].trigger_motor != WARMUP_NO_TRIGGER && motors[i].trigger_motor >= MOTOR_COUNT) {
      return ERR_BAD_PAYLOAD;
    }

    for (uint8_t ph = 0u; ph < motors[i].phase_count; ++ph) {
      if (cursor + WARMUP_PHASE_SIZE > payload_end) {
        return ERR_BAD_PAYLOAD;
      }
      motors[i].phases[ph].peak_dhz = proto_read_le16(&cursor[0]);
      motors[i].phases[ph].accel_dhz_per_s = proto_read_le16(&cursor[2]);
      motors[i].phases[ph].decel_dhz_per_s = proto_read_le16(&cursor[4]);
      motors[i].phases[ph].hold_ms = proto_read_le16(&cursor[6]);
      cursor += WARMUP_PHASE_SIZE;

      if (motors[i].phases[ph].peak_dhz > PLAYBACK_SAFE_MAX_FREQ_DHZ) {
        return ERR_BAD_PAYLOAD;
      }
      if (motors[i].phases[ph].peak_dhz > 0u && motors[i].phases[ph].decel_dhz_per_s == 0u) {
        return ERR_BAD_PAYLOAD;
      }
    }

    motors[i].current_phase = 0u;
    motors[i].triggered = (motors[i].trigger_motor == WARMUP_NO_TRIGGER);
    motors[i].phase_start_count = 0u;
    motors[i].phase_elapsed_ms = 0u;
    motors[i].in_decel = false;
  }

  playback_wave_engine_note_stop_reason(9u);
  playback_wave_engine_stop_all();
  playback_wave_engine_release_step_gpio();
  pulse_engine_stop_all();

  uint64_t motor_phase0_start_counts[MOTOR_COUNT] = {0u};
  bool motor_phase0_count_captured[MOTOR_COUNT];
  memset(motor_phase0_count_captured, 0, sizeof(motor_phase0_count_captured));

  uint8_t sub_state[MOTOR_COUNT];
  int64_t phase_transition_us[MOTOR_COUNT];
  memset(sub_state, 0, sizeof(sub_state));
  memset(phase_transition_us, 0, sizeof(phase_transition_us));

  const int64_t cmd_start_us = esp_timer_get_time();

  while (true) {
    esp_rom_delay_us(WARMUP_POLL_MS * 1000u);

    const int64_t now_us = esp_timer_get_time();
    const int64_t elapsed_us_64 = now_us - cmd_start_us;
    if (elapsed_us_64 < 0) {
      break;
    }
    const uint32_t elapsed_ms = (uint32_t)(elapsed_us_64 / 1000LL);
    if (elapsed_ms > WARMUP_MAX_RUNTIME_MS) {
      break;
    }

    uint64_t step_counts[MOTOR_COUNT] = {0u};
    pulse_engine_get_step_counts(step_counts);

    bool any_active = false;

    for (uint8_t i = 0u; i < motor_count; ++i) {
      warmup_motor_state_t *m = &motors[i];

      if (m->current_phase >= m->phase_count) {
        continue;
      }

      if (elapsed_ms < (uint32_t)m->start_delay_ms) {
        any_active = true;
        continue;
      }

      if (!m->triggered) {
        const uint8_t tm = m->trigger_motor;
        if (motor_phase0_count_captured[tm]) {
          const uint64_t relative = step_counts[tm] - motor_phase0_start_counts[tm];
          if (relative >= (uint64_t)m->trigger_steps) {
            m->triggered = true;
          }
        }
        if (!m->triggered) {
          any_active = true;
          continue;
        }
      }

      if (!motor_phase0_count_captured[i]) {
        motor_phase0_start_counts[i] = step_counts[i];
        motor_phase0_count_captured[i] = true;
      }

      any_active = true;

      const warmup_phase_t *ph = &m->phases[m->current_phase];

      if (ph->peak_dhz == 0u) {
        if (sub_state[i] == 0u) {
          sub_state[i] = 2u;
          phase_transition_us[i] = now_us;
        }
        const int64_t idle_elapsed_ms = (now_us - phase_transition_us[i]) / 1000LL;
        if (idle_elapsed_ms >= (int64_t)ph->hold_ms) {
          m->current_phase++;
          sub_state[i] = 0u;
        }
        continue;
      }

      const uint32_t accel_us = (ph->accel_dhz_per_s > 0u)
          ? (uint32_t)(((uint64_t)ph->peak_dhz * 1000000ULL) / (uint64_t)ph->accel_dhz_per_s)
          : 0u;
      const uint32_t hold_us = (uint32_t)ph->hold_ms * 1000u;
      const uint32_t decel_us = (ph->decel_dhz_per_s > 0u)
          ? (uint32_t)(((uint64_t)ph->peak_dhz * 1000000ULL) / (uint64_t)ph->decel_dhz_per_s)
          : 0u;

      if (sub_state[i] == 0u) {
        motion_apply_exact_target(i, ph->peak_dhz, accel_us, true);
        sub_state[i] = 1u;
        phase_transition_us[i] = now_us;

      } else if (sub_state[i] == 1u) {
        if ((now_us - phase_transition_us[i]) >= (int64_t)accel_us) {
          motion_apply_exact_target(i, ph->peak_dhz, 0u, true);
          sub_state[i] = 2u;
          phase_transition_us[i] = now_us;
        }

      } else if (sub_state[i] == 2u) {
        if ((now_us - phase_transition_us[i]) >= (int64_t)hold_us) {
          motion_apply_exact_target(i, 0u, decel_us, true);
          sub_state[i] = 3u;
          phase_transition_us[i] = now_us;
        }

      } else if (sub_state[i] == 3u) {
        if ((now_us - phase_transition_us[i]) >= (int64_t)decel_us) {
          motion_apply_exact_target(i, 0u, 0u, true);
          m->current_phase++;
          sub_state[i] = 0u;
        }
      }
    }

    if (!any_active) {
      break;
    }
  }

  pulse_engine_stop_all();
  return ERR_OK;
}

static uint32_t step_motion_ramp_steps_between(uint16_t from_dhz, uint16_t to_dhz, uint16_t rate_dhz_per_s) {
  if (from_dhz == 0u || rate_dhz_per_s == 0u || to_dhz >= from_dhz) {
    return 0u;
  }
  const uint64_t from_sq = (uint64_t)from_dhz * (uint64_t)from_dhz;
  const uint64_t to_sq = (uint64_t)to_dhz * (uint64_t)to_dhz;
  const uint64_t num = (from_sq - to_sq) * (uint64_t)STEP_MOTION_MICROSTEP_RATIO;
  const uint64_t den = 20u * (uint64_t)rate_dhz_per_s;
  if (den == 0u) {
    return 0u;
  }
  uint64_t steps = (num + den - 1u) / den;
  if (steps > (uint64_t)UINT32_MAX) {
    steps = (uint64_t)UINT32_MAX;
  }
  return (uint32_t)steps;
}

static uint32_t step_motion_ramp_steps_abs_between(uint16_t from_dhz, uint16_t to_dhz, uint16_t rate_dhz_per_s) {
  if (rate_dhz_per_s == 0u || from_dhz == to_dhz) {
    return 0u;
  }
  if (from_dhz > to_dhz) {
    return step_motion_ramp_steps_between(from_dhz, to_dhz, rate_dhz_per_s);
  }
  return step_motion_ramp_steps_between(to_dhz, from_dhz, rate_dhz_per_s);
}

static uint32_t step_motion_ramp_us_between(uint16_t from_dhz, uint16_t to_dhz, uint16_t rate_dhz_per_s) {
  if (rate_dhz_per_s == 0u || from_dhz == to_dhz) {
    return 0u;
  }
  const uint32_t high = (from_dhz > to_dhz) ? (uint32_t)from_dhz : (uint32_t)to_dhz;
  const uint32_t low = (from_dhz > to_dhz) ? (uint32_t)to_dhz : (uint32_t)from_dhz;
  const uint32_t delta = high - low;
  return (uint32_t)(((uint64_t)delta * 1000000ULL) / (uint64_t)rate_dhz_per_s);
}

static uint16_t step_motion_launch_freq_dhz(uint16_t peak_dhz) {
  if (peak_dhz == 0u) {
    return 0u;
  }
  uint16_t launch_dhz = peak_dhz / 4u;
  if (launch_dhz < STEP_MOTION_MIN_MECHANICAL_DHZ) {
    launch_dhz = STEP_MOTION_MIN_MECHANICAL_DHZ;
  }
  if (launch_dhz > peak_dhz) {
    launch_dhz = peak_dhz;
  }
  return launch_dhz;
}

static uint16_t step_motion_fine_freq_dhz(uint16_t peak_dhz) {
  uint16_t fine_dhz = step_motion_launch_freq_dhz(peak_dhz);
  if (fine_dhz > STEP_MOTION_FINE_FREQ_DHZ) {
    fine_dhz = STEP_MOTION_FINE_FREQ_DHZ;
  }
  if (fine_dhz > peak_dhz) {
    fine_dhz = peak_dhz;
  }
  return fine_dhz;
}

static uint16_t step_motion_triangular_cruise_dhz(
  uint16_t requested_peak_dhz,
  uint16_t launch_dhz,
  uint16_t fine_dhz,
  uint16_t accel_dhz_per_s,
  uint16_t decel_dhz_per_s,
  uint32_t usable_steps
) {
  if (requested_peak_dhz == 0u || usable_steps == 0u || accel_dhz_per_s == 0u || decel_dhz_per_s == 0u) {
    return requested_peak_dhz;
  }

  const double accel = (double)accel_dhz_per_s;
  const double decel = (double)decel_dhz_per_s;
  const double ratio = (double)STEP_MOTION_MICROSTEP_RATIO / 20.0;
  const double launch_sq = (double)launch_dhz * (double)launch_dhz;
  const double fine_sq = (double)fine_dhz * (double)fine_dhz;
  const double denom = (1.0 / accel) + (1.0 / decel);
  if (denom <= 0.0) {
    return requested_peak_dhz;
  }

  const double rhs = ((double)usable_steps / ratio) + (launch_sq / accel) + (fine_sq / decel);
  if (rhs <= 0.0) {
    return requested_peak_dhz;
  }

  double cruise = sqrt(rhs / denom);
  const double min_cruise = (double)((launch_dhz > fine_dhz) ? launch_dhz : fine_dhz);
  if (cruise < min_cruise) {
    cruise = min_cruise;
  }
  if (cruise > (double)requested_peak_dhz) {
    cruise = (double)requested_peak_dhz;
  }
  return (uint16_t)lround(cruise);
}

static step_motion_phase_plan_t step_motion_plan_phase(const step_motion_phase_t *ph) {
  step_motion_phase_plan_t plan = {0};
  if (ph == NULL || ph->peak_dhz == 0u || ph->target_steps == 0u) {
    return plan;
  }

  plan.cruise_dhz = ph->peak_dhz;
  plan.launch_dhz = step_motion_launch_freq_dhz(plan.cruise_dhz);
  plan.fine_dhz = step_motion_fine_freq_dhz(plan.cruise_dhz);

  uint32_t fine_window_steps = (uint32_t)ph->target_steps;
  if (fine_window_steps > STEP_MOTION_FINE_WINDOW_STEPS) {
    fine_window_steps = STEP_MOTION_FINE_WINDOW_STEPS;
  }
  uint32_t usable_steps = (uint32_t)ph->target_steps - fine_window_steps;

  const uint32_t accel_steps_at_peak = step_motion_ramp_steps_abs_between(
    plan.launch_dhz,
    plan.cruise_dhz,
    ph->accel_dhz_per_s
  );
  const uint32_t decel_steps_at_peak = step_motion_ramp_steps_abs_between(
    plan.cruise_dhz,
    plan.fine_dhz,
    ph->decel_dhz_per_s
  );
  if ((usable_steps > 0u) && ((accel_steps_at_peak + decel_steps_at_peak) > usable_steps)) {
    plan.cruise_dhz = step_motion_triangular_cruise_dhz(
      ph->peak_dhz,
      plan.launch_dhz,
      plan.fine_dhz,
      ph->accel_dhz_per_s,
      ph->decel_dhz_per_s,
      usable_steps
    );
    plan.launch_dhz = step_motion_launch_freq_dhz(plan.cruise_dhz);
    plan.fine_dhz = step_motion_fine_freq_dhz(plan.cruise_dhz);
  }

  const uint32_t decel_steps_to_fine = step_motion_ramp_steps_abs_between(
    plan.cruise_dhz,
    plan.fine_dhz,
    ph->decel_dhz_per_s
  );
  const uint32_t stop_ramp_steps = step_motion_ramp_steps_abs_between(
    plan.fine_dhz,
    0u,
    ph->decel_dhz_per_s
  );

  plan.accel_ramp_us = step_motion_ramp_us_between(plan.launch_dhz, plan.cruise_dhz, ph->accel_dhz_per_s);
  plan.decel_ramp_us = step_motion_ramp_us_between(plan.cruise_dhz, plan.fine_dhz, ph->decel_dhz_per_s);
  plan.stop_ramp_us = step_motion_ramp_us_between(plan.fine_dhz, 0u, ph->decel_dhz_per_s);

  const uint32_t usable_stop_steps = ((uint32_t)ph->target_steps > stop_ramp_steps)
    ? ((uint32_t)ph->target_steps - stop_ramp_steps)
    : 0u;
  plan.stop_start_steps = usable_stop_steps;
  plan.decel_start_steps = (usable_steps > decel_steps_to_fine)
    ? (usable_steps - decel_steps_to_fine)
    : 0u;
  if (plan.stop_start_steps < plan.decel_start_steps) {
    plan.stop_start_steps = plan.decel_start_steps;
  }
  return plan;
}

runtime_err_t motion_commands_step_motion(const proto_frame_t *frame, motion_state_snapshot_fn state_get_snapshot) {
  runtime_err_t status = ERR_OK;
  bool playback_claimed = false;
  motion_commands_clear_error_detail();
  if (runtime_busy(state_get_snapshot)) {
    return ERR_BAD_STATE;
  }
  playback_wave_engine_stop_all();
  if (frame->payload_len < 2u) {
    return ERR_BAD_PAYLOAD;
  }

  const uint8_t motor_count = frame->payload[0];
  if (motor_count == 0u || motor_count > MOTOR_COUNT) {
    return ERR_BAD_PAYLOAD;
  }
  if (motor_count > PLAYBACK_MOTOR_COUNT) {
    return ERR_BAD_PAYLOAD;
  }

  step_motion_motor_state_t motors[MOTOR_COUNT];
  memset(motors, 0, sizeof(motors));

  const uint8_t *cursor = &frame->payload[1];
  const uint8_t *payload_end = &frame->payload[frame->payload_len];

  for (uint8_t i = 0u; i < motor_count; ++i) {
    if (cursor + STEP_MOTION_MOTOR_HDR_SIZE > payload_end) {
      return ERR_BAD_PAYLOAD;
    }
    motors[i].start_delay_ms = proto_read_le16(&cursor[0]);
    motors[i].trigger_motor = cursor[2];
    motors[i].trigger_steps = proto_read_le16(&cursor[3]);
    motors[i].phase_count = cursor[5];
    cursor += STEP_MOTION_MOTOR_HDR_SIZE;

    if (motors[i].phase_count == 0u || motors[i].phase_count > STEP_MOTION_MAX_PHASES) {
      return ERR_BAD_PAYLOAD;
    }
    if (motors[i].trigger_motor != STEP_MOTION_NO_TRIGGER && motors[i].trigger_motor >= motor_count) {
      return ERR_BAD_PAYLOAD;
    }

    for (uint8_t ph = 0u; ph < motors[i].phase_count; ++ph) {
      if (cursor + STEP_MOTION_PHASE_SIZE > payload_end) {
        return ERR_BAD_PAYLOAD;
      }
      const uint8_t phase_flags = cursor[0];
      if ((phase_flags & (uint8_t)~STEP_MOTION_PHASE_FLAG_REVERSE) != 0u) {
        return ERR_BAD_PAYLOAD;
      }
      motors[i].phases[ph].direction = (phase_flags & STEP_MOTION_PHASE_FLAG_REVERSE) != 0u ? 1u : 0u;
      motors[i].phases[ph].target_steps = proto_read_le16(&cursor[1]);
      motors[i].phases[ph].peak_dhz = proto_read_le16(&cursor[3]);
      motors[i].phases[ph].accel_dhz_per_s = proto_read_le16(&cursor[5]);
      motors[i].phases[ph].decel_dhz_per_s = proto_read_le16(&cursor[7]);
      motors[i].phases[ph].hold_ms = proto_read_le16(&cursor[9]);
      cursor += STEP_MOTION_PHASE_SIZE;

      if (motors[i].phases[ph].peak_dhz > PLAYBACK_SAFE_MAX_FREQ_DHZ) {
        return ERR_BAD_PAYLOAD;
      }
      if (motors[i].phases[ph].peak_dhz == 0u && motors[i].phases[ph].target_steps != 0u) {
        return ERR_BAD_PAYLOAD;
      }
      if (motors[i].phases[ph].peak_dhz > 0u && motors[i].phases[ph].target_steps == 0u) {
        return ERR_BAD_PAYLOAD;
      }
      if (motors[i].phases[ph].peak_dhz > 0u && motors[i].phases[ph].decel_dhz_per_s == 0u) {
        return ERR_BAD_PAYLOAD;
      }
    }

    motors[i].current_phase = 0u;
    motors[i].triggered = (motors[i].trigger_motor == STEP_MOTION_NO_TRIGGER);
    motors[i].phase_start_count = 0u;
  }

  pulse_engine_stop_all();
  if (playback_wave_engine_claim_step_gpio() != ESP_OK) {
    motion_commands_set_error_detail("playback wave gpio claim failed");
    return ERR_INTERNAL;
  }
  playback_claimed = true;
  playback_wave_engine_note_stop_reason(0u);
  playback_wave_engine_reset_step_counts();
  playback_wave_engine_tick(esp_timer_get_time());

  uint64_t motor_phase0_start_counts[MOTOR_COUNT] = {0u};
  bool motor_phase0_count_captured[MOTOR_COUNT];
  bool phase_started[MOTOR_COUNT];
  bool fine_mode[MOTOR_COUNT];
  uint8_t phase_state[MOTOR_COUNT];
  step_motion_phase_plan_t phase_plans[MOTOR_COUNT];
  uint16_t current_freq_dhz[MOTOR_COUNT];
  int64_t phase_transition_us[MOTOR_COUNT];
  int64_t phase_hold_ready_us[MOTOR_COUNT];
  memset(motor_phase0_count_captured, 0, sizeof(motor_phase0_count_captured));
  memset(phase_started, 0, sizeof(phase_started));
  memset(fine_mode, 0, sizeof(fine_mode));
  memset(phase_state, 0, sizeof(phase_state));
  memset(phase_plans, 0, sizeof(phase_plans));
  memset(current_freq_dhz, 0, sizeof(current_freq_dhz));
  memset(phase_transition_us, 0, sizeof(phase_transition_us));
  memset(phase_hold_ready_us, 0, sizeof(phase_hold_ready_us));
  const int64_t cmd_start_us = esp_timer_get_time();
  bool progress_initialized = false;
  uint64_t last_motion_step_sum = 0u;
  int64_t last_progress_us = cmd_start_us;
  bool fine_polling = false;
  uint8_t stall_motor_idx = 0xFFu;
  uint8_t stall_phase_idx = 0u;
  uint32_t stall_relative_steps = 0u;
  uint16_t stall_target_steps = 0u;
  uint16_t stall_freq_dhz = 0u;
  uint8_t stall_fine_mode = 0u;
  uint8_t stall_phase_state = 0u;
  uint32_t stall_decel_start_steps = 0u;
  uint32_t stall_stop_start_steps = 0u;
  while (true) {
    if (fine_polling) {
      esp_rom_delay_us(STEP_MOTION_FINE_POLL_US);
    } else {
      esp_rom_delay_us(STEP_MOTION_POLL_MS * 1000u);
    }
    const int64_t now_us = esp_timer_get_time();
    playback_wave_engine_tick(now_us);
    const int64_t elapsed_us_64 = now_us - cmd_start_us;
    if (elapsed_us_64 < 0) {
      break;
    }
    const uint32_t elapsed_ms = (uint32_t)(elapsed_us_64 / 1000LL);
    if (elapsed_ms > STEP_MOTION_MAX_RUNTIME_MS) {
      break;
    }

    uint64_t step_counts[MOTOR_COUNT] = {0u};
    playback_wave_engine_get_step_counts(step_counts);

    bool any_active = false;
    uint64_t motion_step_sum = 0u;
    bool motion_active = false;
    bool next_fine_polling = false;
    stall_motor_idx = 0xFFu;
    for (uint8_t i = 0u; i < motor_count; ++i) {
      motion_step_sum += step_counts[i];
    }
    for (uint8_t i = 0u; i < motor_count; ++i) {
      step_motion_motor_state_t *m = &motors[i];
      if (m->current_phase >= m->phase_count) {
        continue;
      }

      if (elapsed_ms < (uint32_t)m->start_delay_ms) {
        any_active = true;
        continue;
      }

      if (!m->triggered) {
        const uint8_t tm = m->trigger_motor;
        if (motor_phase0_count_captured[tm]) {
          const uint64_t relative = step_counts[tm] - motor_phase0_start_counts[tm];
          if (relative >= (uint64_t)m->trigger_steps) {
            m->triggered = true;
          }
        }
        if (!m->triggered) {
          any_active = true;
          continue;
        }
      }

      if (!motor_phase0_count_captured[i]) {
        motor_phase0_start_counts[i] = step_counts[i];
        motor_phase0_count_captured[i] = true;
      }

      const step_motion_phase_t *ph = &m->phases[m->current_phase];
      if (ph->peak_dhz == 0u || ph->target_steps == 0u) {
        any_active = true;
        if (!phase_started[i]) {
          step_motion_apply_playback_target_with_direction(i, 0u, 0u, ph->direction);
          phase_transition_us[i] = now_us;
          phase_started[i] = true;
          m->phase_start_count = step_counts[i];
        }
        const int64_t idle_elapsed_ms = (now_us - phase_transition_us[i]) / 1000LL;
        if (idle_elapsed_ms >= (int64_t)ph->hold_ms) {
          m->current_phase++;
          phase_started[i] = false;
        }
        continue;
      }

      any_active = true;
      const uint64_t relative_steps_64 = step_counts[i] - m->phase_start_count;
      const uint32_t relative_steps = (relative_steps_64 > (uint64_t)UINT32_MAX)
                                        ? UINT32_MAX
                                        : (uint32_t)relative_steps_64;

      if (!phase_started[i]) {
        phase_plans[i] = step_motion_plan_phase(ph);
        current_freq_dhz[i] = phase_plans[i].launch_dhz;
        m->phase_start_count = step_counts[i];
        phase_transition_us[i] = now_us;
        phase_hold_ready_us[i] = now_us
          + (int64_t)phase_plans[i].accel_ramp_us
          + ((int64_t)ph->hold_ms * 1000LL);
        fine_mode[i] = (phase_plans[i].cruise_dhz <= phase_plans[i].fine_dhz);
        phase_state[i] = 1u;
        phase_started[i] = true;
        step_motion_apply_playback_target_with_direction(i, phase_plans[i].launch_dhz, 0u, ph->direction);
        if (phase_plans[i].cruise_dhz != phase_plans[i].launch_dhz) {
          current_freq_dhz[i] = phase_plans[i].cruise_dhz;
          step_motion_apply_playback_target_with_direction(
            i,
            phase_plans[i].cruise_dhz,
            phase_plans[i].accel_ramp_us,
            ph->direction
          );
        }
        motion_active = true;
        stall_motor_idx = i;
        stall_phase_idx = m->current_phase;
        stall_relative_steps = 0u;
        stall_target_steps = ph->target_steps;
        stall_freq_dhz = current_freq_dhz[i];
        stall_fine_mode = fine_mode[i] ? 1u : 0u;
        stall_phase_state = phase_state[i];
        stall_decel_start_steps = phase_plans[i].decel_start_steps;
        stall_stop_start_steps = phase_plans[i].stop_start_steps;
        continue;
      }

      if (phase_state[i] != 3u) {
        const step_motion_phase_plan_t *plan = &phase_plans[i];
        if (phase_state[i] == 1u &&
            plan->decel_ramp_us > 0u &&
            relative_steps >= plan->decel_start_steps &&
            now_us >= phase_hold_ready_us[i]) {
          current_freq_dhz[i] = plan->fine_dhz;
          fine_mode[i] = true;
          phase_state[i] = 2u;
          step_motion_apply_playback_target_with_direction(i, plan->fine_dhz, plan->decel_ramp_us, ph->direction);
        }
        if (relative_steps >= plan->stop_start_steps) {
          current_freq_dhz[i] = 0u;
          phase_state[i] = 3u;
          phase_transition_us[i] = now_us;
          step_motion_apply_playback_target_with_direction(i, 0u, plan->stop_ramp_us, ph->direction);
        }
      }

      if (phase_state[i] == 3u) {
        const uint32_t stop_ramp_us = phase_plans[i].stop_ramp_us;
        if (((stop_ramp_us == 0u) && (relative_steps >= (uint32_t)ph->target_steps)) ||
            ((stop_ramp_us > 0u) && ((now_us - phase_transition_us[i]) >= (int64_t)stop_ramp_us))) {
          playback_wave_motor_debug_t settle_debug = {0};
          playback_wave_engine_get_motor_debug(i, &settle_debug);
          if (settle_debug.state == 0u &&
              settle_debug.timer_running == 0u &&
              settle_debug.stop_requested == 0u) {
            step_motion_apply_playback_target_with_direction(i, 0u, 0u, ph->direction);
            current_freq_dhz[i] = 0u;
            fine_mode[i] = false;
            phase_state[i] = 0u;
            phase_started[i] = false;
            m->current_phase++;
            continue;
          }
          any_active = true;
          continue;
        }
      }

      if (fine_mode[i]) {
        next_fine_polling = true;
      }
      motion_active = true;
      if (stall_motor_idx == 0xFFu) {
        stall_motor_idx = i;
        stall_phase_idx = m->current_phase;
        stall_relative_steps = relative_steps;
        stall_target_steps = ph->target_steps;
        stall_freq_dhz = (current_freq_dhz[i] > 0u) ? current_freq_dhz[i] : phase_plans[i].fine_dhz;
        stall_fine_mode = fine_mode[i] ? 1u : 0u;
        stall_phase_state = phase_state[i];
        stall_decel_start_steps = phase_plans[i].decel_start_steps;
        stall_stop_start_steps = phase_plans[i].stop_start_steps;
      }
    }

    if (motion_active) {
      if (!progress_initialized || motion_step_sum > last_motion_step_sum) {
        last_motion_step_sum = motion_step_sum;
        last_progress_us = now_us;
        progress_initialized = true;
      } else if ((now_us - last_progress_us) > STEP_MOTION_STALL_TIMEOUT_US) {
        playback_wave_motor_debug_t debug = {0};
        if (stall_motor_idx < PLAYBACK_MOTOR_COUNT) {
          playback_wave_engine_get_motor_debug(stall_motor_idx, &debug);
        }
        const uint8_t stop_reason = playback_wave_engine_last_stop_reason();
        motion_commands_set_error_detail(
          "stall m=%u ph=%u pstate=%u steps=%lu/%u decel=%lu stop=%lu freq=%u fine=%u rsn=%u st=%u cur=%u tgt=%u fin=%u acc=%lu run=%u stop=%u ex=%u per=%lu",
          (unsigned int)stall_motor_idx,
          (unsigned int)stall_phase_idx,
          (unsigned int)stall_phase_state,
          (unsigned long)stall_relative_steps,
          (unsigned int)stall_target_steps,
          (unsigned long)stall_decel_start_steps,
          (unsigned long)stall_stop_start_steps,
          (unsigned int)stall_freq_dhz,
          (unsigned int)stall_fine_mode,
          (unsigned int)stop_reason,
          (unsigned int)debug.state,
          (unsigned int)debug.current_dhz,
          (unsigned int)debug.target_dhz,
          (unsigned int)debug.final_target_dhz,
          (unsigned long)debug.exact_accel_dhz_per_s,
          (unsigned int)debug.timer_running,
          (unsigned int)debug.stop_requested,
          (unsigned int)debug.exact_mode,
          (unsigned long)debug.last_period_ticks
        );
        status = ERR_INTERNAL;
        goto cleanup;
      }
    }

    if (!any_active) {
      break;
    }
    fine_polling = next_fine_polling;
  }

cleanup:
  playback_wave_engine_note_stop_reason(10u);
  playback_wave_engine_stop_all();
  if (playback_claimed) {
    playback_wave_engine_release_step_gpio();
  }
  pulse_engine_stop_all();
  return status;
}

const char *motion_commands_error_detail(uint8_t failed_cmd, runtime_err_t code) {
  if (code != ERR_INTERNAL) {
    return NULL;
  }
  if (failed_cmd != PROTO_CMD_STEP_MOTION) {
    return NULL;
  }
  if (s_motion_error_detail[0] == '\0') {
    return NULL;
  }
  return s_motion_error_detail;
}

#include "playback_wave_engine.h"

#include <string.h>

#include "driver/gpio.h"
#include "driver/mcpwm_prelude.h"
#include "esp_attr.h"
#include "esp_check.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"

#include "pulse_accounting.h"
#include "protocol_defs.h"

#define PLAYBACK_WAVE_RESOLUTION_HZ (1000000u)
#define PLAYBACK_WAVE_STEP_HIGH_US (4u)
#define PLAYBACK_WAVE_DEFAULT_CONTROL_INTERVAL_US (1000u)
#define PLAYBACK_WAVE_STOP_SETTLE_TIMEOUT_US (10000u)
#define PLAYBACK_WAVE_STOP_POLL_US (100u)
#define MICROSTEP_RATIO (16u)
/* ESP32 MCPWM timers top out at 65535 ticks, which maps to ~1.0 Hz at the
 * current 1 MHz resolution and 16x microstepping. Keep sub-Hz control-loop
 * tails from requesting invalid timer periods while decelerating to a stop. */
#define PLAYBACK_WAVE_MIN_MUSIC_DHZ (10u)

typedef enum {
  PLAYBACK_WAVE_STATE_STOPPED = 0,
  PLAYBACK_WAVE_STATE_LAUNCHING,
  PLAYBACK_WAVE_STATE_RUNNING,
  PLAYBACK_WAVE_STATE_DECEL_TO_STOP,
  PLAYBACK_WAVE_STATE_FLIP_WAIT_STOP,
  PLAYBACK_WAVE_STATE_RESTART_PENDING,
} playback_wave_state_t;

typedef struct {
  bool configured;
  bool generator_attached;
  bool timer_running;
  bool stop_requested;
  volatile bool stop_complete_pending;
  mcpwm_timer_handle_t timer;
  mcpwm_oper_handle_t oper;
  mcpwm_cmpr_handle_t comparator;
  mcpwm_gen_handle_t generator;
  gpio_num_t dir_pin;
  playback_wave_state_t state;
  uint16_t current_dhz;
  uint16_t target_dhz;
  uint16_t final_target_dhz;
  uint16_t launch_target_dhz;
  uint16_t pending_restart_target_dhz;
  uint8_t current_direction;
  uint8_t pending_direction;
  bool exact_mode;
  bool pending_restart_exact_mode;
  uint32_t exact_accel_dhz_per_s;
  uint32_t last_period_ticks;
  uint32_t pulse_accum_us;
} playback_wave_motor_t;

static const gpio_num_t s_step_pins[PLAYBACK_WAVE_MOTOR_COUNT] = {
  GPIO_NUM_16,
  GPIO_NUM_17,
  GPIO_NUM_18,
  GPIO_NUM_19,
  GPIO_NUM_21,
  GPIO_NUM_22,
};

static const gpio_num_t s_dir_pins[PLAYBACK_WAVE_MOTOR_COUNT] = {
  GPIO_NUM_4,
  GPIO_NUM_13,
  GPIO_NUM_14,
  GPIO_NUM_26,
  GPIO_NUM_27,
  GPIO_NUM_32,
};

static playback_wave_motor_t s_motors[PLAYBACK_WAVE_MOTOR_COUNT];
static uint64_t s_motor_step_counts[PLAYBACK_WAVE_MOTOR_COUNT];
static int64_t s_motor_position_counts[PLAYBACK_WAVE_MOTOR_COUNT];
static playback_wave_diag_counters_t s_diag = {0};
static portMUX_TYPE s_engine_lock = portMUX_INITIALIZER_UNLOCKED;
static uint32_t s_run_accel_dhz_per_s = 80000u;
static uint16_t s_launch_start_dhz = 600u;
static uint32_t s_launch_accel_dhz_per_s = 50000u;
static uint16_t s_launch_crossover_dhz = 1800u;
static bool s_speech_assist_enabled = false;
static uint32_t s_control_interval_us = PLAYBACK_WAVE_DEFAULT_CONTROL_INTERVAL_US;
static uint32_t s_release_accel_dhz_per_s = 50000u;
static int64_t s_last_control_tick_us = 0;
static bool s_initialized = false;
static uint8_t s_last_stop_reason = 0u;

typedef enum {
  PLAYBACK_WAVE_FAULT_NONE = 0,
  PLAYBACK_WAVE_FAULT_ATTACH_NEW_GENERATOR = 1,
  PLAYBACK_WAVE_FAULT_ATTACH_TIMER_ACTION = 2,
  PLAYBACK_WAVE_FAULT_ATTACH_COMPARE_ACTION = 3,
  PLAYBACK_WAVE_FAULT_ATTACH_FORCE_LEVEL = 4,
  PLAYBACK_WAVE_FAULT_DETACH_FORCE_LEVEL = 5,
  PLAYBACK_WAVE_FAULT_DETACH_DELETE_GENERATOR = 6,
  PLAYBACK_WAVE_FAULT_SET_PERIOD = 7,
  PLAYBACK_WAVE_FAULT_SET_COMPARE = 8,
  PLAYBACK_WAVE_FAULT_FORCE_LOW = 9,
  PLAYBACK_WAVE_FAULT_RELEASE_FORCE = 10,
  PLAYBACK_WAVE_FAULT_TIMER_START = 11,
  PLAYBACK_WAVE_FAULT_TIMER_STOP = 12,
  PLAYBACK_WAVE_FAULT_INVALID_MOTOR_CHANGE = 13,
} playback_wave_fault_reason_t;

static void note_engine_fault_locked(uint8_t motor_idx, playback_wave_fault_reason_t reason);
static bool any_motors_stopping_locked(void);

static uint16_t clamp_supported_music_dhz(uint16_t music_dhz) {
  if (music_dhz == 0u) {
    return 0u;
  }
  return (music_dhz < PLAYBACK_WAVE_MIN_MUSIC_DHZ) ? PLAYBACK_WAVE_MIN_MUSIC_DHZ : music_dhz;
}

static inline void set_step_pin_level(gpio_num_t step_pin, uint8_t level) {
  if ((int)step_pin < 0) {
    return;
  }
  gpio_set_level(step_pin, level != 0u ? 1 : 0);
}

static esp_err_t attach_generator_locked(uint8_t motor_idx, playback_wave_motor_t *motor) {
  if (motor == NULL || !motor->configured) {
    return ESP_ERR_INVALID_STATE;
  }
  if (motor->generator_attached && motor->generator != NULL) {
    return ESP_OK;
  }

  mcpwm_generator_config_t generator_config = {
    .gen_gpio_num = s_step_pins[motor_idx],
    .flags = {0},
  };
  esp_err_t err = mcpwm_new_generator(motor->oper, &generator_config, &motor->generator);
  if (err != ESP_OK) {
    note_engine_fault_locked(motor_idx, PLAYBACK_WAVE_FAULT_ATTACH_NEW_GENERATOR);
    motor->generator = NULL;
    return err;
  }
  err = mcpwm_generator_set_action_on_timer_event(
    motor->generator,
    MCPWM_GEN_TIMER_EVENT_ACTION(MCPWM_TIMER_DIRECTION_UP, MCPWM_TIMER_EVENT_EMPTY, MCPWM_GEN_ACTION_HIGH)
  );
  if (err != ESP_OK) {
    note_engine_fault_locked(motor_idx, PLAYBACK_WAVE_FAULT_ATTACH_TIMER_ACTION);
    (void)mcpwm_del_generator(motor->generator);
    motor->generator = NULL;
    return err;
  }
  err = mcpwm_generator_set_action_on_compare_event(
    motor->generator,
    MCPWM_GEN_COMPARE_EVENT_ACTION(MCPWM_TIMER_DIRECTION_UP, motor->comparator, MCPWM_GEN_ACTION_LOW)
  );
  if (err != ESP_OK) {
    note_engine_fault_locked(motor_idx, PLAYBACK_WAVE_FAULT_ATTACH_COMPARE_ACTION);
    (void)mcpwm_del_generator(motor->generator);
    motor->generator = NULL;
    return err;
  }
  motor->generator_attached = true;
  set_step_pin_level(s_step_pins[motor_idx], 0u);
  if (mcpwm_generator_set_force_level(motor->generator, 0, true) != ESP_OK) {
    note_engine_fault_locked(motor_idx, PLAYBACK_WAVE_FAULT_ATTACH_FORCE_LEVEL);
    (void)mcpwm_del_generator(motor->generator);
    motor->generator = NULL;
    motor->generator_attached = false;
    return ESP_FAIL;
  }
  return ESP_OK;
}

static void detach_generator_locked(uint8_t motor_idx, playback_wave_motor_t *motor) {
  if (motor == NULL) {
    return;
  }
  if (motor->generator_attached && motor->generator != NULL) {
    if (mcpwm_generator_set_force_level(motor->generator, 0, true) != ESP_OK) {
      note_engine_fault_locked(motor_idx, PLAYBACK_WAVE_FAULT_DETACH_FORCE_LEVEL);
    }
    if (mcpwm_del_generator(motor->generator) != ESP_OK) {
      note_engine_fault_locked(motor_idx, PLAYBACK_WAVE_FAULT_DETACH_DELETE_GENERATOR);
    }
    motor->generator = NULL;
    motor->generator_attached = false;
  }
  gpio_set_direction(s_step_pins[motor_idx], GPIO_MODE_OUTPUT);
  gpio_set_level(s_step_pins[motor_idx], 0);
}

static inline void set_dir_pin_level(gpio_num_t pin, uint8_t level) {
  if ((int)pin < 0) {
    return;
  }
  gpio_set_level(pin, level != 0u ? 1 : 0);
}

static void note_engine_fault_locked(uint8_t motor_idx, playback_wave_fault_reason_t reason) {
  s_diag.engine_fault_count++;
  if (motor_idx < PLAYBACK_WAVE_MOTOR_COUNT) {
    s_diag.engine_fault_mask |= (1u << motor_idx);
  }
  s_diag.engine_fault_last_reason = (uint32_t)reason;
  s_diag.engine_fault_last_motor = (uint32_t)motor_idx;
  switch (reason) {
    case PLAYBACK_WAVE_FAULT_ATTACH_NEW_GENERATOR:
    case PLAYBACK_WAVE_FAULT_ATTACH_TIMER_ACTION:
    case PLAYBACK_WAVE_FAULT_ATTACH_COMPARE_ACTION:
    case PLAYBACK_WAVE_FAULT_ATTACH_FORCE_LEVEL:
      s_diag.engine_fault_attach_count++;
      break;
    case PLAYBACK_WAVE_FAULT_DETACH_FORCE_LEVEL:
    case PLAYBACK_WAVE_FAULT_DETACH_DELETE_GENERATOR:
      s_diag.engine_fault_detach_count++;
      break;
    case PLAYBACK_WAVE_FAULT_SET_PERIOD:
    case PLAYBACK_WAVE_FAULT_SET_COMPARE:
      s_diag.engine_fault_period_count++;
      break;
    case PLAYBACK_WAVE_FAULT_FORCE_LOW:
    case PLAYBACK_WAVE_FAULT_RELEASE_FORCE:
      s_diag.engine_fault_force_count++;
      break;
    case PLAYBACK_WAVE_FAULT_TIMER_START:
    case PLAYBACK_WAVE_FAULT_TIMER_STOP:
      s_diag.engine_fault_timer_count++;
      break;
    case PLAYBACK_WAVE_FAULT_INVALID_MOTOR_CHANGE:
      s_diag.engine_fault_invalid_change_count++;
      break;
    case PLAYBACK_WAVE_FAULT_NONE:
    default:
      break;
  }
}

static uint32_t period_ticks_for_music_dhz(uint16_t music_dhz) {
  const uint16_t clamped_music_dhz = clamp_supported_music_dhz(music_dhz);
  if (clamped_music_dhz == 0u) {
    return 0u;
  }
  const uint32_t step_dhz = (uint32_t)clamped_music_dhz * MICROSTEP_RATIO;
  uint32_t period_ticks = (uint32_t)(10000000ull / (uint64_t)step_dhz);
  if (period_ticks <= PLAYBACK_WAVE_STEP_HIGH_US) {
    period_ticks = PLAYBACK_WAVE_STEP_HIGH_US + 1u;
  }
  return period_ticks;
}

static bool set_period_locked(uint8_t motor_idx, playback_wave_motor_t *motor, uint16_t music_dhz) {
  const uint32_t period_ticks = period_ticks_for_music_dhz(music_dhz);
  if (period_ticks == 0u) {
    return true;
  }
  if (period_ticks == motor->last_period_ticks) {
    return true;
  }
  if (mcpwm_timer_set_period(motor->timer, period_ticks) != ESP_OK) {
    note_engine_fault_locked(motor_idx, PLAYBACK_WAVE_FAULT_SET_PERIOD);
    return false;
  }
  if (mcpwm_comparator_set_compare_value(motor->comparator, PLAYBACK_WAVE_STEP_HIGH_US) != ESP_OK) {
    note_engine_fault_locked(motor_idx, PLAYBACK_WAVE_FAULT_SET_COMPARE);
    return false;
  }
  motor->last_period_ticks = period_ticks;
  s_diag.wave_period_update_count++;
  return true;
}

static void force_low_locked(uint8_t motor_idx, playback_wave_motor_t *motor) {
  if (!motor->configured || !motor->generator_attached || motor->generator == NULL) {
    return;
  }
  if (mcpwm_generator_set_force_level(motor->generator, 0, true) != ESP_OK) {
    note_engine_fault_locked(motor_idx, PLAYBACK_WAVE_FAULT_FORCE_LOW);
  }
}

static void release_force_locked(uint8_t motor_idx, playback_wave_motor_t *motor) {
  if (!motor->configured || !motor->generator_attached || motor->generator == NULL) {
    return;
  }
  if (mcpwm_generator_set_force_level(motor->generator, -1, true) != ESP_OK) {
    note_engine_fault_locked(motor_idx, PLAYBACK_WAVE_FAULT_RELEASE_FORCE);
  }
}

static void start_timer_locked(uint8_t motor_idx, playback_wave_motor_t *motor) {
  if (!motor->configured || motor->timer_running) {
    return;
  }
  if (!motor->generator_attached || motor->generator == NULL) {
    note_engine_fault_locked(motor_idx, PLAYBACK_WAVE_FAULT_TIMER_START);
    return;
  }
  if (mcpwm_timer_start_stop(motor->timer, MCPWM_TIMER_START_NO_STOP) != ESP_OK) {
    note_engine_fault_locked(motor_idx, PLAYBACK_WAVE_FAULT_TIMER_START);
    return;
  }
  release_force_locked(motor_idx, motor);
  motor->timer_running = true;
  motor->stop_requested = false;
}

static void request_timer_stop_locked(uint8_t motor_idx, playback_wave_motor_t *motor) {
  if (!motor->configured || !motor->timer_running || motor->stop_requested) {
    return;
  }
  if (mcpwm_timer_start_stop(motor->timer, MCPWM_TIMER_STOP_EMPTY) != ESP_OK) {
    note_engine_fault_locked(motor_idx, PLAYBACK_WAVE_FAULT_TIMER_STOP);
    return;
  }
  motor->stop_requested = true;
}

static void request_timer_stop_now_locked(uint8_t motor_idx, playback_wave_motor_t *motor) {
  if (!motor->configured || !motor->timer_running || motor->stop_requested) {
    return;
  }
  force_low_locked(motor_idx, motor);
  if (mcpwm_timer_start_stop(motor->timer, MCPWM_TIMER_STOP_FULL) != ESP_OK) {
    note_engine_fault_locked(motor_idx, PLAYBACK_WAVE_FAULT_TIMER_STOP);
    return;
  }
  motor->stop_requested = true;
}

static void enter_stopped_locked(uint8_t motor_idx, playback_wave_motor_t *motor) {
  motor->state = PLAYBACK_WAVE_STATE_STOPPED;
  motor->current_dhz = 0u;
  motor->target_dhz = 0u;
  motor->final_target_dhz = 0u;
  motor->launch_target_dhz = 0u;
  motor->pending_restart_target_dhz = 0u;
  motor->stop_requested = false;
  motor->stop_complete_pending = false;
  motor->timer_running = false;
  motor->exact_mode = false;
  motor->pending_restart_exact_mode = false;
  motor->exact_accel_dhz_per_s = 0u;
  motor->last_period_ticks = 0u;
  motor->pulse_accum_us = 0u;
  force_low_locked(motor_idx, motor);
  set_step_pin_level(s_step_pins[motor_idx], 0u);
}

static void set_direction_locked(playback_wave_motor_t *motor, uint8_t direction) {
  motor->current_direction = direction;
  set_dir_pin_level(motor->dir_pin, direction);
}

static void start_launch_locked(
  uint8_t motor_idx,
  playback_wave_motor_t *motor,
  uint16_t final_target_dhz,
  uint8_t direction
) {
  const uint16_t supported_final_target_dhz = clamp_supported_music_dhz(final_target_dhz);
  if (supported_final_target_dhz == 0u) {
    enter_stopped_locked(motor_idx, motor);
    return;
  }

  const uint16_t launch_start_dhz = (supported_final_target_dhz < s_launch_start_dhz)
    ? supported_final_target_dhz
    : s_launch_start_dhz;
  const uint16_t launch_target_dhz = (supported_final_target_dhz < s_launch_crossover_dhz)
    ? supported_final_target_dhz
    : s_launch_crossover_dhz;

  set_direction_locked(motor, direction);
  motor->current_dhz = launch_start_dhz;
  motor->launch_target_dhz = launch_target_dhz;
  motor->target_dhz = launch_target_dhz;
  motor->final_target_dhz = supported_final_target_dhz;
  motor->pending_restart_target_dhz = 0u;
  motor->exact_mode = false;
  motor->pending_restart_exact_mode = false;
  motor->exact_accel_dhz_per_s = 0u;
  motor->state = (launch_start_dhz < launch_target_dhz)
    ? PLAYBACK_WAVE_STATE_LAUNCHING
    : PLAYBACK_WAVE_STATE_RUNNING;
  motor->pulse_accum_us = 0u;
  (void)set_period_locked(motor_idx, motor, motor->current_dhz);
  start_timer_locked(motor_idx, motor);
  s_diag.motor_start_count++;
}

static uint32_t exact_accel_for_ramp_us(uint16_t from_dhz, uint16_t to_dhz, uint32_t ramp_us) {
  if (from_dhz == to_dhz || ramp_us == 0u) {
    return 0u;
  }
  const uint32_t high = (from_dhz > to_dhz) ? (uint32_t)from_dhz : (uint32_t)to_dhz;
  const uint32_t low = (from_dhz > to_dhz) ? (uint32_t)to_dhz : (uint32_t)from_dhz;
  const uint32_t delta = high - low;
  const uint64_t rate = (((uint64_t)delta * 1000000ull) + (uint64_t)ramp_us - 1ull) / (uint64_t)ramp_us;
  return (rate == 0u) ? 1u : (uint32_t)rate;
}

static void start_exact_locked(
  uint8_t motor_idx,
  playback_wave_motor_t *motor,
  uint16_t target_dhz,
  uint8_t direction
) {
  const uint16_t supported_target_dhz = clamp_supported_music_dhz(target_dhz);
  if (supported_target_dhz == 0u) {
    enter_stopped_locked(motor_idx, motor);
    return;
  }

  set_direction_locked(motor, direction);
  motor->current_dhz = supported_target_dhz;
  motor->target_dhz = supported_target_dhz;
  motor->final_target_dhz = supported_target_dhz;
  motor->launch_target_dhz = 0u;
  motor->pending_restart_target_dhz = 0u;
  motor->exact_mode = true;
  motor->pending_restart_exact_mode = false;
  motor->exact_accel_dhz_per_s = 0u;
  motor->state = PLAYBACK_WAVE_STATE_RUNNING;
  motor->pulse_accum_us = 0u;
  (void)set_period_locked(motor_idx, motor, motor->current_dhz);
  start_timer_locked(motor_idx, motor);
  s_diag.motor_start_count++;
}

static void apply_exact_change_locked(
  uint8_t motor_idx,
  uint16_t target_dhz,
  uint32_t ramp_us,
  uint8_t direction
) {
  if (motor_idx >= PLAYBACK_WAVE_MOTOR_COUNT) {
    note_engine_fault_locked(motor_idx, PLAYBACK_WAVE_FAULT_INVALID_MOTOR_CHANGE);
    return;
  }

  playback_wave_motor_t *motor = &s_motors[motor_idx];
  const uint16_t supported_target_dhz = clamp_supported_music_dhz(target_dhz);
  const uint8_t next_direction = direction & 0x01u;

  if (supported_target_dhz == 0u) {
    motor->pending_restart_target_dhz = 0u;
    motor->pending_restart_exact_mode = false;
    motor->exact_mode = true;
    motor->exact_accel_dhz_per_s = exact_accel_for_ramp_us(motor->current_dhz, 0u, ramp_us);
    motor->target_dhz = 0u;
    motor->final_target_dhz = 0u;
    motor->launch_target_dhz = 0u;
    if (motor->state == PLAYBACK_WAVE_STATE_STOPPED || !motor->timer_running) {
      enter_stopped_locked(motor_idx, motor);
      return;
    }
    motor->state = PLAYBACK_WAVE_STATE_DECEL_TO_STOP;
    if (ramp_us == 0u || motor->current_dhz == 0u) {
      motor->current_dhz = 0u;
      request_timer_stop_now_locked(motor_idx, motor);
    }
    return;
  }

  if (motor->state == PLAYBACK_WAVE_STATE_STOPPED || !motor->timer_running) {
    start_exact_locked(motor_idx, motor, supported_target_dhz, next_direction);
    return;
  }

  if (motor->current_direction != next_direction) {
    motor->pending_direction = next_direction;
    motor->pending_restart_target_dhz = supported_target_dhz;
    motor->pending_restart_exact_mode = true;
    motor->exact_mode = true;
    motor->exact_accel_dhz_per_s = exact_accel_for_ramp_us(motor->current_dhz, 0u, ramp_us);
    motor->target_dhz = 0u;
    motor->final_target_dhz = 0u;
    motor->state = PLAYBACK_WAVE_STATE_FLIP_WAIT_STOP;
    if (ramp_us == 0u || motor->current_dhz == 0u) {
      motor->current_dhz = 0u;
      request_timer_stop_now_locked(motor_idx, motor);
    }
    return;
  }

  motor->pending_restart_target_dhz = 0u;
  motor->pending_restart_exact_mode = false;
  motor->exact_mode = true;
  motor->exact_accel_dhz_per_s = exact_accel_for_ramp_us(motor->current_dhz, supported_target_dhz, ramp_us);
  motor->target_dhz = supported_target_dhz;
  motor->final_target_dhz = supported_target_dhz;
  motor->state = PLAYBACK_WAVE_STATE_RUNNING;
  (void)set_period_locked(motor_idx, motor, motor->current_dhz);
  start_timer_locked(motor_idx, motor);
}

static bool IRAM_ATTR timer_stop_cb(
  mcpwm_timer_handle_t timer,
  const mcpwm_timer_event_data_t *edata,
  void *user_data
) {
  (void)timer;
  (void)edata;
  playback_wave_motor_t *motor = (playback_wave_motor_t *)user_data;
  if (motor == NULL) {
    return false;
  }
  motor->stop_complete_pending = true;
  return false;
}

static void apply_change_locked(const stream_motor_change_t *change) {
  if (change->motor_idx >= PLAYBACK_WAVE_MOTOR_COUNT) {
    note_engine_fault_locked(change->motor_idx, PLAYBACK_WAVE_FAULT_INVALID_MOTOR_CHANGE);
    return;
  }
  playback_wave_motor_t *motor = &s_motors[change->motor_idx];
  const uint16_t target_dhz = clamp_supported_music_dhz(change->target_dhz);
  const bool flip = (change->flags & STREAM_EVENT_GROUP_FLAG_FLIP_BEFORE_RESTART) != 0u;
  motor->exact_mode = false;
  motor->pending_restart_exact_mode = false;
  motor->exact_accel_dhz_per_s = 0u;

  if ((motor->state == PLAYBACK_WAVE_STATE_LAUNCHING) && (flip || (target_dhz != motor->final_target_dhz))) {
    s_diag.launch_guard_count++;
  }

  if (flip && target_dhz > 0u) {
    motor->pending_direction = (uint8_t)(motor->current_direction ^ 1u);
    motor->pending_restart_target_dhz = target_dhz;
    if (motor->state == PLAYBACK_WAVE_STATE_STOPPED) {
      start_launch_locked(change->motor_idx, motor, target_dhz, motor->pending_direction);
      s_diag.flip_restart_count++;
      return;
    }
    motor->target_dhz = 0u;
    motor->final_target_dhz = 0u;
    motor->state = PLAYBACK_WAVE_STATE_FLIP_WAIT_STOP;
    return;
  }

  if (target_dhz == 0u) {
    if (motor->state == PLAYBACK_WAVE_STATE_STOPPED) {
      enter_stopped_locked(change->motor_idx, motor);
      return;
    }
    motor->pending_restart_target_dhz = 0u;
    motor->target_dhz = 0u;
    motor->final_target_dhz = 0u;
    motor->state = PLAYBACK_WAVE_STATE_DECEL_TO_STOP;
    return;
  }

  if (motor->state == PLAYBACK_WAVE_STATE_STOPPED) {
    start_launch_locked(change->motor_idx, motor, target_dhz, motor->current_direction);
    return;
  }

  if (motor->state == PLAYBACK_WAVE_STATE_FLIP_WAIT_STOP || motor->state == PLAYBACK_WAVE_STATE_RESTART_PENDING) {
    motor->pending_restart_target_dhz = target_dhz;
    return;
  }

  motor->final_target_dhz = target_dhz;
  if (motor->state == PLAYBACK_WAVE_STATE_DECEL_TO_STOP) {
    motor->state = PLAYBACK_WAVE_STATE_RUNNING;
  }
  if (motor->state == PLAYBACK_WAVE_STATE_LAUNCHING) {
    motor->launch_target_dhz = (target_dhz < s_launch_crossover_dhz) ? target_dhz : s_launch_crossover_dhz;
    motor->target_dhz = motor->launch_target_dhz;
    if (motor->current_dhz >= motor->launch_target_dhz) {
      motor->state = PLAYBACK_WAVE_STATE_RUNNING;
      motor->target_dhz = target_dhz;
    }
  } else {
    motor->state = PLAYBACK_WAVE_STATE_RUNNING;
    motor->target_dhz = target_dhz;
  }
}

static uint16_t advance_frequency_toward(uint16_t current_dhz, uint16_t target_dhz, uint32_t accel_dhz_per_s, uint32_t delta_us) {
  if (current_dhz == target_dhz) {
    return current_dhz;
  }
  if (accel_dhz_per_s == 0u || delta_us == 0u) {
    return target_dhz;
  }
  uint32_t step_dhz = (uint32_t)(((uint64_t)accel_dhz_per_s * (uint64_t)delta_us) / 1000000ull);
  if (step_dhz == 0u) {
    step_dhz = 1u;
  }
  if (current_dhz < target_dhz) {
    const uint32_t next_dhz = (uint32_t)current_dhz + step_dhz;
    return (uint16_t)((next_dhz >= target_dhz) ? target_dhz : next_dhz);
  }
  if (step_dhz >= current_dhz || (current_dhz - step_dhz) <= target_dhz) {
    return target_dhz;
  }
  return (uint16_t)(current_dhz - step_dhz);
}

static void tick_motor_locked(uint8_t motor_idx, playback_wave_motor_t *motor, uint32_t delta_us) {
  if (!motor->configured) {
    return;
  }

  if (motor->timer_running && motor->last_period_ticks > 0u && delta_us > 0u) {
    const uint64_t total_us = (uint64_t)motor->pulse_accum_us + (uint64_t)delta_us;
    const uint64_t emitted_steps = total_us / (uint64_t)motor->last_period_ticks;
    motor->pulse_accum_us = (uint32_t)(total_us % (uint64_t)motor->last_period_ticks);
    s_motor_step_counts[motor_idx] += emitted_steps;
    if (motor->current_direction == 0u) {
      s_motor_position_counts[motor_idx] += (int64_t)emitted_steps;
    } else {
      s_motor_position_counts[motor_idx] -= (int64_t)emitted_steps;
    }
    pulse_accounting_record_inferred_steps(motor_idx, (uint32_t)emitted_steps, motor->current_direction);
  }

  if (motor->stop_complete_pending) {
    motor->stop_complete_pending = false;
    motor->timer_running = false;
    motor->stop_requested = false;
    force_low_locked(motor_idx, motor);
    s_diag.motor_stop_count++;

    if (motor->state == PLAYBACK_WAVE_STATE_FLIP_WAIT_STOP || motor->state == PLAYBACK_WAVE_STATE_RESTART_PENDING) {
      const uint16_t restart_target_dhz = motor->pending_restart_target_dhz;
      const uint8_t restart_direction = motor->pending_direction;
      const bool restart_exact = motor->pending_restart_exact_mode;
      motor->state = PLAYBACK_WAVE_STATE_RESTART_PENDING;
      if (restart_target_dhz > 0u) {
        if (restart_exact) {
          start_exact_locked(motor_idx, motor, restart_target_dhz, restart_direction);
        } else {
          start_launch_locked(motor_idx, motor, restart_target_dhz, restart_direction);
        }
        s_diag.flip_restart_count++;
      } else {
        enter_stopped_locked(motor_idx, motor);
      }
      return;
    }

    enter_stopped_locked(motor_idx, motor);
    return;
  }

  if (motor->state == PLAYBACK_WAVE_STATE_STOPPED) {
    return;
  }

  uint32_t accel_dhz_per_s = s_run_accel_dhz_per_s;
  if (motor->exact_mode) {
    accel_dhz_per_s = motor->exact_accel_dhz_per_s;
  } else if (s_speech_assist_enabled &&
      (motor->state == PLAYBACK_WAVE_STATE_DECEL_TO_STOP || motor->state == PLAYBACK_WAVE_STATE_FLIP_WAIT_STOP)) {
    accel_dhz_per_s = s_release_accel_dhz_per_s;
  } else if (motor->state == PLAYBACK_WAVE_STATE_LAUNCHING ||
             motor->state == PLAYBACK_WAVE_STATE_DECEL_TO_STOP ||
             motor->state == PLAYBACK_WAVE_STATE_FLIP_WAIT_STOP) {
    accel_dhz_per_s = s_launch_accel_dhz_per_s;
  }

  const uint16_t next_dhz = advance_frequency_toward(
    motor->current_dhz,
    motor->target_dhz,
    accel_dhz_per_s,
    delta_us
  );
  if (next_dhz > 0u && next_dhz < PLAYBACK_WAVE_MIN_MUSIC_DHZ) {
    if (motor->target_dhz == 0u ||
        motor->state == PLAYBACK_WAVE_STATE_DECEL_TO_STOP ||
        motor->state == PLAYBACK_WAVE_STATE_FLIP_WAIT_STOP) {
      motor->current_dhz = 0u;
    } else {
      motor->current_dhz = PLAYBACK_WAVE_MIN_MUSIC_DHZ;
    }
  } else {
    motor->current_dhz = next_dhz;
  }

  if (motor->state == PLAYBACK_WAVE_STATE_LAUNCHING && motor->current_dhz >= motor->launch_target_dhz) {
    motor->state = PLAYBACK_WAVE_STATE_RUNNING;
    motor->target_dhz = motor->final_target_dhz;
  }

  if ((motor->state == PLAYBACK_WAVE_STATE_DECEL_TO_STOP || motor->state == PLAYBACK_WAVE_STATE_FLIP_WAIT_STOP) &&
      motor->current_dhz == 0u) {
    if (motor->exact_mode) {
      request_timer_stop_now_locked(motor_idx, motor);
    } else {
      request_timer_stop_locked(motor_idx, motor);
    }
    return;
  }

  if (motor->current_dhz == 0u) {
    if (motor->exact_mode) {
      request_timer_stop_now_locked(motor_idx, motor);
    } else {
      request_timer_stop_locked(motor_idx, motor);
    }
    return;
  }

  (void)set_period_locked(motor_idx, motor, motor->current_dhz);
  start_timer_locked(motor_idx, motor);
}

esp_err_t playback_wave_engine_init(
  uint32_t run_accel_dhz_per_s,
  uint16_t launch_start_dhz,
  uint32_t launch_accel_dhz_per_s,
  uint16_t launch_crossover_dhz
) {
  if (s_initialized) {
    playback_wave_engine_configure_profile(
      run_accel_dhz_per_s,
      launch_start_dhz,
      launch_accel_dhz_per_s,
      launch_crossover_dhz
    );
    return ESP_OK;
  }

  memset(s_motors, 0, sizeof(s_motors));
  playback_wave_engine_configure_speech_assist(false, PLAYBACK_WAVE_DEFAULT_CONTROL_INTERVAL_US, 0u);
  playback_wave_engine_configure_profile(
    run_accel_dhz_per_s,
    launch_start_dhz,
    launch_accel_dhz_per_s,
    launch_crossover_dhz
  );

  const mcpwm_timer_event_callbacks_t timer_callbacks = {
    .on_stop = timer_stop_cb,
  };

  for (uint8_t i = 0; i < PLAYBACK_WAVE_MOTOR_COUNT; ++i) {
    playback_wave_motor_t *motor = &s_motors[i];
    const int group_id = (i < 3u) ? 0 : 1;

    gpio_reset_pin(s_dir_pins[i]);
    gpio_set_direction(s_dir_pins[i], GPIO_MODE_OUTPUT);
    motor->dir_pin = s_dir_pins[i];
    set_dir_pin_level(motor->dir_pin, 0u);

    mcpwm_timer_config_t timer_config = {
      .group_id = group_id,
      .clk_src = MCPWM_TIMER_CLK_SRC_DEFAULT,
      .resolution_hz = PLAYBACK_WAVE_RESOLUTION_HZ,
      .count_mode = MCPWM_TIMER_COUNT_MODE_UP,
      .period_ticks = 1000u,
      .intr_priority = 0,
      .flags = {
        .update_period_on_empty = 1,
      },
    };
    ESP_RETURN_ON_ERROR(mcpwm_new_timer(&timer_config, &motor->timer), "playback_wave", "new timer failed");

    mcpwm_operator_config_t operator_config = {
      .group_id = group_id,
      .intr_priority = 0,
      .flags = {0},
    };
    ESP_RETURN_ON_ERROR(mcpwm_new_operator(&operator_config, &motor->oper), "playback_wave", "new operator failed");
    ESP_RETURN_ON_ERROR(mcpwm_operator_connect_timer(motor->oper, motor->timer), "playback_wave", "connect timer failed");

    mcpwm_comparator_config_t comparator_config = {
      .intr_priority = 0,
      .flags = {
        .update_cmp_on_tez = 1,
      },
    };
    ESP_RETURN_ON_ERROR(mcpwm_new_comparator(motor->oper, &comparator_config, &motor->comparator), "playback_wave", "new comparator failed");
    ESP_RETURN_ON_ERROR(mcpwm_comparator_set_compare_value(motor->comparator, PLAYBACK_WAVE_STEP_HIGH_US), "playback_wave", "set compare failed");

    ESP_RETURN_ON_ERROR(mcpwm_timer_register_event_callbacks(motor->timer, &timer_callbacks, motor), "playback_wave", "register callbacks failed");
    ESP_RETURN_ON_ERROR(mcpwm_timer_enable(motor->timer), "playback_wave", "timer enable failed");
    motor->configured = true;
    motor->generator = NULL;
    motor->generator_attached = false;
    motor->pulse_accum_us = 0u;
    enter_stopped_locked(i, motor);
  }

  ESP_RETURN_ON_ERROR(
    pulse_accounting_init(s_step_pins, s_dir_pins, PLAYBACK_WAVE_MOTOR_COUNT),
    "playback_wave",
    "pulse accounting init failed"
  );

  s_initialized = true;
  return ESP_OK;
}

esp_err_t playback_wave_engine_claim_step_gpio(void) {
  if (!s_initialized) {
    return ESP_ERR_INVALID_STATE;
  }
  portENTER_CRITICAL(&s_engine_lock);
  for (uint8_t i = 0; i < PLAYBACK_WAVE_MOTOR_COUNT; ++i) {
    esp_err_t err = attach_generator_locked(i, &s_motors[i]);
    if (err != ESP_OK) {
      for (uint8_t j = 0; j < PLAYBACK_WAVE_MOTOR_COUNT; ++j) {
        detach_generator_locked(j, &s_motors[j]);
      }
      portEXIT_CRITICAL(&s_engine_lock);
      return err;
    }
  }
  pulse_accounting_begin_session();
  portEXIT_CRITICAL(&s_engine_lock);
  return ESP_OK;
}

void playback_wave_engine_release_step_gpio(void) {
  if (!s_initialized) {
    return;
  }
  playback_wave_engine_stop_all();
  pulse_accounting_end_session();
  portENTER_CRITICAL(&s_engine_lock);
  for (uint8_t i = 0; i < PLAYBACK_WAVE_MOTOR_COUNT; ++i) {
    detach_generator_locked(i, &s_motors[i]);
  }
  portEXIT_CRITICAL(&s_engine_lock);
}

void playback_wave_engine_configure_profile(
  uint32_t run_accel_dhz_per_s,
  uint16_t launch_start_dhz,
  uint32_t launch_accel_dhz_per_s,
  uint16_t launch_crossover_dhz
) {
  portENTER_CRITICAL(&s_engine_lock);
  s_run_accel_dhz_per_s = (run_accel_dhz_per_s > 0u) ? run_accel_dhz_per_s : 1u;
  s_launch_start_dhz = (launch_start_dhz > 0u) ? launch_start_dhz : 1u;
  s_launch_accel_dhz_per_s = (launch_accel_dhz_per_s > 0u) ? launch_accel_dhz_per_s : 1u;
  s_launch_crossover_dhz = (launch_crossover_dhz >= s_launch_start_dhz) ? launch_crossover_dhz : s_launch_start_dhz;
  portEXIT_CRITICAL(&s_engine_lock);
}

void playback_wave_engine_configure_speech_assist(
  bool enabled,
  uint16_t control_interval_us,
  uint32_t release_accel_dhz_per_s
) {
  portENTER_CRITICAL(&s_engine_lock);
  s_speech_assist_enabled = enabled;
  s_control_interval_us = (control_interval_us > 0u) ? control_interval_us : PLAYBACK_WAVE_DEFAULT_CONTROL_INTERVAL_US;
  s_release_accel_dhz_per_s = (release_accel_dhz_per_s > 0u)
    ? release_accel_dhz_per_s
    : s_launch_accel_dhz_per_s;
  portEXIT_CRITICAL(&s_engine_lock);
}

void playback_wave_engine_reset(void) {
  playback_wave_engine_stop_all();
  portENTER_CRITICAL(&s_engine_lock);
  s_last_control_tick_us = 0;
  portEXIT_CRITICAL(&s_engine_lock);
  pulse_accounting_reset();
}

void playback_wave_engine_note_stop_reason(uint8_t reason) {
  portENTER_CRITICAL(&s_engine_lock);
  s_last_stop_reason = reason;
  portEXIT_CRITICAL(&s_engine_lock);
}

uint8_t playback_wave_engine_last_stop_reason(void) {
  uint8_t reason = 0u;
  portENTER_CRITICAL(&s_engine_lock);
  reason = s_last_stop_reason;
  portEXIT_CRITICAL(&s_engine_lock);
  return reason;
}

void playback_wave_engine_stop_all(void) {
  if (!s_initialized) {
    return;
  }

  playback_wave_engine_tick(esp_timer_get_time());

  portENTER_CRITICAL(&s_engine_lock);
  for (uint8_t i = 0; i < PLAYBACK_WAVE_MOTOR_COUNT; ++i) {
    playback_wave_motor_t *motor = &s_motors[i];
    if (!motor->configured) {
      continue;
    }
    if (motor->timer_running) {
      request_timer_stop_locked(i, motor);
    } else {
      enter_stopped_locked(i, motor);
    }
  }
  portEXIT_CRITICAL(&s_engine_lock);

  const int64_t settle_start_us = esp_timer_get_time();
  while ((esp_timer_get_time() - settle_start_us) < (int64_t)PLAYBACK_WAVE_STOP_SETTLE_TIMEOUT_US) {
    playback_wave_engine_tick(esp_timer_get_time());

    bool waiting_for_stop = false;
    portENTER_CRITICAL(&s_engine_lock);
    waiting_for_stop = any_motors_stopping_locked();
    portEXIT_CRITICAL(&s_engine_lock);
    if (!waiting_for_stop) {
      break;
    }
    esp_rom_delay_us(PLAYBACK_WAVE_STOP_POLL_US);
  }

  portENTER_CRITICAL(&s_engine_lock);
  for (uint8_t i = 0; i < PLAYBACK_WAVE_MOTOR_COUNT; ++i) {
    playback_wave_motor_t *motor = &s_motors[i];
    if (!motor->configured) {
      continue;
    }
    enter_stopped_locked(i, motor);
  }
  s_last_control_tick_us = 0;
  portEXIT_CRITICAL(&s_engine_lock);
}

void playback_wave_engine_apply_event_group(const stream_event_group_t *event_group) {
  motor_event_batch_t batch;
  if (!motor_event_executor_from_stream_event_group(event_group, &batch)) {
    return;
  }
  playback_wave_engine_apply_events(&batch);
}

void playback_wave_engine_apply_events(const motor_event_batch_t *batch) {
  if (batch == NULL) {
    return;
  }
  portENTER_CRITICAL(&s_engine_lock);
  for (uint8_t i = 0; i < batch->event_count; ++i) {
    const motor_event_t *event = &batch->events[i];
    stream_motor_change_t change = {
      .motor_idx = event->motor_idx,
      .flags = event->flip_before_restart ? STREAM_EVENT_GROUP_FLAG_FLIP_BEFORE_RESTART : 0u,
      .target_dhz = event->target_dhz,
    };
    apply_change_locked(&change);
  }
  portEXIT_CRITICAL(&s_engine_lock);
}

void playback_wave_engine_set_one_target_exact(uint8_t motor_idx, uint16_t target_dhz, uint32_t ramp_us) {
  if (!s_initialized || motor_idx >= PLAYBACK_WAVE_MOTOR_COUNT) {
    return;
  }
  portENTER_CRITICAL(&s_engine_lock);
  const uint8_t direction = s_motors[motor_idx].current_direction;
  apply_exact_change_locked(motor_idx, target_dhz, ramp_us, direction);
  portEXIT_CRITICAL(&s_engine_lock);
}

void playback_wave_engine_set_one_target_exact_with_direction(
  uint8_t motor_idx,
  uint16_t target_dhz,
  uint32_t ramp_us,
  uint8_t direction
) {
  if (!s_initialized || motor_idx >= PLAYBACK_WAVE_MOTOR_COUNT) {
    return;
  }
  portENTER_CRITICAL(&s_engine_lock);
  apply_exact_change_locked(motor_idx, target_dhz, ramp_us, direction);
  portEXIT_CRITICAL(&s_engine_lock);
}

static bool any_motors_stopping_locked(void) {
  for (uint8_t i = 0; i < PLAYBACK_WAVE_MOTOR_COUNT; ++i) {
    const playback_wave_motor_t *motor = &s_motors[i];
    if (!motor->configured) {
      continue;
    }
    if (motor->timer_running || motor->stop_requested || motor->stop_complete_pending) {
      return true;
    }
  }
  return false;
}

void playback_wave_engine_tick(int64_t now_us) {
  portENTER_CRITICAL(&s_engine_lock);
  if (s_last_control_tick_us > 0 && now_us > s_last_control_tick_us) {
    const uint32_t elapsed_us = (uint32_t)(now_us - s_last_control_tick_us);
    if (elapsed_us > s_control_interval_us) {
      const uint32_t late_us = elapsed_us - s_control_interval_us;
      if (late_us > s_diag.control_late_max_us) {
        s_diag.control_late_max_us = late_us;
      }
      if (elapsed_us > (s_control_interval_us * 2u)) {
        s_diag.control_overrun_count++;
      }
    }
    for (uint8_t i = 0; i < PLAYBACK_WAVE_MOTOR_COUNT; ++i) {
      tick_motor_locked(i, &s_motors[i], elapsed_us);
    }
  }
  s_last_control_tick_us = now_us;
  portEXIT_CRITICAL(&s_engine_lock);
  pulse_accounting_sample();
}

uint8_t playback_wave_engine_active_motor_count(void) {
  uint8_t active = 0u;
  portENTER_CRITICAL(&s_engine_lock);
  for (uint8_t i = 0; i < PLAYBACK_WAVE_MOTOR_COUNT; ++i) {
    const playback_wave_motor_t *motor = &s_motors[i];
    if (motor->state != PLAYBACK_WAVE_STATE_STOPPED || motor->timer_running) {
      active++;
    }
  }
  portEXIT_CRITICAL(&s_engine_lock);
  return active;
}

void playback_wave_engine_reset_step_counts(void) {
  portENTER_CRITICAL(&s_engine_lock);
  for (uint8_t i = 0; i < PLAYBACK_WAVE_MOTOR_COUNT; ++i) {
    s_motor_step_counts[i] = 0u;
    s_motor_position_counts[i] = 0;
    s_motors[i].pulse_accum_us = 0u;
  }
  portEXIT_CRITICAL(&s_engine_lock);
  pulse_accounting_reset();
}

void playback_wave_engine_get_step_counts(uint64_t step_counts[PLAYBACK_WAVE_MOTOR_COUNT]) {
  if (step_counts == NULL) {
    return;
  }
  uint64_t measured_counts[PLAYBACK_WAVE_MOTOR_COUNT] = {0};
  pulse_accounting_get_measured_counts(measured_counts, PLAYBACK_WAVE_MOTOR_COUNT);
  bool any_measured = false;
  for (uint8_t i = 0; i < PLAYBACK_WAVE_MOTOR_COUNT; ++i) {
    if (measured_counts[i] > 0u) {
      any_measured = true;
      break;
    }
  }
  portENTER_CRITICAL(&s_engine_lock);
  for (uint8_t i = 0; i < PLAYBACK_WAVE_MOTOR_COUNT; ++i) {
    step_counts[i] = any_measured ? measured_counts[i] : s_motor_step_counts[i];
  }
  portEXIT_CRITICAL(&s_engine_lock);
}

void playback_wave_engine_get_position_counts(int64_t position_counts[PLAYBACK_WAVE_MOTOR_COUNT]) {
  if (position_counts == NULL) {
    return;
  }
  if (pulse_accounting_has_session_data()) {
    pulse_accounting_get_measured_positions(position_counts, PLAYBACK_WAVE_MOTOR_COUNT);
    return;
  }
  portENTER_CRITICAL(&s_engine_lock);
  for (uint8_t i = 0; i < PLAYBACK_WAVE_MOTOR_COUNT; ++i) {
    position_counts[i] = s_motor_position_counts[i];
  }
  portEXIT_CRITICAL(&s_engine_lock);
}

void playback_wave_engine_reset_diag_counters(void) {
  portENTER_CRITICAL(&s_engine_lock);
  memset(&s_diag, 0, sizeof(s_diag));
  s_last_control_tick_us = 0;
  portEXIT_CRITICAL(&s_engine_lock);
  pulse_accounting_reset();
}

void playback_wave_engine_get_diag_counters(playback_wave_diag_counters_t *diag) {
  if (diag == NULL) {
    return;
  }
  pulse_accounting_stats_t accounting = {0};
  pulse_accounting_get_stats(&accounting);
  portENTER_CRITICAL(&s_engine_lock);
  *diag = s_diag;
  portEXIT_CRITICAL(&s_engine_lock);
  diag->inferred_pulse_total = accounting.inferred_total;
  diag->measured_pulse_total = accounting.measured_total;
  diag->measured_pulse_drift_total = accounting.measured_drift_total;
  diag->measured_pulse_active_mask = accounting.active_mask;
  diag->playback_position_unreliable_mask = accounting.unreliable_mask;
  diag->playback_signed_position_drift_total = accounting.position_drift_total;
}

void playback_wave_engine_get_motor_debug(uint8_t motor_idx, playback_wave_motor_debug_t *debug) {
  if (debug == NULL) {
    return;
  }
  memset(debug, 0, sizeof(*debug));
  if (motor_idx >= PLAYBACK_WAVE_MOTOR_COUNT) {
    return;
  }
  portENTER_CRITICAL(&s_engine_lock);
  const playback_wave_motor_t *motor = &s_motors[motor_idx];
  debug->current_dhz = motor->current_dhz;
  debug->target_dhz = motor->target_dhz;
  debug->final_target_dhz = motor->final_target_dhz;
  debug->exact_accel_dhz_per_s = motor->exact_accel_dhz_per_s;
  debug->last_period_ticks = motor->last_period_ticks;
  debug->pulse_accum_us = motor->pulse_accum_us;
  debug->state = (uint8_t)motor->state;
  debug->current_direction = motor->current_direction;
  debug->timer_running = motor->timer_running ? 1u : 0u;
  debug->stop_requested = motor->stop_requested ? 1u : 0u;
  debug->exact_mode = motor->exact_mode ? 1u : 0u;
  portEXIT_CRITICAL(&s_engine_lock);
}

uint32_t playback_wave_engine_control_interval_us(void) {
  return s_control_interval_us;
}

motion_backend_capabilities_t playback_wave_engine_capabilities(void) {
  return motion_backend_playback_capabilities();
}

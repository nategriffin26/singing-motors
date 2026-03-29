#include "pulse_engine.h"

#include "driver/gpio.h"
#include "driver/gptimer.h"
#include "esp_attr.h"
#include "freertos/FreeRTOS.h"
#include "soc/gpio_struct.h"

/*
 * Exact-step warmups/home/step-motion were stable on the older half-period
 * interpolation engine. Playback now uses playback_wave_engine instead, so we
 * keep pulse_engine focused on exact motion reliability.
 */

static const DRAM_ATTR gpio_num_t s_step_pins[MOTOR_COUNT] = {
  GPIO_NUM_16,
  GPIO_NUM_17,
  GPIO_NUM_18,
  GPIO_NUM_19,
  GPIO_NUM_21,
  GPIO_NUM_22,
  GPIO_NUM_23,
  GPIO_NUM_25,
};

static const DRAM_ATTR gpio_num_t s_dir_pins[PLAYBACK_MOTOR_COUNT] = {
  GPIO_NUM_4,
  GPIO_NUM_13,
  GPIO_NUM_14,
  GPIO_NUM_26,
  GPIO_NUM_27,
  GPIO_NUM_32,
};

typedef struct {
  uint32_t half_period_us;
  uint32_t start_half_period_us;
  uint32_t target_half_period_us;
  uint32_t ramp_total_us;
  uint32_t ramp_elapsed_us;
  uint32_t accum_us;
  uint8_t level;
  uint8_t current_direction;
  bool stop_after_ramp;
  int32_t ramp_hp_per_us_q16;
} pulse_motor_state_t;

static pulse_motor_state_t s_motor_state[MOTOR_COUNT];
static uint64_t s_motor_step_counts[MOTOR_COUNT];
static int64_t s_motor_position_counts[MOTOR_COUNT];
static bool s_motor_position_lost[MOTOR_COUNT];
static uint16_t s_last_requested_freq_dhz[MOTOR_COUNT];
static uint32_t s_last_requested_ramp_us[MOTOR_COUNT];
static uint8_t s_last_requested_exact[MOTOR_COUNT];
static gptimer_handle_t s_pulse_timer = NULL;
static uint64_t s_last_count_us = 0u;
static uint32_t s_pulse_late_max_us = 0u;
static uint32_t s_pulse_edge_drop_count = 0u;
static uint32_t s_pulse_timebase_rebase_count = 0u;
static uint32_t s_pulse_timebase_rebase_lost_us = 0u;
static uint32_t s_pulse_target_update_count = 0u;
static uint32_t s_pulse_ramp_change_count = 0u;
static uint32_t s_pulse_stop_after_ramp_count = 0u;
static portMUX_TYPE s_engine_lock = portMUX_INITIALIZER_UNLOCKED;

#define MICROSTEP_RATIO 16u
#define PULSE_ENGINE_STOP_RAMP_HALF_PERIOD_US (50000u)

static inline void IRAM_ATTR set_step_pin_level(gpio_num_t step_pin, uint8_t level) {
  const uint32_t pin = (uint32_t)step_pin;
  if (pin < 32u) {
    const uint32_t mask = (1u << pin);
    if (level != 0u) {
      GPIO.out_w1ts = mask;
    } else {
      GPIO.out_w1tc = mask;
    }
    return;
  }

  const uint32_t mask_hi = (1u << (pin - 32u));
  if (level != 0u) {
    GPIO.out1_w1ts.val = mask_hi;
  } else {
    GPIO.out1_w1tc.val = mask_hi;
  }
}

static inline void IRAM_ATTR set_dir_pin_level(gpio_num_t dir_pin, uint8_t level) {
  const uint32_t pin = (uint32_t)dir_pin;
  if (pin < 32u) {
    const uint32_t mask = (1u << pin);
    if (level != 0u) {
      GPIO.out_w1ts = mask;
    } else {
      GPIO.out_w1tc = mask;
    }
    return;
  }

  const uint32_t mask_hi = (1u << (pin - 32u));
  if (level != 0u) {
    GPIO.out1_w1ts.val = mask_hi;
  } else {
    GPIO.out1_w1tc.val = mask_hi;
  }
}

static inline void set_motor_direction(uint8_t motor_idx, uint8_t direction) {
  if (motor_idx < PLAYBACK_MOTOR_COUNT) {
    set_dir_pin_level(s_dir_pins[motor_idx], direction);
  }
  s_motor_state[motor_idx].current_direction = direction;
}

static inline void force_motor_stop(pulse_motor_state_t *m, gpio_num_t step_pin) {
  m->half_period_us = 0u;
  m->start_half_period_us = 0u;
  m->target_half_period_us = 0u;
  m->ramp_total_us = 0u;
  m->ramp_elapsed_us = 0u;
  m->accum_us = 0u;
  m->level = 0u;
  m->current_direction = 0u;
  m->stop_after_ramp = false;
  m->ramp_hp_per_us_q16 = 0;
  set_step_pin_level(step_pin, 0u);
}

static inline void saturating_add_u32(uint32_t *value, uint32_t delta) {
  if (value == NULL || delta == 0u) {
    return;
  }
  if ((UINT32_MAX - *value) < delta) {
    *value = UINT32_MAX;
    return;
  }
  *value += delta;
}

static uint32_t freq_to_half_period_us(uint16_t freq_dhz) {
  if (freq_dhz == 0u) {
    return 0u;
  }
  uint32_t half_period_us = 5000000u / ((uint32_t)freq_dhz * MICROSTEP_RATIO);
  if (half_period_us < PULSE_ENGINE_MIN_HALF_PERIOD_US) {
    half_period_us = PULSE_ENGINE_MIN_HALF_PERIOD_US;
  }
  return half_period_us;
}

static inline uint32_t interpolate_half_period(const pulse_motor_state_t *m) {
  if (m->ramp_total_us == 0u || m->ramp_elapsed_us >= m->ramp_total_us) {
    return m->target_half_period_us;
  }
  const int32_t scaled = (int32_t)(
    ((int64_t)m->ramp_hp_per_us_q16 * (int64_t)m->ramp_elapsed_us) >> 16
  );
  const int32_t interpolated = (int32_t)m->start_half_period_us + scaled;
  if (interpolated < (int32_t)PULSE_ENGINE_MIN_HALF_PERIOD_US) {
    return PULSE_ENGINE_MIN_HALF_PERIOD_US;
  }
  return (uint32_t)interpolated;
}

static void pulse_engine_advance_elapsed_locked(uint32_t elapsed_us) {
  if (elapsed_us == 0u) {
    return;
  }

  for (uint8_t i = 0; i < MOTOR_COUNT; ++i) {
    pulse_motor_state_t *m = &s_motor_state[i];

    if (m->ramp_total_us > 0u && m->ramp_elapsed_us < m->ramp_total_us) {
      const uint64_t ramp_elapsed = (uint64_t)m->ramp_elapsed_us + (uint64_t)elapsed_us;
      m->ramp_elapsed_us = (ramp_elapsed >= (uint64_t)m->ramp_total_us)
                             ? m->ramp_total_us
                             : (uint32_t)ramp_elapsed;
    }
    if (m->stop_after_ramp && (m->ramp_total_us == 0u || m->ramp_elapsed_us >= m->ramp_total_us)) {
      force_motor_stop(m, s_step_pins[i]);
      continue;
    }

    const uint32_t new_hp = interpolate_half_period(m);
    if (new_hp != m->half_period_us && m->half_period_us > 0u && new_hp > 0u) {
      const uint64_t scaled = ((uint64_t)m->accum_us * (uint64_t)new_hp) /
                              (uint64_t)m->half_period_us;
      m->accum_us = (scaled >= new_hp) ? (new_hp - 1u) : (uint32_t)scaled;
    }
    m->half_period_us = new_hp;

    if (m->half_period_us == 0u) {
      continue;
    }

    const uint64_t accum_total = (uint64_t)m->accum_us + (uint64_t)elapsed_us;
    const uint32_t toggles = (uint32_t)(accum_total / (uint64_t)m->half_period_us);
    m->accum_us = (uint32_t)(accum_total % (uint64_t)m->half_period_us);

    if (toggles > 1u) {
      saturating_add_u32(&s_pulse_edge_drop_count, toggles - 1u);
      s_motor_position_lost[i] = true;
    }
    if (toggles > 0u && ((toggles & 0x01u) != 0u)) {
      m->level ^= 1u;
      set_step_pin_level(s_step_pins[i], m->level);
      if (m->level != 0u) {
        s_motor_step_counts[i]++;
        if (m->current_direction == 0u) {
          s_motor_position_counts[i]++;
        } else {
          s_motor_position_counts[i]--;
        }
      }
    }
  }
}

static bool IRAM_ATTR pulse_timer_on_alarm_cb(
  gptimer_handle_t timer,
  const gptimer_alarm_event_data_t *edata,
  void *user_ctx
) {
  (void)user_ctx;
  if ((timer == NULL) || (edata == NULL)) {
    return false;
  }

  const uint64_t now_count_us = edata->count_value;
  const uint64_t scheduled_alarm_us = edata->alarm_value;

  portENTER_CRITICAL_ISR(&s_engine_lock);

  if (now_count_us > scheduled_alarm_us) {
    uint64_t late_us = now_count_us - scheduled_alarm_us;
    if (late_us > (uint64_t)UINT32_MAX) {
      late_us = UINT32_MAX;
    }
    if ((uint32_t)late_us > s_pulse_late_max_us) {
      s_pulse_late_max_us = (uint32_t)late_us;
    }
  }

  uint32_t elapsed_us = 0u;
  if ((s_last_count_us != 0u) && (now_count_us > s_last_count_us)) {
    uint64_t delta = now_count_us - s_last_count_us;
    if (delta > (uint64_t)UINT32_MAX) {
      delta = UINT32_MAX;
    }
    elapsed_us = (uint32_t)delta;
  }
  s_last_count_us = now_count_us;

  bool has_active = false;
  uint32_t next_interval_us = 0u;

  for (uint8_t i = 0; i < MOTOR_COUNT; ++i) {
    pulse_motor_state_t *m = &s_motor_state[i];

    if (m->ramp_total_us > 0u && m->ramp_elapsed_us < m->ramp_total_us) {
      m->ramp_elapsed_us += elapsed_us;
      if (m->ramp_elapsed_us >= m->ramp_total_us) {
        m->ramp_elapsed_us = m->ramp_total_us;
      }
    }
    if (m->stop_after_ramp && (m->ramp_total_us == 0u || m->ramp_elapsed_us >= m->ramp_total_us)) {
      force_motor_stop(m, s_step_pins[i]);
      continue;
    }

    const uint32_t new_hp = interpolate_half_period(m);
    if (new_hp != m->half_period_us && m->half_period_us > 0u && new_hp > 0u) {
      const uint64_t scaled = ((uint64_t)m->accum_us * (uint64_t)new_hp) /
                              (uint64_t)m->half_period_us;
      m->accum_us = (scaled >= new_hp) ? (new_hp - 1u) : (uint32_t)scaled;
    }
    m->half_period_us = new_hp;

    if (m->half_period_us == 0u) {
      continue;
    }

    has_active = true;

    m->accum_us += elapsed_us;
    uint32_t toggles = 0u;
    if (m->accum_us >= m->half_period_us) {
      m->accum_us -= m->half_period_us;
      toggles = 1u;
      if (m->accum_us >= m->half_period_us) {
        const uint32_t extra = m->accum_us / m->half_period_us;
        m->accum_us -= extra * m->half_period_us;
        saturating_add_u32(&s_pulse_edge_drop_count, extra);
        toggles += extra;
      }
    }

    if (toggles > 1u) {
      s_motor_position_lost[i] = true;
    }

    if (toggles > 0u && ((toggles & 0x01u) != 0u)) {
      m->level ^= 1u;
      set_step_pin_level(s_step_pins[i], m->level);
      if (m->level != 0u) {
        s_motor_step_counts[i]++;
        if (m->current_direction == 0u) {
          s_motor_position_counts[i]++;
        } else {
          s_motor_position_counts[i]--;
        }
      }
    }

    uint32_t until_next_toggle = m->half_period_us - m->accum_us;
    if (until_next_toggle == 0u) {
      until_next_toggle = 1u;
    }
    if ((next_interval_us == 0u) || (until_next_toggle < next_interval_us)) {
      next_interval_us = until_next_toggle;
    }
  }

  if (!has_active) {
    (void)gptimer_set_alarm_action(timer, NULL);
    portEXIT_CRITICAL_ISR(&s_engine_lock);
    return false;
  }

  gptimer_alarm_config_t alarm_cfg = {
    .reload_count = 0u,
    .alarm_count = now_count_us + (uint64_t)next_interval_us,
    .flags = {
      .auto_reload_on_alarm = false,
    },
  };
  (void)gptimer_set_alarm_action(timer, &alarm_cfg);
  portEXIT_CRITICAL_ISR(&s_engine_lock);
  return false;
}

esp_err_t pulse_engine_init(void) {
  gpio_config_t io_cfg = {
    .pin_bit_mask = 0,
    .mode = GPIO_MODE_OUTPUT,
    .pull_up_en = GPIO_PULLUP_DISABLE,
    .pull_down_en = GPIO_PULLDOWN_DISABLE,
    .intr_type = GPIO_INTR_DISABLE,
  };

  for (uint8_t i = 0; i < MOTOR_COUNT; ++i) {
    io_cfg.pin_bit_mask |= (1ULL << s_step_pins[i]);
  }
  for (uint8_t i = 0; i < PLAYBACK_MOTOR_COUNT; ++i) {
    io_cfg.pin_bit_mask |= (1ULL << s_dir_pins[i]);
  }

  esp_err_t err = gpio_config(&io_cfg);
  if (err != ESP_OK) {
    return err;
  }

  for (uint8_t i = 0; i < MOTOR_COUNT; ++i) {
    s_motor_state[i].half_period_us = 0u;
    s_motor_state[i].start_half_period_us = 0u;
    s_motor_state[i].target_half_period_us = 0u;
    s_motor_state[i].ramp_total_us = 0u;
    s_motor_state[i].ramp_elapsed_us = 0u;
    s_motor_state[i].accum_us = 0u;
    s_motor_state[i].level = 0u;
    s_motor_state[i].current_direction = 0u;
    s_motor_state[i].stop_after_ramp = false;
    s_motor_state[i].ramp_hp_per_us_q16 = 0;
    s_motor_step_counts[i] = 0u;
    s_motor_position_counts[i] = 0;
    s_motor_position_lost[i] = false;
    s_last_requested_freq_dhz[i] = 0u;
    s_last_requested_ramp_us[i] = 0u;
    s_last_requested_exact[i] = 0u;
    set_step_pin_level(s_step_pins[i], 0u);
    if (i < PLAYBACK_MOTOR_COUNT) {
      set_dir_pin_level(s_dir_pins[i], 0u);
    }
  }
  s_last_count_us = 0u;
  s_pulse_late_max_us = 0u;
  s_pulse_edge_drop_count = 0u;
  s_pulse_timebase_rebase_count = 0u;
  s_pulse_timebase_rebase_lost_us = 0u;
  s_pulse_target_update_count = 0u;
  s_pulse_ramp_change_count = 0u;
  s_pulse_stop_after_ramp_count = 0u;

  gptimer_config_t timer_cfg = {
    .clk_src = GPTIMER_CLK_SRC_DEFAULT,
    .direction = GPTIMER_COUNT_UP,
    .resolution_hz = PULSE_ENGINE_TIMER_RESOLUTION_HZ,
  };
  err = gptimer_new_timer(&timer_cfg, &s_pulse_timer);
  if (err != ESP_OK) {
    return err;
  }

  gptimer_event_callbacks_t callbacks = {
    .on_alarm = pulse_timer_on_alarm_cb,
  };
  err = gptimer_register_event_callbacks(s_pulse_timer, &callbacks, NULL);
  if (err != ESP_OK) {
    return err;
  }

  err = gptimer_enable(s_pulse_timer);
  if (err != ESP_OK) {
    return err;
  }
  err = gptimer_start(s_pulse_timer);
  if (err != ESP_OK) {
    return err;
  }

  uint64_t now_count_us = 0u;
  (void)gptimer_get_raw_count(s_pulse_timer, &now_count_us);
  s_last_count_us = now_count_us;
  (void)gptimer_set_alarm_action(s_pulse_timer, NULL);
  return ESP_OK;
}

static void pulse_engine_schedule_from_now(uint64_t now_count_us) {
  if (s_pulse_timer == NULL) {
    return;
  }

  bool has_active = false;
  uint32_t next_interval_us = 0u;

  portENTER_CRITICAL(&s_engine_lock);
  for (uint8_t i = 0; i < MOTOR_COUNT; ++i) {
    const uint32_t half_period_us = s_motor_state[i].half_period_us;
    if (half_period_us == 0u) {
      if (s_motor_state[i].target_half_period_us > 0u &&
          s_motor_state[i].ramp_elapsed_us < s_motor_state[i].ramp_total_us) {
        const uint32_t ramp_tick = 100u;
        if (!has_active || ramp_tick < next_interval_us) {
          has_active = true;
          next_interval_us = ramp_tick;
        }
      }
      continue;
    }
    uint32_t until_next_toggle = half_period_us - s_motor_state[i].accum_us;
    if (until_next_toggle == 0u) {
      until_next_toggle = 1u;
    }
    if (!has_active || until_next_toggle < next_interval_us) {
      has_active = true;
      next_interval_us = until_next_toggle;
    }
  }

  if (!has_active) {
    (void)gptimer_set_alarm_action(s_pulse_timer, NULL);
    portEXIT_CRITICAL(&s_engine_lock);
    return;
  }

  gptimer_alarm_config_t alarm_cfg = {
    .reload_count = 0u,
    .alarm_count = now_count_us + (uint64_t)next_interval_us,
    .flags = {
      .auto_reload_on_alarm = false,
    },
  };
  (void)gptimer_set_alarm_action(s_pulse_timer, &alarm_cfg);
  portEXIT_CRITICAL(&s_engine_lock);
}

static uint32_t compute_ramp_us(uint16_t from_dhz, uint16_t to_dhz) {
  if (PULSE_ENGINE_MAX_ACCEL_DHZ_PER_S == 0u) {
    return 0u;
  }
  uint32_t delta_dhz;
  if (to_dhz > from_dhz) {
    delta_dhz = (uint32_t)to_dhz - (uint32_t)from_dhz;
  } else {
    delta_dhz = (uint32_t)from_dhz - (uint32_t)to_dhz;
  }
  if (delta_dhz == 0u) {
    return 0u;
  }
  const uint64_t ramp_us_64 = ((uint64_t)delta_dhz * 1000000u) /
                               (uint64_t)PULSE_ENGINE_MAX_ACCEL_DHZ_PER_S;
  uint32_t ramp_us = (ramp_us_64 > (uint64_t)UINT32_MAX) ? UINT32_MAX : (uint32_t)ramp_us_64;
  if (ramp_us < 500u) {
    ramp_us = 0u;
  }
  return ramp_us;
}

static uint16_t half_period_to_approx_dhz(uint32_t half_period_us) {
  if (half_period_us == 0u) {
    return 0u;
  }
  uint32_t dhz = 5000000u / (half_period_us * MICROSTEP_RATIO);
  if (dhz > 0xFFFFu) {
    dhz = 0xFFFFu;
  }
  return (uint16_t)dhz;
}

static inline int32_t compute_ramp_rate_q16(uint32_t start_hp, uint32_t target_hp, uint32_t ramp_us) {
  if (ramp_us == 0u) {
    return 0;
  }
  return (int32_t)(
    ((int64_t)((int32_t)target_hp - (int32_t)start_hp) << 16) / (int64_t)ramp_us
  );
}

static uint64_t pulse_engine_sync_now_locked(void) {
  uint64_t now_count_us = s_last_count_us;
  if (s_pulse_timer != NULL) {
    (void)gptimer_get_raw_count(s_pulse_timer, &now_count_us);
  }
  if (now_count_us < s_last_count_us) {
    s_pulse_timebase_rebase_count++;
    uint64_t lost_us = s_last_count_us - now_count_us;
    if (lost_us > (uint64_t)UINT32_MAX) {
      lost_us = UINT32_MAX;
    }
    saturating_add_u32(&s_pulse_timebase_rebase_lost_us, (uint32_t)lost_us);
    now_count_us = s_last_count_us;
  } else if (now_count_us > s_last_count_us) {
    uint64_t elapsed_total = now_count_us - s_last_count_us;
    while (elapsed_total > 0u) {
      const uint32_t elapsed_chunk = (elapsed_total > (uint64_t)UINT32_MAX)
                                       ? UINT32_MAX
                                       : (uint32_t)elapsed_total;
      pulse_engine_advance_elapsed_locked(elapsed_chunk);
      elapsed_total -= (uint64_t)elapsed_chunk;
    }
    s_last_count_us = now_count_us;
  }
  return now_count_us;
}

static void pulse_engine_apply_target_locked(
  uint8_t motor_idx,
  uint16_t freq_dhz,
  uint32_t ramp_us,
  bool exact_ramp
) {
  pulse_motor_state_t *m = &s_motor_state[motor_idx];
  s_last_requested_freq_dhz[motor_idx] = freq_dhz;
  s_last_requested_ramp_us[motor_idx] = ramp_us;
  s_last_requested_exact[motor_idx] = exact_ramp ? 1u : 0u;
  const uint32_t new_target_hp = freq_to_half_period_us(freq_dhz);
  const bool exact_zero_ramp_lock_in = exact_ramp && ramp_us == 0u;

  if (new_target_hp == m->half_period_us &&
      new_target_hp == m->target_half_period_us &&
      !exact_zero_ramp_lock_in) {
    return;
  }
  s_pulse_target_update_count++;

  if (new_target_hp == 0u) {
    if (ramp_us == 0u || m->half_period_us == 0u) {
      force_motor_stop(m, s_step_pins[motor_idx]);
      return;
    }

    const uint32_t stop_target_hp = (m->half_period_us > PULSE_ENGINE_STOP_RAMP_HALF_PERIOD_US)
                                      ? m->half_period_us
                                      : PULSE_ENGINE_STOP_RAMP_HALF_PERIOD_US;
    uint32_t ramp = ramp_us;
    if (!exact_ramp) {
      const uint16_t current_dhz = half_period_to_approx_dhz(m->half_period_us);
      const uint16_t stop_dhz = half_period_to_approx_dhz(stop_target_hp);
      ramp = compute_ramp_us(current_dhz, stop_dhz);
      if (ramp > ramp_us) {
        ramp = ramp_us;
      }
    }
    if (ramp == 0u) {
      force_motor_stop(m, s_step_pins[motor_idx]);
      return;
    }

    m->start_half_period_us = m->half_period_us;
    m->target_half_period_us = stop_target_hp;
    m->ramp_total_us = ramp;
    m->ramp_elapsed_us = 0u;
    m->ramp_hp_per_us_q16 = compute_ramp_rate_q16(m->half_period_us, stop_target_hp, ramp);
    m->stop_after_ramp = true;
    s_pulse_ramp_change_count++;
    s_pulse_stop_after_ramp_count++;
    return;
  }

  if (m->half_period_us == 0u) {
    if (ramp_us == 0u) {
      m->half_period_us = new_target_hp;
      m->start_half_period_us = new_target_hp;
      m->target_half_period_us = new_target_hp;
      m->ramp_total_us = 0u;
      m->ramp_elapsed_us = 0u;
      m->accum_us = 0u;
      m->level = 0u;
      m->stop_after_ramp = false;
      m->ramp_hp_per_us_q16 = 0;
      set_step_pin_level(s_step_pins[motor_idx], 0u);
      return;
    }

    uint32_t start_hp = new_target_hp * 4u;
    if (start_hp > 50000u) {
      start_hp = 50000u;
    }

    uint32_t ramp = ramp_us;
    if (!exact_ramp) {
      const uint16_t start_dhz = half_period_to_approx_dhz(start_hp);
      ramp = compute_ramp_us(start_dhz, freq_dhz);
      if (ramp > ramp_us) {
        ramp = ramp_us;
      }
    }

    m->start_half_period_us = start_hp;
    m->half_period_us = start_hp;
    m->target_half_period_us = new_target_hp;
    m->ramp_total_us = ramp;
    m->ramp_elapsed_us = 0u;
    m->ramp_hp_per_us_q16 = compute_ramp_rate_q16(start_hp, new_target_hp, ramp);
    m->accum_us = 0u;
    m->level = 0u;
    m->stop_after_ramp = false;
    set_step_pin_level(s_step_pins[motor_idx], 0u);
    if (ramp > 0u) {
      s_pulse_ramp_change_count++;
    }
    return;
  }

  uint32_t ramp = ramp_us;
  if (!exact_ramp) {
    const uint16_t current_dhz = half_period_to_approx_dhz(m->half_period_us);
    ramp = compute_ramp_us(current_dhz, freq_dhz);
    if (ramp > ramp_us) {
      ramp = ramp_us;
    }
  }

  m->start_half_period_us = m->half_period_us;
  m->target_half_period_us = new_target_hp;
  m->stop_after_ramp = false;
  if (ramp > 0u) {
    m->ramp_total_us = ramp;
    m->ramp_elapsed_us = 0u;
    m->ramp_hp_per_us_q16 = compute_ramp_rate_q16(m->start_half_period_us, new_target_hp, ramp);
    s_pulse_ramp_change_count++;
    return;
  }

  uint64_t scaled_accum = 0u;
  if (m->half_period_us > 0u) {
    scaled_accum = ((uint64_t)m->accum_us * (uint64_t)new_target_hp) /
                   (uint64_t)m->half_period_us;
  }
  m->half_period_us = new_target_hp;
  m->accum_us = (scaled_accum >= new_target_hp) ? (new_target_hp - 1u) : (uint32_t)scaled_accum;
  m->start_half_period_us = new_target_hp;
  m->target_half_period_us = new_target_hp;
  m->ramp_total_us = 0u;
  m->ramp_elapsed_us = 0u;
  m->ramp_hp_per_us_q16 = 0;
}

void pulse_engine_set_targets(const uint16_t freq_dhz[MOTOR_COUNT], uint32_t max_ramp_us) {
  pulse_engine_set_targets_with_flips(freq_dhz, 0u, max_ramp_us);
}

void pulse_engine_set_targets_with_flips(
  const uint16_t freq_dhz[MOTOR_COUNT],
  uint8_t direction_flip_mask,
  uint32_t max_ramp_us
) {
  if (freq_dhz == NULL) {
    return;
  }
  portENTER_CRITICAL(&s_engine_lock);
  const uint64_t now_count_us = pulse_engine_sync_now_locked();

  for (uint8_t i = 0; i < MOTOR_COUNT; ++i) {
    uint8_t direction = s_motor_state[i].current_direction;
    if ((direction_flip_mask & (1u << i)) != 0u) {
      direction ^= 0x01u;
    }
    set_motor_direction(i, direction);
    pulse_engine_apply_target_locked(i, freq_dhz[i], max_ramp_us, false);
  }
  portEXIT_CRITICAL(&s_engine_lock);

  if (s_pulse_timer != NULL) {
    pulse_engine_schedule_from_now(now_count_us);
  }
}

void pulse_engine_set_one_target(uint8_t motor_idx, uint16_t freq_dhz, uint32_t max_ramp_us) {
  pulse_engine_set_one_target_with_direction(motor_idx, freq_dhz, max_ramp_us, 0u);
}

void pulse_engine_set_one_target_with_direction(
  uint8_t motor_idx,
  uint16_t freq_dhz,
  uint32_t max_ramp_us,
  uint8_t direction
) {
  if (motor_idx >= MOTOR_COUNT) {
    return;
  }
  portENTER_CRITICAL(&s_engine_lock);
  const uint64_t now_count_us = pulse_engine_sync_now_locked();
  set_motor_direction(motor_idx, direction != 0u ? 1u : 0u);
  pulse_engine_apply_target_locked(motor_idx, freq_dhz, max_ramp_us, false);
  portEXIT_CRITICAL(&s_engine_lock);
  if (s_pulse_timer != NULL) {
    pulse_engine_schedule_from_now(now_count_us);
  }
}

void pulse_engine_set_one_target_exact(uint8_t motor_idx, uint16_t freq_dhz, uint32_t ramp_us) {
  pulse_engine_set_one_target_exact_with_direction(motor_idx, freq_dhz, ramp_us, 0u);
}

void pulse_engine_set_one_target_exact_with_direction(
  uint8_t motor_idx,
  uint16_t freq_dhz,
  uint32_t ramp_us,
  uint8_t direction
) {
  if (motor_idx >= MOTOR_COUNT) {
    return;
  }
  portENTER_CRITICAL(&s_engine_lock);
  const uint64_t now_count_us = pulse_engine_sync_now_locked();
  set_motor_direction(motor_idx, direction != 0u ? 1u : 0u);
  pulse_engine_apply_target_locked(motor_idx, freq_dhz, ramp_us, true);
  portEXIT_CRITICAL(&s_engine_lock);
  if (s_pulse_timer != NULL) {
    pulse_engine_schedule_from_now(now_count_us);
  }
}

void pulse_engine_stop_all(void) {
  uint16_t zero_freq[MOTOR_COUNT] = {0};
  pulse_engine_set_targets(zero_freq, 0u);
}

void pulse_engine_reset_step_counts(void) {
  portENTER_CRITICAL(&s_engine_lock);
  for (uint8_t i = 0; i < MOTOR_COUNT; ++i) {
    s_motor_step_counts[i] = 0u;
    s_motor_position_counts[i] = 0;
  }
  portEXIT_CRITICAL(&s_engine_lock);
}

void pulse_engine_get_step_counts(uint64_t step_counts[MOTOR_COUNT]) {
  if (step_counts == NULL) {
    return;
  }
  portENTER_CRITICAL(&s_engine_lock);
  for (uint8_t i = 0; i < MOTOR_COUNT; ++i) {
    step_counts[i] = s_motor_step_counts[i];
  }
  portEXIT_CRITICAL(&s_engine_lock);
}

void pulse_engine_get_position_counts(int64_t position_counts[MOTOR_COUNT]) {
  if (position_counts == NULL) {
    return;
  }
  portENTER_CRITICAL(&s_engine_lock);
  for (uint8_t i = 0; i < MOTOR_COUNT; ++i) {
    position_counts[i] = s_motor_position_counts[i];
  }
  portEXIT_CRITICAL(&s_engine_lock);
}

void pulse_engine_reset_diag_counters(void) {
  portENTER_CRITICAL(&s_engine_lock);
  s_pulse_late_max_us = 0u;
  s_pulse_edge_drop_count = 0u;
  s_pulse_timebase_rebase_count = 0u;
  s_pulse_timebase_rebase_lost_us = 0u;
  s_pulse_target_update_count = 0u;
  s_pulse_ramp_change_count = 0u;
  s_pulse_stop_after_ramp_count = 0u;
  for (uint8_t i = 0; i < MOTOR_COUNT; ++i) {
    s_motor_position_lost[i] = false;
  }
  portEXIT_CRITICAL(&s_engine_lock);
}

void pulse_engine_get_diag_counters(pulse_engine_diag_counters_t *diag) {
  if (diag == NULL) {
    return;
  }
  portENTER_CRITICAL(&s_engine_lock);
  diag->pulse_late_max_us = s_pulse_late_max_us;
  diag->pulse_edge_drop_count = s_pulse_edge_drop_count;
  diag->pulse_timebase_rebase_count = s_pulse_timebase_rebase_count;
  diag->pulse_timebase_rebase_lost_us = s_pulse_timebase_rebase_lost_us;
  diag->pulse_target_update_count = s_pulse_target_update_count;
  diag->pulse_ramp_change_count = s_pulse_ramp_change_count;
  diag->pulse_stop_after_ramp_count = s_pulse_stop_after_ramp_count;
  uint32_t lost_mask = 0u;
  for (uint8_t i = 0; i < MOTOR_COUNT; ++i) {
    if (s_motor_position_lost[i]) {
      lost_mask |= (1u << i);
    }
  }
  diag->pulse_position_lost_mask = lost_mask;
  portEXIT_CRITICAL(&s_engine_lock);
}

void pulse_engine_get_motor_debug(uint8_t motor_idx, pulse_engine_motor_debug_t *debug) {
  if (debug == NULL || motor_idx >= MOTOR_COUNT) {
    return;
  }
  portENTER_CRITICAL(&s_engine_lock);
  debug->half_period_us = s_motor_state[motor_idx].half_period_us;
  debug->target_half_period_us = s_motor_state[motor_idx].target_half_period_us;
  debug->accum_us = s_motor_state[motor_idx].accum_us;
  debug->ramp_total_us = s_motor_state[motor_idx].ramp_total_us;
  debug->ramp_elapsed_us = s_motor_state[motor_idx].ramp_elapsed_us;
  debug->last_requested_ramp_us = s_last_requested_ramp_us[motor_idx];
  debug->last_requested_freq_dhz = s_last_requested_freq_dhz[motor_idx];
  debug->level = s_motor_state[motor_idx].level;
  debug->current_direction = s_motor_state[motor_idx].current_direction;
  debug->stop_after_ramp = s_motor_state[motor_idx].stop_after_ramp ? 1u : 0u;
  debug->last_requested_exact = s_last_requested_exact[motor_idx];
  portEXIT_CRITICAL(&s_engine_lock);
}

motion_backend_capabilities_t pulse_engine_capabilities(void) {
  return (motion_backend_capabilities_t){
    .kind = MOTION_BACKEND_KIND_PULSE_EXACT,
    .backend_id = MOTION_BACKEND_EXACT_ID,
    .motor_count = MOTOR_COUNT,
    .supports_continuous_playback = false,
    .supports_exact_steps = true,
    .supports_direction_flips = true,
  };
}

#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "driver/gpio.h"
#include "esp_err.h"

typedef struct {
  uint32_t inferred_total;
  uint32_t measured_total;
  uint32_t measured_drift_total;
  uint32_t sample_count;
  uint32_t active_mask;
  uint32_t unreliable_mask;
  uint32_t position_drift_total;
} pulse_accounting_stats_t;

esp_err_t pulse_accounting_init(const gpio_num_t *step_pins, const gpio_num_t *dir_pins, uint8_t motor_count);
void pulse_accounting_begin_session(void);
void pulse_accounting_end_session(void);
void pulse_accounting_reset(void);
void pulse_accounting_record_inferred_steps(uint8_t motor_idx, uint32_t emitted_steps, uint8_t direction);
void pulse_accounting_sample(void);
void pulse_accounting_get_measured_counts(uint64_t *counts, uint8_t motor_count);
void pulse_accounting_get_measured_positions(int64_t *positions, uint8_t motor_count);
bool pulse_accounting_has_session_data(void);
void pulse_accounting_get_stats(pulse_accounting_stats_t *stats);

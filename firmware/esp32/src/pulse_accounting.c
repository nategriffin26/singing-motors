#include "pulse_accounting.h"

#include <string.h>

#include "esp_check.h"
#include "driver/pulse_cnt.h"

#include "protocol_defs.h"

#define PULSE_ACCOUNTING_HIGH_LIMIT (30000)
#define PULSE_ACCOUNTING_LOW_LIMIT (-1)

typedef struct {
  gpio_num_t step_pin;
  gpio_num_t dir_pin;
  bool configured;
  pcnt_unit_handle_t unit;
  pcnt_channel_handle_t channel;
  int last_raw_count;
  uint32_t measured_count;
  int64_t measured_position_count;
  uint32_t inferred_count;
  int64_t inferred_position_count;
  bool unreliable;
} pulse_accounting_channel_t;

static pulse_accounting_channel_t s_channels[PLAYBACK_MOTOR_COUNT];
static uint8_t s_motor_count = 0u;
static bool s_initialized = false;
static bool s_session_active = false;
static bool s_session_has_data = false;

static uint32_t abs_u32_from_int64(int64_t value) {
  uint64_t magnitude = (value >= 0) ? (uint64_t)value : (uint64_t)(-value);
  if (magnitude > (uint64_t)UINT32_MAX) {
    return UINT32_MAX;
  }
  return (uint32_t)magnitude;
}

static uint32_t saturating_add_u32(uint32_t lhs, uint32_t rhs) {
  uint64_t total = (uint64_t)lhs + (uint64_t)rhs;
  if (total > (uint64_t)UINT32_MAX) {
    return UINT32_MAX;
  }
  return (uint32_t)total;
}

static void pulse_accounting_sample_locked(void) {
  if (!s_initialized || !s_session_active) {
    return;
  }
  for (uint8_t i = 0; i < s_motor_count; ++i) {
    pulse_accounting_channel_t *channel = &s_channels[i];
    if (!channel->configured || channel->unit == NULL) {
      continue;
    }
    int raw_count = 0;
    if (pcnt_unit_get_count(channel->unit, &raw_count) != ESP_OK) {
      channel->unreliable = true;
      continue;
    }
    const int delta = raw_count - channel->last_raw_count;
    if (delta != 0) {
      channel->measured_count = saturating_add_u32(channel->measured_count, abs_u32_from_int64((int64_t)delta));
      channel->measured_position_count += (int64_t)delta;
    }
    channel->last_raw_count = raw_count;
  }
}

esp_err_t pulse_accounting_init(const gpio_num_t *step_pins, const gpio_num_t *dir_pins, uint8_t motor_count) {
  if (step_pins == NULL || dir_pins == NULL || motor_count == 0u || motor_count > PLAYBACK_MOTOR_COUNT) {
    return ESP_ERR_INVALID_ARG;
  }
  if (s_initialized) {
    return ESP_OK;
  }

  memset(s_channels, 0, sizeof(s_channels));
  s_motor_count = motor_count;
  for (uint8_t i = 0; i < motor_count; ++i) {
    pulse_accounting_channel_t *channel = &s_channels[i];
    channel->step_pin = step_pins[i];
    channel->dir_pin = dir_pins[i];

    pcnt_unit_config_t unit_config = {
      .low_limit = PULSE_ACCOUNTING_LOW_LIMIT,
      .high_limit = PULSE_ACCOUNTING_HIGH_LIMIT,
      .flags.accum_count = true,
    };
    ESP_RETURN_ON_ERROR(pcnt_new_unit(&unit_config, &channel->unit), "pulse_accounting", "new unit failed");
    ESP_RETURN_ON_ERROR(pcnt_unit_set_glitch_filter(channel->unit, NULL), "pulse_accounting", "filter failed");

    pcnt_chan_config_t channel_config = {
      .edge_gpio_num = channel->step_pin,
      .level_gpio_num = channel->dir_pin,
    };
    ESP_RETURN_ON_ERROR(
      pcnt_new_channel(channel->unit, &channel_config, &channel->channel),
      "pulse_accounting",
      "new channel failed"
    );
    ESP_RETURN_ON_ERROR(
      pcnt_channel_set_edge_action(
        channel->channel,
        PCNT_CHANNEL_EDGE_ACTION_INCREASE,
        PCNT_CHANNEL_EDGE_ACTION_HOLD
      ),
      "pulse_accounting",
      "edge action failed"
    );
    ESP_RETURN_ON_ERROR(
      pcnt_channel_set_level_action(
        channel->channel,
        PCNT_CHANNEL_LEVEL_ACTION_KEEP,
        PCNT_CHANNEL_LEVEL_ACTION_INVERSE
      ),
      "pulse_accounting",
      "level action failed"
    );
    ESP_RETURN_ON_ERROR(pcnt_unit_enable(channel->unit), "pulse_accounting", "enable failed");
    channel->configured = true;
  }

  s_initialized = true;
  pulse_accounting_reset();
  return ESP_OK;
}

void pulse_accounting_begin_session(void) {
  if (!s_initialized) {
    return;
  }
  pulse_accounting_reset();
  for (uint8_t i = 0; i < s_motor_count; ++i) {
    pulse_accounting_channel_t *channel = &s_channels[i];
    if (!channel->configured || channel->unit == NULL) {
      continue;
    }
    if (pcnt_unit_clear_count(channel->unit) != ESP_OK) {
      channel->unreliable = true;
    }
    if (pcnt_unit_start(channel->unit) != ESP_OK) {
      channel->unreliable = true;
    }
    channel->last_raw_count = 0;
  }
  s_session_active = true;
  s_session_has_data = true;
}

void pulse_accounting_end_session(void) {
  if (!s_initialized) {
    return;
  }
  pulse_accounting_sample_locked();
  for (uint8_t i = 0; i < s_motor_count; ++i) {
    pulse_accounting_channel_t *channel = &s_channels[i];
    if (!channel->configured || channel->unit == NULL) {
      continue;
    }
    (void)pcnt_unit_stop(channel->unit);
  }
  s_session_active = false;
}

void pulse_accounting_reset(void) {
  for (uint8_t i = 0; i < PLAYBACK_MOTOR_COUNT; ++i) {
    s_channels[i].last_raw_count = 0;
    s_channels[i].measured_count = 0u;
    s_channels[i].measured_position_count = 0;
    s_channels[i].inferred_count = 0u;
    s_channels[i].inferred_position_count = 0;
    s_channels[i].unreliable = false;
  }
  s_session_active = false;
  s_session_has_data = false;
}

void pulse_accounting_record_inferred_steps(uint8_t motor_idx, uint32_t emitted_steps, uint8_t direction) {
  if (motor_idx >= s_motor_count || emitted_steps == 0u) {
    return;
  }
  pulse_accounting_channel_t *channel = &s_channels[motor_idx];
  channel->inferred_count = saturating_add_u32(channel->inferred_count, emitted_steps);
  if (direction == 0u) {
    channel->inferred_position_count += (int64_t)emitted_steps;
  } else {
    channel->inferred_position_count -= (int64_t)emitted_steps;
  }
}

void pulse_accounting_sample(void) {
  pulse_accounting_sample_locked();
}

void pulse_accounting_get_measured_counts(uint64_t *counts, uint8_t motor_count) {
  if (counts == NULL) {
    return;
  }
  pulse_accounting_sample_locked();
  for (uint8_t i = 0; i < motor_count; ++i) {
    counts[i] = (i < s_motor_count) ? (uint64_t)s_channels[i].measured_count : 0u;
  }
}

void pulse_accounting_get_measured_positions(int64_t *positions, uint8_t motor_count) {
  if (positions == NULL) {
    return;
  }
  pulse_accounting_sample_locked();
  for (uint8_t i = 0; i < motor_count; ++i) {
    positions[i] = (i < s_motor_count) ? s_channels[i].measured_position_count : 0;
  }
}

bool pulse_accounting_has_session_data(void) {
  return s_session_has_data;
}

void pulse_accounting_get_stats(pulse_accounting_stats_t *stats) {
  if (stats == NULL) {
    return;
  }
  pulse_accounting_sample_locked();
  memset(stats, 0, sizeof(*stats));
  for (uint8_t i = 0; i < s_motor_count; ++i) {
    stats->inferred_total += s_channels[i].inferred_count;
    stats->measured_total += s_channels[i].measured_count;
    const uint32_t position_drift = abs_u32_from_int64(
      s_channels[i].inferred_position_count - s_channels[i].measured_position_count
    );
    stats->position_drift_total = saturating_add_u32(stats->position_drift_total, position_drift);
    if (s_channels[i].unreliable) {
      stats->unreliable_mask |= (1u << i);
    }
    if (s_channels[i].measured_count > 0u ||
        s_channels[i].inferred_count > 0u ||
        s_channels[i].measured_position_count != 0 ||
        s_channels[i].inferred_position_count != 0) {
      stats->active_mask |= (1u << i);
      stats->sample_count++;
    }
  }
  stats->measured_drift_total = (stats->inferred_total > stats->measured_total)
    ? (stats->inferred_total - stats->measured_total)
    : (stats->measured_total - stats->inferred_total);
}

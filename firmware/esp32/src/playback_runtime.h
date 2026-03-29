#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"

typedef enum {
  ERR_OK = 0,
  ERR_BAD_VERSION = 1,
  ERR_UNKNOWN_CMD = 2,
  ERR_BAD_PAYLOAD = 3,
  ERR_BAD_STATE = 4,
  ERR_NO_CREDITS = 5,
  ERR_INTERNAL = 6,
} runtime_err_t;

typedef struct {
  uint8_t stream_open;
  uint8_t stream_end_received;
  uint8_t playing;
  uint32_t playhead_us;
  uint8_t active_motors;
  uint64_t scheduled_start_device_us;
} runtime_state_t;

typedef struct {
  runtime_state_t (*state_get_snapshot)(void);
  void (*state_set_playing)(bool playing);
  void (*state_set_scheduled_start)(uint64_t scheduled_start_device_us);
  void (*state_set_stream)(bool open, bool end_received);
  void (*state_set_playhead_and_active)(uint32_t playhead_us, uint8_t active_motors);
  void (*metrics_note_underrun)(void);
  void (*metrics_note_timer_empty_event)(void);
  void (*metrics_note_timer_restart)(void);
  void (*metrics_note_event_group_started)(void);
  void (*metrics_note_scheduler_guard_hit)(void);
  void (*metrics_note_late_us)(uint32_t late_us);
} playback_runtime_callbacks_t;

esp_err_t playback_runtime_init(const playback_runtime_callbacks_t *callbacks, bool use_isr_dispatch);
runtime_err_t playback_runtime_start(uint64_t scheduled_start_device_us);
void playback_runtime_stop(void);
void playback_runtime_reset_scheduler_state(void);
void playback_runtime_wake(void);
void playback_runtime_task(void *arg);

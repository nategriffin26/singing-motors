#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "motion_backend.h"
#include "stream_queue.h"

typedef enum {
  MOTOR_EVENT_STOP_NONE = 0,
  MOTOR_EVENT_STOP_DECELERATE = 1,
  MOTOR_EVENT_STOP_IMMEDIATE = 2,
} motor_event_stop_mode_t;

typedef struct {
  uint8_t motor_idx;
  uint16_t target_dhz;
  uint32_t ramp_us;
  bool exact_ramp;
  bool flip_before_restart;
  motor_event_stop_mode_t stop_mode;
} motor_event_t;

typedef struct {
  uint32_t delta_us;
  motion_backend_kind_t backend;
  uint8_t event_count;
  motor_event_t events[MOTOR_COUNT];
} motor_event_batch_t;

void motor_event_executor_clear(motor_event_batch_t *batch, motion_backend_kind_t backend);
bool motor_event_executor_from_stream_event_group(
  const stream_event_group_t *event_group,
  motor_event_batch_t *batch
);
bool motor_event_executor_set_exact_target(
  motor_event_batch_t *batch,
  uint8_t motor_idx,
  uint16_t target_dhz,
  uint32_t ramp_us,
  bool exact_ramp
);
void motor_event_executor_apply(const motor_event_batch_t *batch);

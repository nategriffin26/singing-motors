#include "motor_event_executor.h"

#include <string.h>

#include "playback_wave_engine.h"
#include "pulse_engine.h"

void motor_event_executor_clear(motor_event_batch_t *batch, motion_backend_kind_t backend) {
  if (batch == NULL) {
    return;
  }
  memset(batch, 0, sizeof(*batch));
  batch->backend = backend;
}

bool motor_event_executor_from_stream_event_group(
  const stream_event_group_t *event_group,
  motor_event_batch_t *batch
) {
  if (event_group == NULL || batch == NULL) {
    return false;
  }
  motor_event_executor_clear(batch, MOTION_BACKEND_KIND_PLAYBACK_WAVE);
  batch->delta_us = event_group->delta_us;
  batch->event_count = event_group->change_count;
  for (uint8_t i = 0; i < event_group->change_count; ++i) {
    batch->events[i].motor_idx = event_group->changes[i].motor_idx;
    batch->events[i].target_dhz = event_group->changes[i].target_dhz;
    batch->events[i].flip_before_restart =
      (event_group->changes[i].flags & STREAM_EVENT_GROUP_FLAG_FLIP_BEFORE_RESTART) != 0u;
    batch->events[i].stop_mode =
      (event_group->changes[i].target_dhz == 0u) ? MOTOR_EVENT_STOP_DECELERATE : MOTOR_EVENT_STOP_NONE;
  }
  return true;
}

bool motor_event_executor_set_exact_target(
  motor_event_batch_t *batch,
  uint8_t motor_idx,
  uint16_t target_dhz,
  uint32_t ramp_us,
  bool exact_ramp
) {
  if (batch == NULL || batch->event_count >= MOTOR_COUNT || motor_idx >= MOTOR_COUNT) {
    return false;
  }
  motor_event_t *event = &batch->events[batch->event_count++];
  event->motor_idx = motor_idx;
  event->target_dhz = target_dhz;
  event->ramp_us = ramp_us;
  event->exact_ramp = exact_ramp;
  event->flip_before_restart = false;
  event->stop_mode = (target_dhz == 0u) ? MOTOR_EVENT_STOP_DECELERATE : MOTOR_EVENT_STOP_NONE;
  return true;
}

static void apply_exact_batch(const motor_event_batch_t *batch) {
  for (uint8_t i = 0; i < batch->event_count; ++i) {
    const motor_event_t *event = &batch->events[i];
    if (event->motor_idx >= MOTOR_COUNT) {
      continue;
    }
    if (event->exact_ramp) {
      pulse_engine_set_one_target_exact(event->motor_idx, event->target_dhz, event->ramp_us);
    } else {
      pulse_engine_set_one_target(event->motor_idx, event->target_dhz, event->ramp_us);
    }
  }
}

void motor_event_executor_apply(const motor_event_batch_t *batch) {
  if (batch == NULL || batch->event_count == 0u) {
    return;
  }
  if (batch->backend == MOTION_BACKEND_KIND_PLAYBACK_WAVE) {
    playback_wave_engine_apply_events(batch);
    return;
  }
  apply_exact_batch(batch);
}

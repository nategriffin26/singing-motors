#pragma once

#include <stdint.h>

#include "playback_runtime.h"
#include "protocol_defs.h"

typedef runtime_state_t (*motion_state_snapshot_fn)(void);

runtime_err_t motion_commands_home(
  const proto_frame_t *frame,
  uint8_t configured_motors,
  motion_state_snapshot_fn state_get_snapshot
);
runtime_err_t motion_commands_warmup(const proto_frame_t *frame, motion_state_snapshot_fn state_get_snapshot);
runtime_err_t motion_commands_step_motion(const proto_frame_t *frame, motion_state_snapshot_fn state_get_snapshot);
const char *motion_commands_error_detail(uint8_t failed_cmd, runtime_err_t code);

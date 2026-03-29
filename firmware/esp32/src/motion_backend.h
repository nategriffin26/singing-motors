#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "protocol_defs.h"

typedef enum {
  MOTION_BACKEND_KIND_PLAYBACK_WAVE = 0,
  MOTION_BACKEND_KIND_PULSE_EXACT = 1,
} motion_backend_kind_t;

typedef struct {
  motion_backend_kind_t kind;
  const char *backend_id;
  uint8_t motor_count;
  bool supports_continuous_playback;
  bool supports_exact_steps;
  bool supports_direction_flips;
} motion_backend_capabilities_t;

#define MOTION_BACKEND_PLAYBACK_ID "playback-wave"
#define MOTION_BACKEND_EXACT_ID "pulse-exact"

static inline motion_backend_capabilities_t motion_backend_playback_capabilities(void) {
  return (motion_backend_capabilities_t){
    .kind = MOTION_BACKEND_KIND_PLAYBACK_WAVE,
    .backend_id = MOTION_BACKEND_PLAYBACK_ID,
    .motor_count = PLAYBACK_MOTOR_COUNT,
    .supports_continuous_playback = true,
    .supports_exact_steps = false,
    .supports_direction_flips = true,
  };
}

static inline motion_backend_capabilities_t motion_backend_exact_capabilities(void) {
  return (motion_backend_capabilities_t){
    .kind = MOTION_BACKEND_KIND_PULSE_EXACT,
    .backend_id = MOTION_BACKEND_EXACT_ID,
    .motor_count = MOTOR_COUNT,
    .supports_continuous_playback = false,
    .supports_exact_steps = true,
    .supports_direction_flips = true,
  };
}

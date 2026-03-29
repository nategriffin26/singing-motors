#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "freertos/FreeRTOS.h"

#include "protocol_defs.h"

#define STREAM_EVENT_GROUP_QUEUE_CAPACITY (128u)

#define STREAM_EVENT_GROUP_FLAG_FLIP_BEFORE_RESTART (0x01u)

typedef struct {
  uint8_t motor_idx;
  uint8_t flags;
  uint16_t target_dhz;
} stream_motor_change_t;

typedef struct {
  uint32_t delta_us;
  uint8_t change_count;
  stream_motor_change_t changes[MOTOR_COUNT];
} stream_event_group_t;

bool stream_queue_init(void);
bool stream_queue_push(const stream_event_group_t *event_group, TickType_t timeout_ticks);
bool stream_queue_pop(stream_event_group_t *event_group, TickType_t timeout_ticks);
void stream_queue_reset(void);
uint16_t stream_queue_depth(void);
uint16_t stream_queue_capacity(void);
uint16_t stream_queue_credits(void);
uint16_t stream_queue_high_water(void);

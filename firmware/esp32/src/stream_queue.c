#include "stream_queue.h"

#include "freertos/queue.h"

static QueueHandle_t s_segment_queue = NULL;
static uint16_t s_high_water = 0u;

bool stream_queue_init(void) {
  if (s_segment_queue != NULL) {
    return true;
  }

  s_segment_queue = xQueueCreate(STREAM_EVENT_GROUP_QUEUE_CAPACITY, sizeof(stream_event_group_t));
  s_high_water = 0u;
  return s_segment_queue != NULL;
}

bool stream_queue_push(const stream_event_group_t *event_group, TickType_t timeout_ticks) {
  if ((s_segment_queue == NULL) || (event_group == NULL)) {
    return false;
  }

  if (xQueueSend(s_segment_queue, event_group, timeout_ticks) != pdTRUE) {
    return false;
  }

  const uint16_t depth = (uint16_t)uxQueueMessagesWaiting(s_segment_queue);
  if (depth > s_high_water) {
    s_high_water = depth;
  }
  return true;
}

bool stream_queue_pop(stream_event_group_t *event_group, TickType_t timeout_ticks) {
  if ((s_segment_queue == NULL) || (event_group == NULL)) {
    return false;
  }

  return xQueueReceive(s_segment_queue, event_group, timeout_ticks) == pdTRUE;
}

void stream_queue_reset(void) {
  if (s_segment_queue == NULL) {
    return;
  }
  xQueueReset(s_segment_queue);
  s_high_water = 0u;
}

uint16_t stream_queue_depth(void) {
  if (s_segment_queue == NULL) {
    return 0u;
  }
  return (uint16_t)uxQueueMessagesWaiting(s_segment_queue);
}

uint16_t stream_queue_capacity(void) {
  return (uint16_t)STREAM_EVENT_GROUP_QUEUE_CAPACITY;
}

uint16_t stream_queue_credits(void) {
  const uint16_t depth = stream_queue_depth();
  const uint16_t cap = stream_queue_capacity();
  return (depth >= cap) ? 0u : (uint16_t)(cap - depth);
}

uint16_t stream_queue_high_water(void) {
  return s_high_water;
}

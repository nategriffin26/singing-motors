#include "playback_runtime.h"

#include <stdbool.h>
#include <stdint.h>
#include <string.h>

#include "esp_check.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "motor_event_executor.h"
#include "playback_wave_engine.h"
#include "pulse_engine.h"
#include "stream_queue.h"

#define PLAYBACK_RUNTIME_NOTIFY_CONTROL (1u << 0)
#define PLAYBACK_RUNTIME_NOTIFY_BOUNDARY (1u << 1)
#define PLAYBACK_RUNTIME_NOTIFY_QUEUE (1u << 2)

static playback_runtime_callbacks_t s_callbacks = {0};
static TaskHandle_t s_runtime_task = NULL;
static esp_timer_handle_t s_boundary_timer = NULL;
static esp_timer_handle_t s_control_timer = NULL;
static bool s_use_isr_dispatch = false;
static bool s_boundary_timer_running = false;
static bool s_underrun_latched = false;
static uint32_t s_generation = 0u;
static int64_t s_expected_boundary_us = 0;
static int64_t s_next_boundary_due_us = 0;
static int64_t s_scheduled_start_us = 0;
static stream_event_group_t s_pending_event_group = {0};
static bool s_pending_event_group_loaded = false;
static portMUX_TYPE s_runtime_lock = portMUX_INITIALIZER_UNLOCKED;

static TickType_t delay_ticks_from_ms(uint32_t ms) {
  if (ms == 0u) {
    return 0;
  }
  const TickType_t ticks = pdMS_TO_TICKS(ms);
  return (ticks == 0) ? 1 : ticks;
}

static void playback_set_boundary_timer_running(bool running) {
  portENTER_CRITICAL(&s_runtime_lock);
  s_boundary_timer_running = running;
  portEXIT_CRITICAL(&s_runtime_lock);
}

static uint32_t playback_generation_snapshot(void) {
  uint32_t generation = 0u;
  portENTER_CRITICAL(&s_runtime_lock);
  generation = s_generation;
  portEXIT_CRITICAL(&s_runtime_lock);
  return generation;
}

static void playback_notify_runtime(uint32_t bits) {
  if (s_runtime_task == NULL || bits == 0u) {
    return;
  }
  xTaskNotify(s_runtime_task, bits, eSetBits);
}

static void playback_notify_runtime_from_timer(uint32_t bits) {
  if (s_runtime_task == NULL || bits == 0u) {
    return;
  }
#if CONFIG_ESP_TIMER_SUPPORTS_ISR_DISPATCH_METHOD
  if (s_use_isr_dispatch) {
    BaseType_t higher_priority_woken = pdFALSE;
    xTaskNotifyFromISR(s_runtime_task, bits, eSetBits, &higher_priority_woken);
    if (higher_priority_woken == pdTRUE) {
      portYIELD_FROM_ISR();
    }
    return;
  }
#endif
  xTaskNotify(s_runtime_task, bits, eSetBits);
}

void playback_runtime_wake(void) {
  playback_notify_runtime(PLAYBACK_RUNTIME_NOTIFY_QUEUE);
}

static void playback_runtime_reset_scheduler_state_locked(bool reset_engine) {
  if (s_boundary_timer != NULL) {
    (void)esp_timer_stop(s_boundary_timer);
  }
  if (s_control_timer != NULL) {
    (void)esp_timer_stop(s_control_timer);
  }
  s_expected_boundary_us = 0;
  s_next_boundary_due_us = 0;
  s_scheduled_start_us = 0;
  s_underrun_latched = false;
  memset(&s_pending_event_group, 0, sizeof(s_pending_event_group));
  s_pending_event_group_loaded = false;
  if (reset_engine) {
    playback_wave_engine_reset();
  }
  portENTER_CRITICAL(&s_runtime_lock);
  s_boundary_timer_running = false;
  s_generation++;
  portEXIT_CRITICAL(&s_runtime_lock);
}

void playback_runtime_reset_scheduler_state(void) {
  playback_runtime_reset_scheduler_state_locked(true);
}

static bool playback_load_pending_event_group(void) {
  if (s_pending_event_group_loaded) {
    return true;
  }
  if (!stream_queue_pop(&s_pending_event_group, 0u)) {
    return false;
  }
  s_pending_event_group_loaded = true;
  return true;
}

static void playback_complete_if_finished(const runtime_state_t *snap) {
  if (snap == NULL) {
    return;
  }
  if (snap->stream_open && snap->stream_end_received && stream_queue_depth() == 0u && !s_pending_event_group_loaded) {
    playback_wave_engine_note_stop_reason(1u);
    playback_wave_engine_stop_all();
    s_callbacks.state_set_playing(false);
    s_callbacks.state_set_stream(false, false);
    s_callbacks.state_set_playhead_and_active(snap->playhead_us, 0u);
    s_callbacks.state_set_scheduled_start(0u);
    s_expected_boundary_us = 0;
    s_next_boundary_due_us = 0;
    s_scheduled_start_us = 0;
    s_underrun_latched = false;
    playback_set_boundary_timer_running(false);
  }
}

static void playback_note_underrun(void) {
  s_callbacks.metrics_note_timer_empty_event();
  playback_wave_engine_note_stop_reason(2u);
  playback_wave_engine_stop_all();
  if (!s_underrun_latched) {
    s_callbacks.metrics_note_underrun();
    s_underrun_latched = true;
  }
  s_expected_boundary_us = 0;
  s_next_boundary_due_us = 0;
  s_scheduled_start_us = 0;
  playback_set_boundary_timer_running(false);
}

static bool playback_arm_boundary_timer(int64_t now_us) {
  if (!s_pending_event_group_loaded || s_boundary_timer == NULL) {
    return false;
  }
  if (s_expected_boundary_us == 0) {
    s_expected_boundary_us = now_us;
  }
  s_next_boundary_due_us = s_expected_boundary_us + (int64_t)s_pending_event_group.delta_us;
  if (s_boundary_timer_running && s_next_boundary_due_us > now_us) {
    return true;
  }
  const uint64_t wait_us = (s_next_boundary_due_us > now_us) ? (uint64_t)(s_next_boundary_due_us - now_us) : 1u;
  if (esp_timer_start_once(s_boundary_timer, wait_us) != ESP_OK) {
    playback_set_boundary_timer_running(false);
    return false;
  }
  playback_set_boundary_timer_running(true);
  return true;
}

static bool playback_dispatch_due_groups(int64_t now_us, uint32_t generation) {
  if (generation != playback_generation_snapshot()) {
    return false;
  }
  const runtime_state_t snap = s_callbacks.state_get_snapshot();
  if (!snap.playing) {
    return false;
  }
  if (!playback_load_pending_event_group()) {
    if (snap.stream_open && !snap.stream_end_received) {
      playback_note_underrun();
    } else {
      playback_complete_if_finished(&snap);
    }
    return false;
  }

  if (s_expected_boundary_us == 0) {
    s_expected_boundary_us = now_us;
  }

  bool dispatched_group = false;
  bool restart_recorded = false;
  uint32_t playhead_us = snap.playhead_us;
  while (s_pending_event_group_loaded) {
    const int64_t due_us = s_expected_boundary_us + (int64_t)s_pending_event_group.delta_us;
    if (now_us < due_us) {
      if (s_underrun_latched && !restart_recorded) {
        s_callbacks.metrics_note_timer_restart();
        restart_recorded = true;
      }
      s_underrun_latched = false;
      return playback_arm_boundary_timer(now_us);
    }

    if (now_us > due_us) {
      s_callbacks.metrics_note_late_us((uint32_t)(now_us - due_us));
    }

    motor_event_batch_t batch;
    if (motor_event_executor_from_stream_event_group(&s_pending_event_group, &batch)) {
      motor_event_executor_apply(&batch);
    }
    s_expected_boundary_us = due_us;
    s_next_boundary_due_us = due_us;
    s_pending_event_group_loaded = false;
    playhead_us += s_pending_event_group.delta_us;
    s_callbacks.state_set_playhead_and_active(
      playhead_us,
      playback_wave_engine_active_motor_count()
    );
    s_callbacks.metrics_note_event_group_started();
    dispatched_group = true;

    if (!playback_load_pending_event_group()) {
      playback_set_boundary_timer_running(false);
      const runtime_state_t after_dispatch = s_callbacks.state_get_snapshot();
      playback_complete_if_finished(&after_dispatch);
      if (after_dispatch.playing && after_dispatch.stream_open && !after_dispatch.stream_end_received) {
        playback_note_underrun();
      }
      return dispatched_group;
    }
    if (generation != playback_generation_snapshot()) {
      return false;
    }
  }

  return dispatched_group;
}

static void playback_boundary_timer_cb(void *arg) {
  (void)arg;
  playback_set_boundary_timer_running(false);
  playback_notify_runtime_from_timer(PLAYBACK_RUNTIME_NOTIFY_BOUNDARY);
}

static void playback_control_timer_cb(void *arg) {
  (void)arg;
  playback_notify_runtime_from_timer(PLAYBACK_RUNTIME_NOTIFY_CONTROL);
}

esp_err_t playback_runtime_init(const playback_runtime_callbacks_t *callbacks, bool use_isr_dispatch) {
  if (callbacks == NULL ||
      callbacks->state_get_snapshot == NULL ||
      callbacks->state_set_playing == NULL ||
      callbacks->state_set_scheduled_start == NULL ||
      callbacks->state_set_stream == NULL ||
      callbacks->state_set_playhead_and_active == NULL ||
      callbacks->metrics_note_underrun == NULL ||
      callbacks->metrics_note_timer_empty_event == NULL ||
      callbacks->metrics_note_timer_restart == NULL ||
      callbacks->metrics_note_event_group_started == NULL ||
      callbacks->metrics_note_scheduler_guard_hit == NULL ||
      callbacks->metrics_note_late_us == NULL) {
    return ESP_ERR_INVALID_ARG;
  }

  s_callbacks = *callbacks;

#if CONFIG_ESP_TIMER_SUPPORTS_ISR_DISPATCH_METHOD
  s_use_isr_dispatch = use_isr_dispatch;
#else
  s_use_isr_dispatch = false;
  (void)use_isr_dispatch;
#endif

  if (s_boundary_timer != NULL && s_control_timer != NULL) {
    return ESP_OK;
  }

  const esp_timer_dispatch_t dispatch_method =
#if CONFIG_ESP_TIMER_SUPPORTS_ISR_DISPATCH_METHOD
    s_use_isr_dispatch ? ESP_TIMER_ISR : ESP_TIMER_TASK;
#else
    ESP_TIMER_TASK;
#endif

  const esp_timer_create_args_t boundary_timer_args = {
    .callback = &playback_boundary_timer_cb,
    .arg = NULL,
    .name = "playback_boundary",
    .dispatch_method = dispatch_method,
  };
  ESP_RETURN_ON_ERROR(esp_timer_create(&boundary_timer_args, &s_boundary_timer), "playback_runtime", "boundary timer failed");

  const esp_timer_create_args_t control_timer_args = {
    .callback = &playback_control_timer_cb,
    .arg = NULL,
    .name = "playback_control",
    .dispatch_method = dispatch_method,
  };
  return esp_timer_create(&control_timer_args, &s_control_timer);
}

runtime_err_t playback_runtime_start(uint64_t scheduled_start_device_us) {
  const int64_t now_us = esp_timer_get_time();
  if (playback_wave_engine_claim_step_gpio() != ESP_OK) {
    return ERR_INTERNAL;
  }
  playback_runtime_reset_scheduler_state();
  s_scheduled_start_us = (scheduled_start_device_us > 0u)
    ? (int64_t)scheduled_start_device_us
    : now_us;
  if (s_scheduled_start_us < now_us) {
    s_scheduled_start_us = now_us;
  }
  s_callbacks.state_set_scheduled_start((uint64_t)s_scheduled_start_us);
  if (s_control_timer != NULL &&
      esp_timer_start_periodic(s_control_timer, playback_wave_engine_control_interval_us()) != ESP_OK) {
    playback_wave_engine_release_step_gpio();
    return ERR_INTERNAL;
  }
  playback_runtime_wake();
  return ERR_OK;
}

void playback_runtime_stop(void) {
  playback_runtime_reset_scheduler_state();
  playback_wave_engine_note_stop_reason(3u);
  playback_wave_engine_stop_all();
  playback_wave_engine_release_step_gpio();
}

static void playback_service_runtime(void) {
  const runtime_state_t snap = s_callbacks.state_get_snapshot();
  if (!snap.playing) {
    playback_runtime_reset_scheduler_state_locked(false);
    s_callbacks.state_set_playhead_and_active(0u, 0u);
    return;
  }

  const int64_t now_us = esp_timer_get_time();
  playback_wave_engine_tick(now_us);

  if (s_scheduled_start_us > now_us) {
    if (s_boundary_timer != NULL && !s_boundary_timer_running) {
      const uint64_t wait_us = (uint64_t)(s_scheduled_start_us - now_us);
      if (esp_timer_start_once(s_boundary_timer, wait_us > 0u ? wait_us : 1u) == ESP_OK) {
        playback_set_boundary_timer_running(true);
      }
    }
    return;
  }
  if (s_scheduled_start_us > 0) {
    s_expected_boundary_us = s_scheduled_start_us;
    s_next_boundary_due_us = s_scheduled_start_us;
    s_scheduled_start_us = 0;
    s_callbacks.state_set_scheduled_start(0u);
  }

  if (s_underrun_latched && s_expected_boundary_us == 0) {
    s_expected_boundary_us = now_us;
  }

  const uint32_t generation = playback_generation_snapshot();
  if (!playback_dispatch_due_groups(now_us, generation)) {
    playback_complete_if_finished(&snap);
  }
}

void playback_runtime_task(void *arg) {
  (void)arg;
  s_runtime_task = xTaskGetCurrentTaskHandle();

  while (true) {
    uint32_t notify_bits = 0u;
    (void)xTaskNotifyWait(0u, UINT32_MAX, &notify_bits, delay_ticks_from_ms(100u));
    if (notify_bits == 0u) {
      notify_bits = PLAYBACK_RUNTIME_NOTIFY_CONTROL;
    }
    playback_service_runtime();
  }
}

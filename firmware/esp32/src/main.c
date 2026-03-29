#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include "driver/uart.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "motion_backend.h"
#include "motion_commands.h"
#include "playback_runtime.h"
#include "playback_wave_engine.h"
#include "protocol_codec.h"
#include "protocol_defs.h"
#include "pulse_engine.h"
#include "stream_parser.h"
#include "stream_queue.h"

#define UART_RX_BUFFER_SIZE (4096)
#define UART_READ_CHUNK_SIZE (128)

#define PLAYBACK_TASK_STACK (6144)
#define RX_TASK_STACK (12288)
#define PLAYBACK_TASK_PRIO (8)
#define RX_TASK_PRIO (9)

#ifndef MUSIC2_SAFE_MAX_FREQ_DHZ
#define MUSIC2_SAFE_MAX_FREQ_DHZ (8000u)
#endif
#define PLAYBACK_SAFE_MAX_FREQ_DHZ (MUSIC2_SAFE_MAX_FREQ_DHZ)

#ifndef MUSIC2_PLAYBACK_TIMER_ISR_DISPATCH
#define MUSIC2_PLAYBACK_TIMER_ISR_DISPATCH (1u)
#endif

typedef struct {
  uint32_t underrun_count;
  uint16_t queue_high_water;
  uint32_t scheduling_late_max_us;
  uint32_t crc_parse_errors;
  uint32_t rx_parse_errors;
  uint32_t timer_empty_events;
  uint32_t timer_restart_count;
  uint32_t event_groups_started;
  uint32_t scheduler_guard_hits;
} runtime_metrics_t;

typedef struct {
  uint8_t motors;
  uint8_t idle_mode;
  uint8_t min_note;
  uint8_t max_note;
  int8_t transpose;
} runtime_setup_t;

static const char *TAG = "music2-fw";

static runtime_metrics_t g_metrics = {0};
static runtime_state_t g_state = {0};
static runtime_setup_t g_setup = {0};
static portMUX_TYPE g_state_lock = portMUX_INITIALIZER_UNLOCKED;
static uint32_t g_playback_run_accel_dhz_per_s = 80000u;
static uint16_t g_playback_launch_start_dhz = 600u;
static uint32_t g_playback_launch_accel_dhz_per_s = 50000u;
static uint16_t g_playback_launch_crossover_dhz = 1800u;

static runtime_err_t command_play_at(uint64_t scheduled_start_device_us);

static void metrics_reset(void) {
  portENTER_CRITICAL(&g_state_lock);
  memset(&g_metrics, 0, sizeof(g_metrics));
  portEXIT_CRITICAL(&g_state_lock);
  pulse_engine_reset_diag_counters();
  playback_wave_engine_reset_diag_counters();
}

static void metrics_note_crc_parse_error(void) {
  portENTER_CRITICAL(&g_state_lock);
  g_metrics.crc_parse_errors++;
  portEXIT_CRITICAL(&g_state_lock);
}

static void metrics_note_rx_parse_error(void) {
  portENTER_CRITICAL(&g_state_lock);
  g_metrics.rx_parse_errors++;
  portEXIT_CRITICAL(&g_state_lock);
}

static void metrics_note_underrun(void) {
  portENTER_CRITICAL(&g_state_lock);
  g_metrics.underrun_count++;
  portEXIT_CRITICAL(&g_state_lock);
}

static void metrics_note_timer_empty_event(void) {
  portENTER_CRITICAL(&g_state_lock);
  g_metrics.timer_empty_events++;
  portEXIT_CRITICAL(&g_state_lock);
}

static void metrics_note_timer_restart(void) {
  portENTER_CRITICAL(&g_state_lock);
  g_metrics.timer_restart_count++;
  portEXIT_CRITICAL(&g_state_lock);
}

static void metrics_note_event_group_started(void) {
  portENTER_CRITICAL(&g_state_lock);
  g_metrics.event_groups_started++;
  portEXIT_CRITICAL(&g_state_lock);
}

static void metrics_note_scheduler_guard_hit(void) {
  portENTER_CRITICAL(&g_state_lock);
  g_metrics.scheduler_guard_hits++;
  portEXIT_CRITICAL(&g_state_lock);
}

static void metrics_note_late_us(uint32_t late_us) {
  portENTER_CRITICAL(&g_state_lock);
  if (late_us > g_metrics.scheduling_late_max_us) {
    g_metrics.scheduling_late_max_us = late_us;
  }
  portEXIT_CRITICAL(&g_state_lock);
}

static void metrics_update_queue_high_water(void) {
  const uint16_t queue_hwm = stream_queue_high_water();
  portENTER_CRITICAL(&g_state_lock);
  if (queue_hwm > g_metrics.queue_high_water) {
    g_metrics.queue_high_water = queue_hwm;
  }
  portEXIT_CRITICAL(&g_state_lock);
}

static void state_set_stream(bool open, bool end_received) {
  portENTER_CRITICAL(&g_state_lock);
  g_state.stream_open = open ? 1u : 0u;
  g_state.stream_end_received = end_received ? 1u : 0u;
  portEXIT_CRITICAL(&g_state_lock);
}

static void state_set_playing(bool playing) {
  portENTER_CRITICAL(&g_state_lock);
  g_state.playing = playing ? 1u : 0u;
  portEXIT_CRITICAL(&g_state_lock);
}

static void state_set_scheduled_start(uint64_t scheduled_start_device_us) {
  portENTER_CRITICAL(&g_state_lock);
  g_state.scheduled_start_device_us = scheduled_start_device_us;
  portEXIT_CRITICAL(&g_state_lock);
}

static void state_set_playhead_and_active(uint32_t playhead_us, uint8_t active_motors) {
  portENTER_CRITICAL(&g_state_lock);
  g_state.playhead_us = playhead_us;
  g_state.active_motors = active_motors;
  portEXIT_CRITICAL(&g_state_lock);
}

static runtime_state_t state_get_snapshot(void) {
  runtime_state_t snap;
  portENTER_CRITICAL(&g_state_lock);
  snap = g_state;
  portEXIT_CRITICAL(&g_state_lock);
  return snap;
}

static bool send_frame(uint8_t cmd, uint16_t seq, uint8_t flags, const uint8_t *payload, uint16_t payload_len) {
  if (payload_len > PROTO_MAX_PAYLOAD) {
    return false;
  }

  proto_frame_t frame = {
    .version = PROTO_VERSION,
    .cmd = cmd,
    .seq = seq,
    .flags = flags,
    .payload_len = payload_len,
  };
  if ((payload_len > 0u) && (payload != NULL)) {
    memcpy(frame.payload, payload, payload_len);
  }

  uint8_t encoded[PROTO_MAX_ENCODED_FRAME + 1u];
  size_t encoded_len = 0u;
  if (!proto_frame_encode(&frame, encoded, sizeof(encoded), &encoded_len)) {
    return false;
  }
  return uart_write_bytes(UART_NUM_0, (const char *)encoded, encoded_len) == (int)encoded_len;
}

static bool send_ack_basic(uint16_t seq, uint8_t acked_cmd) {
  uint8_t payload[6] = {0};
  payload[0] = acked_cmd;
  payload[1] = 0u;
  proto_write_le16(&payload[2], stream_queue_credits());
  proto_write_le16(&payload[4], stream_queue_depth());
  return send_frame(PROTO_CMD_ACK, seq, 0u, payload, sizeof(payload));
}

static bool send_ack_play_at(uint16_t seq, uint64_t accepted_start_device_us) {
  uint8_t payload[14] = {0};
  payload[0] = PROTO_CMD_PLAY_AT;
  payload[1] = 0u;
  proto_write_le16(&payload[2], stream_queue_credits());
  proto_write_le16(&payload[4], stream_queue_depth());
  proto_write_le32(&payload[6], (uint32_t)(accepted_start_device_us & 0xFFFFFFFFu));
  proto_write_le32(&payload[10], (uint32_t)(accepted_start_device_us >> 32));
  return send_frame(PROTO_CMD_ACK, seq, 0u, payload, sizeof(payload));
}

static bool send_ack_hello(uint16_t seq) {
  const motion_backend_capabilities_t playback_caps = playback_wave_engine_capabilities();
  uint8_t payload[19] = {0};
  payload[0] = PROTO_CMD_HELLO;
  payload[1] = 0u;
  proto_write_le16(&payload[2], stream_queue_credits());
  proto_write_le16(&payload[4], stream_queue_depth());
  payload[6] = PROTO_VERSION;
  payload[7] = MOTOR_COUNT;
  payload[8] = (uint8_t)(
    FEATURE_FLAG_PLAYBACK_EVENT_STREAMING |
    FEATURE_FLAG_HOME |
    FEATURE_FLAG_WARMUP |
    FEATURE_FLAG_STEP_MOTION |
    FEATURE_FLAG_DIRECTION_FLIP |
    FEATURE_FLAG_CONTINUOUS_PLAYBACK_ENGINE |
    FEATURE_FLAG_PLAYBACK_SETUP_PROFILE |
    FEATURE_FLAG_SPEECH_ASSIST
  );
  proto_write_le16(&payload[9], stream_queue_capacity());
  proto_write_le16(&payload[11], PULSE_ENGINE_TICK_US);
  proto_write_le32(&payload[13], g_playback_run_accel_dhz_per_s);
  payload[17] = playback_caps.motor_count;
  payload[18] = EXACT_MOTION_FEATURE_DIRECTIONAL_STEP_MOTION;
  return send_frame(PROTO_CMD_ACK, seq, 0u, payload, sizeof(payload));
}

static bool send_err(uint16_t seq, uint8_t failed_cmd, runtime_err_t code) {
  uint8_t payload[96] = {0};
  uint16_t payload_len = 6u;
  payload[0] = failed_cmd;
  payload[1] = (uint8_t)code;
  proto_write_le16(&payload[2], stream_queue_credits());
  proto_write_le16(&payload[4], stream_queue_depth());
  const char *detail = motion_commands_error_detail(failed_cmd, code);
  if (detail != NULL) {
    const size_t detail_len = strnlen(detail, sizeof(payload) - (size_t)payload_len);
    if (detail_len > 0u) {
      memcpy(&payload[payload_len], detail, detail_len);
      payload_len = (uint16_t)(payload_len + (uint16_t)detail_len);
    }
  }
  return send_frame(PROTO_CMD_ERR, seq, 0u, payload, payload_len);
}

static bool send_status(uint16_t seq) {
  const runtime_state_t snap = state_get_snapshot();
  uint8_t payload[32] = {0};
  const uint64_t device_time_us = (uint64_t)esp_timer_get_time();
  payload[0] = PROTO_VERSION;
  payload[1] = 0u;
  payload[1] |= snap.playing ? 0x01u : 0u;
  payload[1] |= snap.stream_open ? 0x02u : 0u;
  payload[1] |= snap.stream_end_received ? 0x04u : 0u;
  payload[2] = MOTOR_COUNT;
  payload[3] = 0u;
  proto_write_le16(&payload[4], stream_queue_depth());
  proto_write_le16(&payload[6], stream_queue_capacity());
  proto_write_le16(&payload[8], stream_queue_credits());
  payload[10] = snap.active_motors;
  payload[11] = 0u;
  proto_write_le32(&payload[12], snap.playhead_us);
  proto_write_le32(&payload[16], (uint32_t)(device_time_us & 0xFFFFFFFFu));
  proto_write_le32(&payload[20], (uint32_t)(device_time_us >> 32));
  proto_write_le32(&payload[24], (uint32_t)(snap.scheduled_start_device_us & 0xFFFFFFFFu));
  proto_write_le32(&payload[28], (uint32_t)(snap.scheduled_start_device_us >> 32));
  return send_frame(PROTO_CMD_STATUS, seq, 0u, payload, sizeof(payload));
}

static bool send_metrics(uint16_t seq) {
  runtime_metrics_t metrics_snapshot;
  playback_wave_diag_counters_t playback_diag = {0};
  pulse_engine_diag_counters_t exact_diag = {0};

  metrics_update_queue_high_water();
  playback_wave_engine_get_diag_counters(&playback_diag);
  pulse_engine_get_diag_counters(&exact_diag);

  portENTER_CRITICAL(&g_state_lock);
  metrics_snapshot = g_metrics;
  portEXIT_CRITICAL(&g_state_lock);

  uint8_t payload[136] = {0};
  proto_write_le32(&payload[0], metrics_snapshot.underrun_count);
  proto_write_le16(&payload[4], metrics_snapshot.queue_high_water);
  proto_write_le16(&payload[6], 0u);
  proto_write_le32(&payload[8], metrics_snapshot.scheduling_late_max_us);
  proto_write_le32(&payload[12], metrics_snapshot.crc_parse_errors);
  proto_write_le32(&payload[16], metrics_snapshot.rx_parse_errors);
  proto_write_le16(&payload[20], stream_queue_depth());
  proto_write_le16(&payload[22], stream_queue_credits());
  proto_write_le32(&payload[24], metrics_snapshot.timer_empty_events);
  proto_write_le32(&payload[28], metrics_snapshot.timer_restart_count);
  proto_write_le32(&payload[32], metrics_snapshot.event_groups_started);
  proto_write_le32(&payload[36], metrics_snapshot.scheduler_guard_hits);
  proto_write_le32(&payload[40], playback_diag.control_late_max_us);
  proto_write_le32(&payload[44], playback_diag.control_overrun_count);
  proto_write_le32(&payload[48], playback_diag.wave_period_update_count);
  proto_write_le32(&payload[52], playback_diag.motor_start_count);
  proto_write_le32(&payload[56], playback_diag.motor_stop_count);
  proto_write_le32(&payload[60], playback_diag.flip_restart_count);
  proto_write_le32(&payload[64], playback_diag.launch_guard_count);
  proto_write_le32(&payload[68], playback_diag.engine_fault_count);
  proto_write_le32(&payload[72], playback_diag.engine_fault_mask);
  proto_write_le32(&payload[76], playback_diag.engine_fault_attach_count);
  proto_write_le32(&payload[80], playback_diag.engine_fault_detach_count);
  proto_write_le32(&payload[84], playback_diag.engine_fault_period_count);
  proto_write_le32(&payload[88], playback_diag.engine_fault_force_count);
  proto_write_le32(&payload[92], playback_diag.engine_fault_timer_count);
  proto_write_le32(&payload[96], playback_diag.engine_fault_invalid_change_count);
  proto_write_le32(&payload[100], playback_diag.engine_fault_last_reason);
  proto_write_le32(&payload[104], playback_diag.engine_fault_last_motor);
  proto_write_le32(&payload[108], playback_diag.inferred_pulse_total);
  proto_write_le32(&payload[112], playback_diag.measured_pulse_total);
  proto_write_le32(&payload[116], playback_diag.measured_pulse_drift_total);
  proto_write_le32(&payload[120], playback_diag.measured_pulse_active_mask);
  proto_write_le32(&payload[124], exact_diag.pulse_position_lost_mask);
  proto_write_le32(&payload[128], playback_diag.playback_position_unreliable_mask);
  proto_write_le32(&payload[132], playback_diag.playback_signed_position_drift_total);
  return send_frame(PROTO_CMD_METRICS, seq, 0u, payload, sizeof(payload));
}

static runtime_err_t command_setup(const proto_frame_t *frame) {
  if ((frame->payload_len != SETUP_BASE_PAYLOAD_SIZE) &&
      (frame->payload_len != SETUP_WITH_PLAYBACK_PROFILE_PAYLOAD_SIZE) &&
      (frame->payload_len != SETUP_WITH_SPEECH_ASSIST_PAYLOAD_SIZE)) {
    return ERR_BAD_PAYLOAD;
  }

  const uint8_t motors = frame->payload[0];
  const uint8_t idle_mode = frame->payload[1];
  const uint8_t min_note = frame->payload[2];
  const uint8_t max_note = frame->payload[3];
  const int8_t transpose = (int8_t)frame->payload[4];
  if ((motors == 0u) || (motors > MOTOR_COUNT)) {
    return ERR_BAD_PAYLOAD;
  }
  if (idle_mode > 1u || min_note > max_note) {
    return ERR_BAD_PAYLOAD;
  }

  uint32_t next_run_accel_dhz_per_s = g_playback_run_accel_dhz_per_s;
  uint16_t next_launch_start_dhz = g_playback_launch_start_dhz;
  uint32_t next_launch_accel_dhz_per_s = g_playback_launch_accel_dhz_per_s;
  uint16_t next_launch_crossover_dhz = g_playback_launch_crossover_dhz;
  bool speech_assist_enabled = false;
  uint16_t speech_control_interval_us = 0u;
  uint32_t speech_release_accel_dhz_per_s = 0u;
  if ((frame->payload_len == SETUP_WITH_PLAYBACK_PROFILE_PAYLOAD_SIZE) ||
      (frame->payload_len == SETUP_WITH_SPEECH_ASSIST_PAYLOAD_SIZE)) {
    next_run_accel_dhz_per_s = proto_read_le32(&frame->payload[5]);
    next_launch_start_dhz = proto_read_le16(&frame->payload[9]);
    next_launch_accel_dhz_per_s = proto_read_le32(&frame->payload[11]);
    next_launch_crossover_dhz = proto_read_le16(&frame->payload[15]);
    if (next_launch_start_dhz == 0u || next_launch_crossover_dhz < next_launch_start_dhz) {
      return ERR_BAD_PAYLOAD;
    }
  }
  if (frame->payload_len == SETUP_WITH_SPEECH_ASSIST_PAYLOAD_SIZE) {
    speech_assist_enabled = true;
    speech_control_interval_us = proto_read_le16(&frame->payload[17]);
    speech_release_accel_dhz_per_s = proto_read_le32(&frame->payload[19]);
    if (speech_control_interval_us == 0u || speech_release_accel_dhz_per_s == 0u) {
      return ERR_BAD_PAYLOAD;
    }
  }

  const runtime_state_t snap = state_get_snapshot();
  if (snap.playing || snap.stream_open) {
    return ERR_BAD_STATE;
  }

  stream_queue_reset();
  metrics_reset();
  playback_runtime_reset_scheduler_state();
  playback_wave_engine_configure_profile(
    next_run_accel_dhz_per_s,
    next_launch_start_dhz,
    next_launch_accel_dhz_per_s,
    next_launch_crossover_dhz
  );
  playback_wave_engine_configure_speech_assist(
    speech_assist_enabled,
    speech_control_interval_us,
    speech_release_accel_dhz_per_s
  );
  g_playback_run_accel_dhz_per_s = next_run_accel_dhz_per_s;
  g_playback_launch_start_dhz = next_launch_start_dhz;
  g_playback_launch_accel_dhz_per_s = next_launch_accel_dhz_per_s;
  g_playback_launch_crossover_dhz = next_launch_crossover_dhz;
  playback_wave_engine_note_stop_reason(4u);
  playback_wave_engine_stop_all();
  pulse_engine_stop_all();
  state_set_playing(false);
  state_set_scheduled_start(0u);
  state_set_stream(false, false);
  state_set_playhead_and_active(0u, 0u);

  portENTER_CRITICAL(&g_state_lock);
  g_setup.motors = motors;
  g_setup.idle_mode = idle_mode;
  g_setup.min_note = min_note;
  g_setup.max_note = max_note;
  g_setup.transpose = transpose;
  portEXIT_CRITICAL(&g_state_lock);
  return ERR_OK;
}

static runtime_err_t command_stream_begin(const proto_frame_t *frame) {
  if (frame->payload_len != 6u) {
    return ERR_BAD_PAYLOAD;
  }
  const runtime_state_t snap = state_get_snapshot();
  if (snap.playing || snap.stream_open) {
    return ERR_BAD_STATE;
  }
  const uint32_t total_segments = proto_read_le32(&frame->payload[0]);
  const uint16_t requested_credits = proto_read_le16(&frame->payload[4]);
  if ((total_segments == 0u) || (requested_credits == 0u)) {
    return ERR_BAD_PAYLOAD;
  }
  if ((g_setup.motors == 0u) || (g_setup.motors > MOTOR_COUNT)) {
    return ERR_BAD_STATE;
  }

  stream_queue_reset();
  metrics_reset();
  playback_runtime_reset_scheduler_state();
  playback_wave_engine_note_stop_reason(5u);
  playback_wave_engine_stop_all();
  pulse_engine_stop_all();
  state_set_playing(false);
  state_set_scheduled_start(0u);
  state_set_playhead_and_active(0u, 0u);
  state_set_stream(true, false);
  return ERR_OK;
}

static runtime_err_t command_stream_append(const proto_frame_t *frame) {
  const runtime_state_t snap = state_get_snapshot();
  if (!snap.stream_open || snap.stream_end_received || frame->payload_len < 1u) {
    return ERR_BAD_STATE;
  }

  const uint8_t event_group_count = frame->payload[0];
  if (event_group_count == 0u) {
    return ERR_BAD_PAYLOAD;
  }
  if ((uint16_t)event_group_count > stream_queue_credits()) {
    return ERR_NO_CREDITS;
  }

  const uint8_t *cursor = &frame->payload[1];
  for (uint8_t group_idx = 0; group_idx < event_group_count; ++group_idx) {
    stream_event_group_t event_group = {0};
    if ((size_t)(cursor - frame->payload) + 5u > (size_t)frame->payload_len) {
      return ERR_BAD_PAYLOAD;
    }
    event_group.delta_us = proto_read_le32(cursor);
    cursor += 4u;
    event_group.change_count = cursor[0];
    cursor += 1u;
    if ((event_group.change_count == 0u) || (event_group.change_count > MOTOR_COUNT)) {
      return ERR_BAD_PAYLOAD;
    }
    uint8_t seen_motor_mask = 0u;
    for (uint8_t change_idx = 0; change_idx < event_group.change_count; ++change_idx) {
      if ((size_t)(cursor - frame->payload) + 4u > (size_t)frame->payload_len) {
        return ERR_BAD_PAYLOAD;
      }
      const uint8_t motor_idx = cursor[0];
      const uint8_t flags = cursor[3];
      if (motor_idx >= PLAYBACK_MOTOR_COUNT) {
        return ERR_BAD_PAYLOAD;
      }
      if ((flags & ~STREAM_EVENT_GROUP_FLAG_FLIP_BEFORE_RESTART) != 0u) {
        return ERR_BAD_PAYLOAD;
      }
      if ((seen_motor_mask & (1u << motor_idx)) != 0u) {
        return ERR_BAD_PAYLOAD;
      }
      seen_motor_mask |= (uint8_t)(1u << motor_idx);
      uint16_t target_dhz = proto_read_le16(&cursor[1]);
      if (target_dhz > PLAYBACK_SAFE_MAX_FREQ_DHZ) {
        target_dhz = PLAYBACK_SAFE_MAX_FREQ_DHZ;
      }
      event_group.changes[change_idx].motor_idx = motor_idx;
      event_group.changes[change_idx].target_dhz = target_dhz;
      event_group.changes[change_idx].flags = flags;
      cursor += 4u;
    }
    if (!stream_queue_push(&event_group, 0u)) {
      return ERR_NO_CREDITS;
    }
  }

  if (cursor != &frame->payload[frame->payload_len]) {
    return ERR_BAD_PAYLOAD;
  }
  metrics_update_queue_high_water();
  if (snap.playing) {
    playback_runtime_wake();
  }
  return ERR_OK;
}

static runtime_err_t command_stream_end(void) {
  const runtime_state_t snap = state_get_snapshot();
  if (!snap.stream_open || snap.stream_end_received) {
    return ERR_BAD_STATE;
  }
  state_set_stream(true, true);
  if (snap.playing) {
    playback_runtime_wake();
  }
  return ERR_OK;
}

static runtime_err_t command_play(void) {
  return command_play_at((uint64_t)esp_timer_get_time());
}

static runtime_err_t command_play_at(uint64_t scheduled_start_device_us) {
  const runtime_state_t snap = state_get_snapshot();
  if (!snap.stream_open || snap.playing) {
    return ERR_BAD_STATE;
  }
  if (stream_queue_depth() == 0u) {
    return ERR_NO_CREDITS;
  }
  const runtime_err_t err = playback_runtime_start(scheduled_start_device_us);
  if (err != ERR_OK) {
    return err;
  }
  state_set_playhead_and_active(0u, 0u);
  state_set_playing(true);
  playback_runtime_wake();
  return ERR_OK;
}

static runtime_err_t command_stop(void) {
  playback_runtime_stop();
  state_set_playing(false);
  state_set_scheduled_start(0u);
  state_set_stream(false, false);
  state_set_playhead_and_active(0u, 0u);
  stream_queue_reset();
  return ERR_OK;
}

static runtime_err_t command_home(const proto_frame_t *frame) {
  return motion_commands_home(frame, g_setup.motors, state_get_snapshot);
}

static runtime_err_t command_warmup(const proto_frame_t *frame) {
  return motion_commands_warmup(frame, state_get_snapshot);
}

static runtime_err_t command_step_motion(const proto_frame_t *frame) {
  return motion_commands_step_motion(frame, state_get_snapshot);
}

static void handle_command(const proto_frame_t *frame) {
  runtime_err_t err = ERR_OK;

  switch (frame->cmd) {
    case PROTO_CMD_HELLO:
      if (!send_ack_hello(frame->seq)) {
        ESP_LOGW(TAG, "failed to send HELLO ACK");
      }
      return;
    case PROTO_CMD_SETUP:
      err = command_setup(frame);
      break;
    case PROTO_CMD_STREAM_BEGIN:
      err = command_stream_begin(frame);
      break;
    case PROTO_CMD_STREAM_APPEND:
      err = command_stream_append(frame);
      break;
    case PROTO_CMD_STREAM_END:
      err = command_stream_end();
      break;
    case PROTO_CMD_PLAY:
      err = command_play();
      break;
    case PROTO_CMD_PLAY_AT:
      if (frame->payload_len != 8u) {
        err = ERR_BAD_PAYLOAD;
      } else {
        const uint64_t scheduled_start_device_us =
          ((uint64_t)proto_read_le32(&frame->payload[0])) |
          (((uint64_t)proto_read_le32(&frame->payload[4])) << 32);
        err = command_play_at(scheduled_start_device_us);
        if (err == ERR_OK) {
          const runtime_state_t snap = state_get_snapshot();
          if (!send_ack_play_at(frame->seq, snap.scheduled_start_device_us)) {
            ESP_LOGW(TAG, "failed to send PLAY_AT ACK");
          }
          return;
        }
      }
      break;
    case PROTO_CMD_STOP:
      err = command_stop();
      break;
    case PROTO_CMD_HOME:
      err = command_home(frame);
      break;
    case PROTO_CMD_WARMUP:
      err = command_warmup(frame);
      break;
    case PROTO_CMD_STEP_MOTION:
      err = command_step_motion(frame);
      break;
    case PROTO_CMD_STATUS:
      if (!send_status(frame->seq)) {
        ESP_LOGW(TAG, "failed to send STATUS");
      }
      return;
    case PROTO_CMD_METRICS:
      if (!send_metrics(frame->seq)) {
        ESP_LOGW(TAG, "failed to send METRICS");
      }
      return;
    default:
      err = ERR_UNKNOWN_CMD;
      break;
  }

  if (err == ERR_OK) {
    if (!send_ack_basic(frame->seq, frame->cmd)) {
      ESP_LOGW(TAG, "failed to send ACK for cmd=0x%02X", frame->cmd);
    }
  } else if (!send_err(frame->seq, frame->cmd, err)) {
    ESP_LOGW(TAG, "failed to send ERR for cmd=0x%02X", frame->cmd);
  }
}

static void rx_task(void *arg) {
  (void)arg;

  stream_parser_t parser;
  stream_parser_init(&parser);

  uint8_t rx_bytes[UART_READ_CHUNK_SIZE];
  uint8_t packet_buf[PROTO_MAX_ENCODED_FRAME];
  uint8_t raw_scratch[PROTO_MAX_RAW_FRAME];

  while (true) {
    const int rx_len = uart_read_bytes(UART_NUM_0, rx_bytes, sizeof(rx_bytes), pdMS_TO_TICKS(50));
    if (rx_len <= 0) {
      continue;
    }

    for (int i = 0; i < rx_len; ++i) {
      size_t packet_len = 0u;
      const stream_parser_event_t ev = stream_parser_feed(
        &parser,
        rx_bytes[i],
        packet_buf,
        sizeof(packet_buf),
        &packet_len
      );

      if (ev == STREAM_PARSER_PACKET_DROPPED) {
        metrics_note_rx_parse_error();
        continue;
      }
      if (ev != STREAM_PARSER_PACKET_READY) {
        continue;
      }

      proto_frame_t frame = {0};
      const proto_decode_status_t status =
        proto_frame_decode_packet(packet_buf, packet_len, raw_scratch, sizeof(raw_scratch), &frame);
      if (status != PROTO_DECODE_OK) {
        if (status == PROTO_DECODE_CRC) {
          metrics_note_crc_parse_error();
        } else {
          metrics_note_rx_parse_error();
        }
        continue;
      }

      if (frame.version < PROTO_MIN_COMPAT_VERSION || frame.version > PROTO_VERSION) {
        (void)send_err(frame.seq, frame.cmd, ERR_BAD_VERSION);
        continue;
      }
      handle_command(&frame);
    }
  }
}

void app_main(void) {
  esp_log_level_set("*", ESP_LOG_NONE);

  const uart_config_t uart_cfg = {
    .baud_rate = 921600,
    .data_bits = UART_DATA_8_BITS,
    .parity = UART_PARITY_DISABLE,
    .stop_bits = UART_STOP_BITS_1,
    .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
    .source_clk = UART_SCLK_DEFAULT,
  };

  esp_err_t err = uart_driver_install(UART_NUM_0, UART_RX_BUFFER_SIZE, 0, 0, NULL, 0);
  if (err == ESP_ERR_INVALID_STATE) {
    ESP_LOGW(TAG, "UART0 driver already installed; reusing existing console driver");
  } else {
    ESP_ERROR_CHECK(err);
  }
  ESP_ERROR_CHECK(uart_param_config(UART_NUM_0, &uart_cfg));
  ESP_ERROR_CHECK(
    uart_set_pin(UART_NUM_0, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE)
  );

  if (!stream_queue_init()) {
    ESP_LOGE(TAG, "failed to initialize stream queue");
    return;
  }

  ESP_ERROR_CHECK(pulse_engine_init());
  ESP_ERROR_CHECK(
    playback_wave_engine_init(
      g_playback_run_accel_dhz_per_s,
      g_playback_launch_start_dhz,
      g_playback_launch_accel_dhz_per_s,
      g_playback_launch_crossover_dhz
    )
  );

  const playback_runtime_callbacks_t runtime_callbacks = {
    .state_get_snapshot = state_get_snapshot,
    .state_set_playing = state_set_playing,
    .state_set_scheduled_start = state_set_scheduled_start,
    .state_set_stream = state_set_stream,
    .state_set_playhead_and_active = state_set_playhead_and_active,
    .metrics_note_underrun = metrics_note_underrun,
    .metrics_note_timer_empty_event = metrics_note_timer_empty_event,
    .metrics_note_timer_restart = metrics_note_timer_restart,
    .metrics_note_event_group_started = metrics_note_event_group_started,
    .metrics_note_scheduler_guard_hit = metrics_note_scheduler_guard_hit,
    .metrics_note_late_us = metrics_note_late_us,
  };
  ESP_ERROR_CHECK(
    playback_runtime_init(
      &runtime_callbacks,
      MUSIC2_PLAYBACK_TIMER_ISR_DISPATCH != 0u
    )
  );

  xTaskCreate(playback_runtime_task, "playback_task", PLAYBACK_TASK_STACK, NULL, PLAYBACK_TASK_PRIO, NULL);
  xTaskCreate(rx_task, "rx_task", RX_TASK_STACK, NULL, RX_TASK_PRIO, NULL);
  vTaskDelete(NULL);
}

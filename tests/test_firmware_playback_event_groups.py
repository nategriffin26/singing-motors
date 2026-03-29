from __future__ import annotations

from pathlib import Path


def _main_source() -> str:
    return (Path(__file__).resolve().parents[1] / "firmware" / "esp32" / "src" / "main.c").read_text(
        encoding="utf-8"
    )


def _playback_runtime_source() -> str:
    return (
        Path(__file__).resolve().parents[1] / "firmware" / "esp32" / "src" / "playback_runtime.c"
    ).read_text(encoding="utf-8")


def _protocol_defs() -> str:
    return (
        Path(__file__).resolve().parents[1] / "firmware" / "esp32" / "src" / "protocol_defs.h"
    ).read_text(encoding="utf-8")


def _stream_queue_header() -> str:
    return (
        Path(__file__).resolve().parents[1] / "firmware" / "esp32" / "src" / "stream_queue.h"
    ).read_text(encoding="utf-8")


def test_protocol_version_is_bumped_for_playback_v2() -> None:
    defs = _protocol_defs()
    assert "#define PROTO_VERSION (3u)" in defs


def test_stream_queue_carries_sparse_event_groups() -> None:
    header = _stream_queue_header()
    assert "STREAM_EVENT_GROUP_QUEUE_CAPACITY" in header
    assert "STREAM_EVENT_GROUP_FLAG_FLIP_BEFORE_RESTART" in header
    assert "typedef struct {" in header
    assert "stream_motor_change_t" in header
    assert "stream_event_group_t" in header
    assert "uint32_t delta_us;" in header
    assert "uint8_t change_count;" in header


def test_hello_ack_includes_playback_acceleration_metadata() -> None:
    source = _main_source()
    assert "uint8_t payload[19] = {0};" in source
    assert "FEATURE_FLAG_CONTINUOUS_PLAYBACK_ENGINE" in source
    assert "FEATURE_FLAG_PLAYBACK_SETUP_PROFILE" in source
    assert "FEATURE_FLAG_SPEECH_ASSIST" in source
    assert "proto_write_le32(&payload[13], g_playback_run_accel_dhz_per_s);" in source
    assert "payload[17] = playback_caps.motor_count;" in source
    assert "payload[18] = EXACT_MOTION_FEATURE_DIRECTIONAL_STEP_MOTION;" in source


def test_stream_append_parses_event_group_shape_and_reserved_flags() -> None:
    source = _main_source()
    assert "const uint8_t event_group_count = frame->payload[0];" in source
    assert "event_group.change_count = cursor[0];" in source
    assert "const uint8_t flags = cursor[3];" in source
    assert "(flags & ~STREAM_EVENT_GROUP_FLAG_FLIP_BEFORE_RESTART) != 0u" in source
    assert "seen_motor_mask" in source
    assert "event_group.changes[change_idx].target_dhz = target_dhz;" in source


def test_playback_scheduler_tracks_pending_event_groups_by_delta() -> None:
    source = _playback_runtime_source()
    assert "static stream_event_group_t s_pending_event_group = {0};" in source
    assert "static bool s_pending_event_group_loaded = false;" in source
    assert "const int64_t due_us = s_expected_boundary_us + (int64_t)s_pending_event_group.delta_us;" in source
    assert "playhead_us += s_pending_event_group.delta_us;" in source
    assert "s_callbacks.metrics_note_event_group_started();" in source
    assert "motor_event_executor_from_stream_event_group(&s_pending_event_group, &batch)" in source
    assert "playback_wave_engine_active_motor_count()" in source

from __future__ import annotations

import pytest

from music2.models import PlaybackEventGroup, PlaybackMotorChange, Segment
from music2.protocol import (
    Ack,
    Command,
    EXACT_MOTION_FEATURE_DIRECTIONAL_STEP_MOTION,
    EVENT_CHANGE_FLAG_FLIP_BEFORE_RESTART,
    MAX_EVENT_GROUPS_PER_APPEND,
    PROTOCOL_VERSION,
    ProtocolError,
    StepMotionMotorParams,
    StepMotionPhase,
    cobs_decode,
    cobs_encode,
    crc16_ccitt,
    decode_setup_payload,
    decode_step_motion_payload,
    decode_home_payload,
    decode_frame,
    decode_stream_append_event_groups_payload,
    decode_stream_append_payload,
    encode_step_motion_payload,
    encode_frame,
    encode_home_payload,
    encode_setup_payload,
    encode_stream_append_event_groups_payload,
    encode_stream_append_payload,
    parse_hello_ack,
    parse_ack,
    parse_err,
    parse_metrics_payload,
)


def test_cobs_round_trip_with_zeros() -> None:
    payload = b"\x11\x00\x22\x00\x33"
    encoded = cobs_encode(payload)
    assert b"\x00" not in encoded
    decoded = cobs_decode(encoded)
    assert decoded == payload


def test_encode_decode_frame_round_trip() -> None:
    frame = encode_frame(Command.HELLO, seq=7, payload=b"abc")
    parsed = decode_frame(frame)
    assert parsed.version == PROTOCOL_VERSION == 3
    assert parsed.command == Command.HELLO
    assert parsed.seq == 7
    assert parsed.payload == b"abc"


def test_decode_frame_rejects_bad_crc() -> None:
    frame = bytearray(encode_frame(Command.PLAY, seq=3, payload=b""))
    frame[-2] ^= 0x01
    with pytest.raises(ProtocolError, match="CRC mismatch"):
        decode_frame(bytes(frame))


def _mutate_encoded_frame(frame: bytes, *, version: int | None = None, command: int | None = None) -> bytes:
    raw = bytearray(cobs_decode(frame[:-1]))
    body = bytearray(raw[:-2])
    if version is not None:
        body[0] = version & 0xFF
    if command is not None:
        body[1] = command & 0xFF
    crc = crc16_ccitt(bytes(body)).to_bytes(2, "little")
    return cobs_encode(bytes(body) + crc) + b"\x00"


def test_decode_frame_rejects_bad_protocol_version() -> None:
    frame = encode_frame(Command.PLAY, seq=3, payload=b"")
    mutated = _mutate_encoded_frame(frame, version=9)
    with pytest.raises(ProtocolError, match="protocol version mismatch"):
        decode_frame(mutated)


def test_decode_frame_rejects_unknown_command() -> None:
    frame = encode_frame(Command.PLAY, seq=3, payload=b"")
    mutated = _mutate_encoded_frame(frame, command=0xAA)
    with pytest.raises(ProtocolError, match="unknown frame command"):
        decode_frame(mutated)


def test_stream_append_payload_round_trip() -> None:
    segments = [
        Segment(
            duration_us=5000,
            motor_freq_hz=(110.0, 220.0, 330.0, 0.0, 0.0, 55.5, 10.0, 0.0),
            direction_flip_mask=0x21,
        ),
        Segment(duration_us=7000, motor_freq_hz=(0.0, 0.0, 0.0, 100.0, 50.0, 0.0, 0.0, 0.0)),
    ]
    payload = encode_stream_append_payload(segments)
    parsed = decode_stream_append_payload(payload)
    assert len(parsed) == 2
    assert parsed[0].duration_us == 5000
    assert parsed[0].motor_freq_hz[0] == 110.0
    assert parsed[0].motor_freq_hz[5] == 55.5
    assert parsed[0].direction_flip_mask == 0x21
    assert parsed[1].direction_flip_mask == 0
    assert parsed[1].duration_us == 7000


def test_stream_append_payload_pads_to_eight_motors() -> None:
    segments = [Segment(duration_us=9000, motor_freq_hz=(110.0, 220.0, 0.0, 55.0), direction_flip_mask=0x08)]
    payload = encode_stream_append_payload(segments)
    parsed = decode_stream_append_payload(payload)
    assert len(parsed[0].motor_freq_hz) == 8
    assert parsed[0].motor_freq_hz[0] == 110.0
    assert parsed[0].motor_freq_hz[1] == 220.0
    assert parsed[0].motor_freq_hz[3] == 55.0
    assert parsed[0].motor_freq_hz[7] == 0.0
    assert parsed[0].direction_flip_mask == 0x08


def test_stream_append_event_group_payload_round_trip() -> None:
    event_groups = [
        PlaybackEventGroup(
            delta_us=0,
            changes=(
                PlaybackMotorChange(motor_idx=0, target_hz=110.0, flip_before_restart=True),
                PlaybackMotorChange(motor_idx=3, target_hz=55.5),
            ),
        ),
        PlaybackEventGroup(
            delta_us=12_500,
            changes=(PlaybackMotorChange(motor_idx=0, target_hz=0.0),),
        ),
    ]
    payload = encode_stream_append_event_groups_payload(event_groups)
    parsed = decode_stream_append_event_groups_payload(payload)
    assert parsed == event_groups


def test_stream_append_event_group_payload_rejects_reserved_flags() -> None:
    payload = bytearray()
    payload.append(1)  # one event group
    payload.extend((1000).to_bytes(4, "little"))
    payload.append(1)  # change count
    payload.append(2)  # motor idx
    payload.extend((4400).to_bytes(2, "little"))
    payload.append(EVENT_CHANGE_FLAG_FLIP_BEFORE_RESTART | 0x02)
    with pytest.raises(ProtocolError, match="reserved flags"):
        decode_stream_append_event_groups_payload(bytes(payload))


def test_stream_append_event_group_payload_rejects_too_many_event_groups() -> None:
    payload = bytes([MAX_EVENT_GROUPS_PER_APPEND + 1])
    with pytest.raises(ProtocolError, match="event_group_count exceeds payload capacity"):
        decode_stream_append_event_groups_payload(payload)


def test_stream_append_event_group_payload_rejects_duplicate_motor_changes() -> None:
    with pytest.raises(ValueError, match="duplicate motor_idx"):
        PlaybackEventGroup(
            delta_us=0,
            changes=(
                PlaybackMotorChange(motor_idx=1, target_hz=220.0),
                PlaybackMotorChange(motor_idx=1, target_hz=330.0),
            ),
        )


def test_stream_append_event_group_payload_rejects_duplicate_motor_changes_from_wire() -> None:
    payload = bytearray()
    payload.append(1)
    payload.extend((1000).to_bytes(4, "little"))
    payload.append(2)
    payload.append(1)
    payload.extend((2200).to_bytes(2, "little"))
    payload.append(0)
    payload.append(1)
    payload.extend((3300).to_bytes(2, "little"))
    payload.append(0)
    with pytest.raises(ProtocolError, match="event group 0 is invalid"):
        decode_stream_append_event_groups_payload(bytes(payload))


def test_stream_append_event_group_payload_rejects_invalid_motor_idx_from_wire() -> None:
    payload = bytearray()
    payload.append(1)
    payload.extend((1000).to_bytes(4, "little"))
    payload.append(1)
    payload.append(9)
    payload.extend((4400).to_bytes(2, "little"))
    payload.append(0)
    with pytest.raises(ProtocolError, match="change 0 is invalid"):
        decode_stream_append_event_groups_payload(bytes(payload))


def test_parse_hello_ack_v1_legacy_shape() -> None:
    hello = parse_hello_ack(
        Ack(
            for_command=Command.HELLO,
            credits=16,
            queue_depth=0,
            extra=bytes([1, 8, 0x1F]) + (128).to_bytes(2, "little") + (25).to_bytes(2, "little"),
        )
    )
    assert hello is not None
    assert hello.protocol_version == 1
    assert hello.motor_count == 8
    assert hello.queue_capacity == 128
    assert hello.scheduler_tick_us == 25
    assert hello.playback_accel_dhz_per_s == 0


def test_parse_hello_ack_v2_extended_shape() -> None:
    hello = parse_hello_ack(
        Ack(
            for_command=Command.HELLO,
            credits=16,
            queue_depth=0,
            extra=(
                bytes([2, 8, 0x1F])
                + (128).to_bytes(2, "little")
                + (25).to_bytes(2, "little")
                + (100000).to_bytes(4, "little")
            ),
        )
    )
    assert hello is not None
    assert hello.playback_accel_dhz_per_s == 100000
    assert hello.playback_motor_count == 8


def test_parse_hello_ack_continuous_playback_shape() -> None:
    hello = parse_hello_ack(
        Ack(
            for_command=Command.HELLO,
            credits=16,
            queue_depth=0,
            extra=(
                bytes([2, 8, 0x7F])
                + (128).to_bytes(2, "little")
                + (25).to_bytes(2, "little")
                + (80_000).to_bytes(4, "little")
                + bytes([6])
                + bytes([EXACT_MOTION_FEATURE_DIRECTIONAL_STEP_MOTION])
            ),
        )
    )
    assert hello is not None
    assert hello.playback_run_accel_dhz_per_s == 80_000
    assert hello.playback_motor_count == 6
    assert hello.exact_motion_flags == EXACT_MOTION_FEATURE_DIRECTIONAL_STEP_MOTION
    assert hello.exact_direction_step_motion_supported is True


def test_parse_hello_ack_rejects_partial_extended_shape() -> None:
    with pytest.raises(ProtocolError, match="HELLO ACK extra size mismatch"):
        parse_hello_ack(
            Ack(
                for_command=Command.HELLO,
                credits=16,
                queue_depth=0,
                extra=(
                    bytes([2, 8, 0x1F])
                    + (128).to_bytes(2, "little")
                    + (25).to_bytes(2, "little")
                    + b"\x01"
                ),
            )
        )


def test_setup_payload_round_trip_with_playback_profile() -> None:
    payload = encode_setup_payload(
        motors=6,
        idle_mode="duplicate",
        min_note=21,
        max_note=108,
        transpose=0,
        playback_run_accel_hz_per_s=8000.0,
        playback_launch_start_hz=60.0,
        playback_launch_accel_hz_per_s=5000.0,
        playback_launch_crossover_hz=180.0,
    )
    decoded = decode_setup_payload(payload)
    assert decoded.playback_run_accel_dhz_per_s == 80_000
    assert decoded.playback_launch_start_dhz == 600
    assert decoded.playback_launch_accel_dhz_per_s == 50_000
    assert decoded.playback_launch_crossover_dhz == 1_800


def test_setup_payload_round_trip_with_speech_assist() -> None:
    payload = encode_setup_payload(
        motors=6,
        idle_mode="duplicate",
        min_note=0,
        max_note=127,
        transpose=0,
        playback_run_accel_hz_per_s=8000.0,
        playback_launch_start_hz=60.0,
        playback_launch_accel_hz_per_s=5000.0,
        playback_launch_crossover_hz=180.0,
        speech_assist_control_interval_us=500,
        speech_assist_release_accel_hz_per_s=3200.0,
    )
    decoded = decode_setup_payload(payload)
    assert decoded.playback_run_accel_dhz_per_s == 80_000
    assert decoded.speech_assist_control_interval_us == 500
    assert decoded.speech_assist_release_accel_dhz_per_s == 32_000


def test_setup_payload_requires_full_playback_profile() -> None:
    with pytest.raises(ValueError, match="requires all four"):
        encode_setup_payload(
            motors=6,
            idle_mode="duplicate",
            min_note=21,
            max_note=108,
            transpose=0,
            playback_run_accel_hz_per_s=8000.0,
        )


def test_setup_payload_requires_both_speech_assist_values() -> None:
    with pytest.raises(ValueError, match="requires both speech assist values"):
        encode_setup_payload(
            motors=6,
            idle_mode="duplicate",
            min_note=0,
            max_note=127,
            transpose=0,
            playback_run_accel_hz_per_s=8000.0,
            playback_launch_start_hz=60.0,
            playback_launch_accel_hz_per_s=5000.0,
            playback_launch_crossover_hz=180.0,
            speech_assist_control_interval_us=500,
        )


def test_step_motion_payload_round_trip() -> None:
    params = [
        StepMotionMotorParams(
            phases=(
                StepMotionPhase(
                    target_steps=1600,
                    peak_hz=250.0,
                    accel_hz_per_s=180.0,
                    decel_hz_per_s=220.0,
                    hold_ms=50,
                    direction=-1,
                ),
                StepMotionPhase(
                    target_steps=0,
                    peak_hz=0.0,
                    accel_hz_per_s=100.0,
                    decel_hz_per_s=100.0,
                    hold_ms=125,
                ),
            ),
            start_delay_ms=100,
            trigger_motor=None,
            trigger_steps=0,
        ),
        StepMotionMotorParams(
            phases=(
                StepMotionPhase(
                    target_steps=800,
                    peak_hz=120.0,
                    accel_hz_per_s=90.0,
                    decel_hz_per_s=90.0,
                    hold_ms=0,
                ),
            ),
            start_delay_ms=0,
            trigger_motor=0,
            trigger_steps=400,
        ),
    ]
    payload = encode_step_motion_payload(params)
    decoded = decode_step_motion_payload(payload)
    assert len(decoded) == 2
    assert decoded[0].phases[0].target_steps == 1600
    assert decoded[0].phases[0].direction == -1
    assert decoded[0].phases[1].hold_ms == 125
    assert decoded[1].trigger_motor == 0
    assert decoded[1].trigger_steps == 400


def test_ack_parsing_with_queue_fields() -> None:
    ack_payload = bytes([int(Command.STREAM_BEGIN), 0x00, 0x07, 0x00, 0x02, 0x00]) + b"hello"
    ack = parse_ack(ack_payload)
    assert ack.for_command == Command.STREAM_BEGIN
    assert ack.credits == 7
    assert ack.queue_depth == 2
    assert ack.extra == b"hello"


def test_parse_ack_rejects_unknown_for_command() -> None:
    with pytest.raises(ProtocolError, match="unknown ACK for_command"):
        parse_ack(bytes([0xAA, 0x00]))


def test_parse_err_rejects_unknown_for_command() -> None:
    payload = bytes([0xAA, 0x05]) + (0).to_bytes(2, "little") + (0).to_bytes(2, "little")
    with pytest.raises(ProtocolError, match="unknown ERR for_command"):
        parse_err(payload)


def test_home_payload_round_trip() -> None:
    payload = encode_home_payload(
        steps_per_rev=800,
        home_hz=80.0,
        start_hz=60.0,
        accel_hz_per_s=200.0,
    )
    parsed = decode_home_payload(payload)
    assert parsed.steps_per_rev == 800
    assert parsed.start_freq_dhz == 600
    assert parsed.home_freq_dhz == 800
    assert parsed.accel_hz_per_s_dhz == 2000


def test_home_payload_v1_decode_backward_compatible() -> None:
    payload = (800).to_bytes(2, "little") + (800).to_bytes(2, "little")
    parsed = decode_home_payload(payload)
    assert parsed.steps_per_rev == 800
    assert parsed.start_freq_dhz == 800
    assert parsed.home_freq_dhz == 800
    assert parsed.accel_hz_per_s_dhz == 0


def test_home_payload_rejects_invalid_values() -> None:
    with pytest.raises(ValueError, match="steps_per_rev"):
        encode_home_payload(steps_per_rev=0, home_hz=80.0)
    with pytest.raises(ValueError, match="home_hz"):
        encode_home_payload(steps_per_rev=800, home_hz=0.0)
    with pytest.raises(ValueError, match="start_hz"):
        encode_home_payload(steps_per_rev=800, home_hz=80.0, start_hz=0.0)
    with pytest.raises(ValueError, match="start_hz"):
        encode_home_payload(steps_per_rev=800, home_hz=80.0, start_hz=100.0)


def test_home_payload_rejects_wrong_size() -> None:
    with pytest.raises(ProtocolError, match="HOME payload size mismatch"):
        decode_home_payload(b"\x00\x00\x00")


def test_parse_metrics_payload_v1_legacy() -> None:
    payload = (
        (3).to_bytes(4, "little")  # underruns
        + (42).to_bytes(2, "little")  # high water
        + (0).to_bytes(2, "little")
        + (1500).to_bytes(4, "little")  # late max
        + (2).to_bytes(4, "little")  # crc
        + (7).to_bytes(4, "little")  # rx parse errors
        + (9).to_bytes(2, "little")  # queue depth
        + (11).to_bytes(2, "little")  # credits
    )
    metrics = parse_metrics_payload(payload)
    assert metrics.underrun_count == 3
    assert metrics.queue_high_water == 42
    assert metrics.scheduling_late_max_us == 1500
    assert metrics.crc_parse_errors == 2
    assert metrics.rx_parse_errors == 7
    assert metrics.timer_empty_events == 0
    assert metrics.timer_restart_count == 0
    assert metrics.event_groups_started == 0
    assert metrics.control_overrun_count == 0


def test_parse_metrics_payload_v2_extended() -> None:
    payload = (
        (0).to_bytes(4, "little")
        + (12).to_bytes(2, "little")
        + (0).to_bytes(2, "little")
        + (250).to_bytes(4, "little")
        + (0).to_bytes(4, "little")
        + (1).to_bytes(4, "little")
        + (4).to_bytes(2, "little")
        + (8).to_bytes(2, "little")
        + (5).to_bytes(4, "little")  # timer empty events
        + (3).to_bytes(4, "little")  # timer restart count
        + (777).to_bytes(4, "little")  # segments started
    )
    metrics = parse_metrics_payload(payload)
    assert metrics.rx_parse_errors == 1
    assert metrics.timer_empty_events == 5
    assert metrics.timer_restart_count == 3
    assert metrics.event_groups_started == 777


def test_parse_metrics_payload_v3_extended() -> None:
    payload = (
        (0).to_bytes(4, "little")
        + (12).to_bytes(2, "little")
        + (0).to_bytes(2, "little")
        + (250).to_bytes(4, "little")
        + (0).to_bytes(4, "little")
        + (1).to_bytes(4, "little")
        + (4).to_bytes(2, "little")
        + (8).to_bytes(2, "little")
        + (5).to_bytes(4, "little")  # timer empty events
        + (3).to_bytes(4, "little")  # timer restart count
        + (777).to_bytes(4, "little")  # segments started
        + (2).to_bytes(4, "little")  # scheduler guard hits
        + (125).to_bytes(4, "little")  # pulse late max
        + (9).to_bytes(4, "little")  # pulse edge drops
    )
    metrics = parse_metrics_payload(payload)
    assert metrics.scheduler_guard_hits == 2
    assert metrics.control_late_max_us == 125
    assert metrics.control_overrun_count == 9


def test_parse_metrics_payload_v4_with_continuous_engine_counters() -> None:
    payload = (
        (0).to_bytes(4, "little")
        + (12).to_bytes(2, "little")
        + (0).to_bytes(2, "little")
        + (250).to_bytes(4, "little")
        + (0).to_bytes(4, "little")
        + (1).to_bytes(4, "little")
        + (4).to_bytes(2, "little")
        + (8).to_bytes(2, "little")
        + (5).to_bytes(4, "little")  # timer empty events
        + (3).to_bytes(4, "little")  # timer restart count
        + (777).to_bytes(4, "little")  # segments started
        + (2).to_bytes(4, "little")  # scheduler guard hits
        + (125).to_bytes(4, "little")  # control late max
        + (9).to_bytes(4, "little")  # control overruns
        + (17).to_bytes(4, "little")  # wave period updates
        + (33).to_bytes(4, "little")  # motor starts
        + (31).to_bytes(4, "little")  # motor stops
        + (7).to_bytes(4, "little")  # flip restarts
        + (3).to_bytes(4, "little")  # launch guards
        + (1).to_bytes(4, "little")  # engine fault count
        + (0x2A).to_bytes(4, "little")  # engine fault mask
    )
    metrics = parse_metrics_payload(payload)
    assert metrics.control_overrun_count == 9
    assert metrics.wave_period_update_count == 17
    assert metrics.motor_start_count == 33
    assert metrics.motor_stop_count == 31
    assert metrics.flip_restart_count == 7
    assert metrics.launch_guard_count == 3
    assert metrics.engine_fault_count == 1
    assert metrics.engine_fault_mask == 0x2A


def test_parse_metrics_payload_v5_with_fault_breakdown() -> None:
    payload = (
        (0).to_bytes(4, "little")
        + (12).to_bytes(2, "little")
        + (0).to_bytes(2, "little")
        + (250).to_bytes(4, "little")
        + (0).to_bytes(4, "little")
        + (1).to_bytes(4, "little")
        + (4).to_bytes(2, "little")
        + (8).to_bytes(2, "little")
        + (5).to_bytes(4, "little")
        + (3).to_bytes(4, "little")
        + (777).to_bytes(4, "little")
        + (2).to_bytes(4, "little")
        + (125).to_bytes(4, "little")
        + (9).to_bytes(4, "little")
        + (17).to_bytes(4, "little")
        + (33).to_bytes(4, "little")
        + (31).to_bytes(4, "little")
        + (7).to_bytes(4, "little")
        + (3).to_bytes(4, "little")
        + (1).to_bytes(4, "little")
        + (0x2A).to_bytes(4, "little")
        + (11).to_bytes(4, "little")
        + (12).to_bytes(4, "little")
        + (13).to_bytes(4, "little")
        + (14).to_bytes(4, "little")
        + (15).to_bytes(4, "little")
        + (16).to_bytes(4, "little")
        + (10).to_bytes(4, "little")
        + (5).to_bytes(4, "little")
    )
    metrics = parse_metrics_payload(payload)
    assert metrics.engine_fault_attach_count == 11
    assert metrics.engine_fault_detach_count == 12
    assert metrics.engine_fault_period_count == 13
    assert metrics.engine_fault_force_count == 14
    assert metrics.engine_fault_timer_count == 15
    assert metrics.engine_fault_invalid_change_count == 16
    assert metrics.engine_fault_last_reason == 10
    assert metrics.engine_fault_last_motor == 5


def test_parse_metrics_payload_v7_with_position_tracking_fields() -> None:
    payload = (
        (0).to_bytes(4, "little")
        + (12).to_bytes(2, "little")
        + (0).to_bytes(2, "little")
        + (250).to_bytes(4, "little")
        + (0).to_bytes(4, "little")
        + (1).to_bytes(4, "little")
        + (4).to_bytes(2, "little")
        + (8).to_bytes(2, "little")
        + (5).to_bytes(4, "little")
        + (3).to_bytes(4, "little")
        + (777).to_bytes(4, "little")
        + (2).to_bytes(4, "little")
        + (125).to_bytes(4, "little")
        + (9).to_bytes(4, "little")
        + (17).to_bytes(4, "little")
        + (33).to_bytes(4, "little")
        + (31).to_bytes(4, "little")
        + (7).to_bytes(4, "little")
        + (3).to_bytes(4, "little")
        + (1).to_bytes(4, "little")
        + (0x2A).to_bytes(4, "little")
        + (11).to_bytes(4, "little")
        + (12).to_bytes(4, "little")
        + (13).to_bytes(4, "little")
        + (14).to_bytes(4, "little")
        + (15).to_bytes(4, "little")
        + (16).to_bytes(4, "little")
        + (10).to_bytes(4, "little")
        + (5).to_bytes(4, "little")
        + (1_234).to_bytes(4, "little")
        + (1_200).to_bytes(4, "little")
        + (34).to_bytes(4, "little")
        + (0x15).to_bytes(4, "little")
        + (0x03).to_bytes(4, "little")
        + (0x12).to_bytes(4, "little")
        + (27).to_bytes(4, "little")
    )
    metrics = parse_metrics_payload(payload)
    assert metrics.inferred_pulse_total == 1_234
    assert metrics.measured_pulse_total == 1_200
    assert metrics.measured_pulse_drift_total == 34
    assert metrics.measured_pulse_active_mask == 0x15
    assert metrics.exact_position_lost_mask == 0x03
    assert metrics.playback_position_unreliable_mask == 0x12
    assert metrics.playback_signed_position_drift_total == 27

from __future__ import annotations

import errno
import termios

import music2.serial_client as serial_client_module
import pytest
from music2.models import PlaybackEventGroup, PlaybackMotorChange, Segment
from music2.protocol import (
    Command,
    EXACT_MOTION_FEATURE_DIRECTIONAL_STEP_MOTION,
    FEATURE_FLAG_CONTINUOUS_PLAYBACK_ENGINE,
    FEATURE_FLAG_DIRECTION_FLIP,
    FEATURE_FLAG_HOME,
    FEATURE_FLAG_PLAYBACK_SETUP_PROFILE,
    FEATURE_FLAG_SPEECH_ASSIST,
    FEATURE_FLAG_STEP_MOTION,
    FEATURE_FLAG_TIMED_STREAMING,
    FEATURE_FLAG_WARMUP,
    ProtocolError,
    StepMotionMotorParams,
    StepMotionPhase,
    decode_frame,
    decode_home_payload,
    decode_setup_payload,
    decode_step_motion_payload,
    decode_stream_append_event_groups_payload,
    decode_stream_append_payload,
    decode_stream_begin_payload,
    encode_frame,
)
from music2.serial_client import SerialClient, SerialClientError


class SimulatedSerialDevice:
    def __init__(
        self,
        queue_capacity: int = 6,
        *,
        support_home: bool = True,
        support_direction_flip: bool = True,
        support_speech_assist: bool = True,
        drop_first_append_ack: bool = False,
    ) -> None:
        self._read_buffer = bytearray()
        self.queue_capacity = queue_capacity
        self.support_home = support_home
        self.support_direction_flip = support_direction_flip
        self.support_speech_assist = support_speech_assist
        self.drop_first_append_ack = drop_first_append_ack
        self.queue_depth = 0
        self.received_segments = 0
        self.append_call_count = 0
        self.playing = False
        self.stream_end = False
        self.playhead_us = 0
        self.in_waiting = 0
        self.last_setup = None
        self.last_stream_begin = None
        self.last_home = None
        self.last_step_motion = None
        self.last_append_segments = None
        self.playback_motor_count = 6

    def write(self, data: bytes) -> int:
        packet = decode_frame(data)
        response = self._handle(packet.command, packet.seq, packet.payload)
        self._read_buffer.extend(response)
        self.in_waiting = len(self._read_buffer)
        return len(data)

    def read_until(self, expected: bytes = b"\n") -> bytes:
        idx = self._read_buffer.find(expected)
        if idx < 0:
            return b""
        chunk = bytes(self._read_buffer[: idx + 1])
        del self._read_buffer[: idx + 1]
        self.in_waiting = len(self._read_buffer)
        return chunk

    def reset_input_buffer(self) -> None:
        self._read_buffer.clear()
        self.in_waiting = 0

    def close(self) -> None:
        return None

    def flush(self) -> None:
        return None

    def _ack(self, for_command: Command, seq: int, credits: int, depth: int, extra: bytes = b"") -> bytes:
        payload = bytes([int(for_command), 0x00])
        payload += credits.to_bytes(2, "little")
        payload += depth.to_bytes(2, "little")
        payload += extra
        return encode_frame(Command.ACK, seq=seq, payload=payload)

    def _err(self, for_command: Command, seq: int, code: int) -> bytes:
        payload = bytes([int(for_command), code])
        payload += max(0, self.queue_capacity - self.queue_depth).to_bytes(2, "little")
        payload += self.queue_depth.to_bytes(2, "little")
        return encode_frame(Command.ERR, seq=seq, payload=payload)

    def _status_payload(self) -> bytes:
        if self.playing and self.queue_depth > 0:
            self.queue_depth -= 1
            self.playhead_us += 10_000
        if self.playing and self.stream_end and self.queue_depth == 0:
            self.playing = False

        state_flags = 0
        if self.playing:
            state_flags |= 0x01
        if not self.stream_end:
            state_flags |= 0x02
        if self.stream_end:
            state_flags |= 0x04

        credits = max(0, self.queue_capacity - self.queue_depth)
        active_motors = 4 if self.playing else 0

        payload = bytes([1, state_flags, 8, 0])
        payload += self.queue_depth.to_bytes(2, "little")
        payload += self.queue_capacity.to_bytes(2, "little")
        payload += credits.to_bytes(2, "little")
        payload += bytes([active_motors, 0])
        payload += self.playhead_us.to_bytes(4, "little")
        return payload

    def _metrics_payload(self) -> bytes:
        payload = (0).to_bytes(4, "little")
        payload += (self.queue_capacity).to_bytes(2, "little")
        payload += (0).to_bytes(2, "little")
        payload += (500).to_bytes(4, "little")
        payload += (0).to_bytes(4, "little")
        payload += (0).to_bytes(4, "little")
        payload += self.queue_depth.to_bytes(2, "little")
        payload += max(0, self.queue_capacity - self.queue_depth).to_bytes(2, "little")
        return payload

    def _handle(self, command: Command, seq: int, payload: bytes) -> bytes:
        if command == Command.HELLO:
            feature_flags = (
                FEATURE_FLAG_TIMED_STREAMING
                | (FEATURE_FLAG_HOME if self.support_home else 0x00)
                | FEATURE_FLAG_WARMUP
                | FEATURE_FLAG_STEP_MOTION
                | (FEATURE_FLAG_DIRECTION_FLIP if self.support_direction_flip else 0x00)
                | FEATURE_FLAG_CONTINUOUS_PLAYBACK_ENGINE
                | FEATURE_FLAG_PLAYBACK_SETUP_PROFILE
                | (FEATURE_FLAG_SPEECH_ASSIST if self.support_speech_assist else 0x00)
            )
            extra = bytes([2, 8, feature_flags])
            extra += self.queue_capacity.to_bytes(2, "little")
            extra += (50).to_bytes(2, "little")
            extra += (80_000).to_bytes(4, "little")
            extra += bytes([self.playback_motor_count])
            extra += bytes([EXACT_MOTION_FEATURE_DIRECTIONAL_STEP_MOTION])
            return self._ack(Command.HELLO, seq, self.queue_capacity, self.queue_depth, extra=extra)

        if command == Command.SETUP:
            self.last_setup = decode_setup_payload(payload)
            return self._ack(Command.SETUP, seq, self.queue_capacity, self.queue_depth)

        if command == Command.STREAM_BEGIN:
            self.last_stream_begin = decode_stream_begin_payload(payload)
            self.queue_depth = 0
            self.stream_end = False
            self.playing = False
            return self._ack(Command.STREAM_BEGIN, seq, self.queue_capacity, self.queue_depth)

        if command == Command.STREAM_APPEND:
            try:
                event_groups = decode_stream_append_event_groups_payload(payload)
                self.last_append_segments = event_groups
                append_count = len(event_groups)
            except ProtocolError:
                segments = decode_stream_append_payload(payload)
                self.last_append_segments = segments
                append_count = len(segments)
            self.append_call_count += 1
            if self.queue_depth + append_count > self.queue_capacity:
                return self._err(Command.STREAM_APPEND, seq, 5)
            self.queue_depth += append_count
            self.received_segments += append_count
            if self.drop_first_append_ack and self.append_call_count == 1:
                return b""
            return self._ack(Command.STREAM_APPEND, seq, self.queue_capacity - self.queue_depth, self.queue_depth)

        if command == Command.STREAM_END:
            self.stream_end = True
            return self._ack(Command.STREAM_END, seq, self.queue_capacity - self.queue_depth, self.queue_depth)

        if command == Command.PLAY:
            self.playing = True
            return self._ack(Command.PLAY, seq, self.queue_capacity - self.queue_depth, self.queue_depth)

        if command == Command.STOP:
            self.playing = False
            self.queue_depth = 0
            return self._ack(Command.STOP, seq, self.queue_capacity, self.queue_depth)

        if command == Command.HOME:
            if not self.support_home:
                return self._err(Command.HOME, seq, 2)
            self.last_home = decode_home_payload(payload)
            return self._ack(Command.HOME, seq, self.queue_capacity - self.queue_depth, self.queue_depth)

        if command == Command.STATUS:
            return encode_frame(Command.STATUS, seq=seq, payload=self._status_payload())

        if command == Command.METRICS:
            return encode_frame(Command.METRICS, seq=seq, payload=self._metrics_payload())

        if command == Command.STEP_MOTION:
            self.last_step_motion = decode_step_motion_payload(payload)
            return self._ack(Command.STEP_MOTION, seq, self.queue_capacity - self.queue_depth, self.queue_depth)

        return self._err(command, seq, 2)


class _FlakyStatusPollDevice(SimulatedSerialDevice):
    def __init__(self, *, drop_status_writes: int, queue_capacity: int = 6) -> None:
        super().__init__(queue_capacity=queue_capacity)
        self._drop_status_writes = max(0, drop_status_writes)

    def _handle(self, command: Command, seq: int, payload: bytes) -> bytes:
        if command == Command.STATUS and self._drop_status_writes > 0:
            self._drop_status_writes -= 1
            return b""
        return super()._handle(command, seq, payload)


def _event_group(delta_us: int, target_hz: float, *, motor_idx: int = 0, flip_before_restart: bool = False) -> PlaybackEventGroup:
    return PlaybackEventGroup(
        delta_us=delta_us,
        changes=(PlaybackMotorChange(motor_idx=motor_idx, target_hz=target_hz, flip_before_restart=flip_before_restart),),
    )


def test_serial_client_stream_and_play() -> None:
    device = SimulatedSerialDevice(queue_capacity=6)
    client = SerialClient(
        port="/dev/null",
        baudrate=921600,
        timeout_s=0.2,
        write_timeout_s=0.2,
        retries=1,
        serial_factory=lambda: device,
    )

    event_groups = [
        _event_group(10_000, 100.0),
        _event_group(10_000, 110.0),
        _event_group(10_000, 120.0),
        _event_group(10_000, 130.0),
        _event_group(10_000, 140.0),
        _event_group(10_000, 150.0),
        _event_group(10_000, 160.0),
    ]

    with client:
        hello = client.hello()
        assert hello["motor_count"] == 8
        assert hello["protocol_version"] == 2
        assert hello["playback_run_accel_dhz_per_s"] == 80_000
        assert hello["playback_motor_count"] == 6
        client.setup(
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
        client.stream_song_and_play(event_groups, lookahead_ms=50)
        status = client.status()
        metrics = client.metrics()

    assert device.last_setup is not None
    assert device.last_setup.playback_run_accel_dhz_per_s == 80_000
    assert device.last_setup.playback_launch_start_dhz == 600
    assert device.last_stream_begin is not None
    assert device.last_stream_begin.total_segments == len(event_groups)
    assert device.received_segments == len(event_groups)
    assert status.queue_depth == 0
    assert metrics.queue_high_water >= 0


def test_serial_client_setup_round_trip_with_speech_assist() -> None:
    device = SimulatedSerialDevice(queue_capacity=6, support_speech_assist=True)
    client = SerialClient(
        port="/dev/null",
        baudrate=921600,
        timeout_s=0.2,
        write_timeout_s=0.2,
        retries=1,
        serial_factory=lambda: device,
    )

    with client:
        hello = client.hello()
        assert hello["feature_flags"] & 0x80
        client.setup(
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

    assert device.last_setup is not None
    assert device.last_setup.speech_assist_control_interval_us == 500
    assert device.last_setup.speech_assist_release_accel_dhz_per_s == 32_000


def test_serial_client_open_waits_for_board_boot_before_flushing_input(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    sleep_calls: list[float] = []

    class _FakeSerial:
        def __init__(self) -> None:
            self.port = None
            self.baudrate = None
            self.timeout = None
            self.write_timeout = None
            self.dtr = None
            self.rts = None

        def open(self) -> None:
            events.append("open")

        def reset_input_buffer(self) -> None:
            events.append("reset_input_buffer")

        def close(self) -> None:
            events.append("close")

    monkeypatch.setattr(serial_client_module.serial, "Serial", _FakeSerial)
    monkeypatch.setattr(serial_client_module.time, "sleep", lambda seconds: sleep_calls.append(seconds))

    client = SerialClient(
        port="/dev/cu.usbserial-0001",
        baudrate=921600,
        timeout_s=0.2,
        write_timeout_s=0.2,
        retries=1,
    )

    client.open()
    client.close()

    assert client._OPEN_BOOT_SETTLE_S >= 1.5
    assert sleep_calls == [client._OPEN_BOOT_SETTLE_S]
    assert events == ["open", "reset_input_buffer", "close"]


def test_serial_client_open_falls_back_when_termios_tcsetattr_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    sleep_calls: list[float] = []
    open_calls: list[tuple[str, int]] = []
    pipe_calls = iter([(11, 12), (13, 14)])

    class _FakeSerial:
        def __init__(self) -> None:
            self.port = None
            self.baudrate = None
            self.timeout = None
            self.write_timeout = None
            self.dtr = None
            self.rts = None
            self._dsrdtr = False
            self._rtscts = False
            self.fd = None
            self.is_open = False

        def open(self) -> None:
            raise termios.error(errno.EINVAL, "Invalid argument")

        def _set_special_baudrate(self, baudrate: int) -> None:
            events.append(f"special_baud:{baudrate}")

        def _update_dtr_state(self) -> None:
            events.append("update_dtr")

        def _update_rts_state(self) -> None:
            events.append("update_rts")

        def reset_input_buffer(self) -> None:
            events.append("reset_input_buffer")

        def close(self) -> None:
            events.append("close")

    monkeypatch.setattr(serial_client_module.serial, "Serial", _FakeSerial)
    monkeypatch.setattr(serial_client_module.time, "sleep", lambda seconds: sleep_calls.append(seconds))
    monkeypatch.setattr(
        serial_client_module.os,
        "open",
        lambda port, flags: open_calls.append((port, flags)) or 10,
    )
    monkeypatch.setattr(serial_client_module.os, "pipe", lambda: next(pipe_calls))
    monkeypatch.setattr(serial_client_module.fcntl, "fcntl", lambda *_args: 0)

    client = SerialClient(
        port="/dev/cu.usbserial-0001",
        baudrate=921600,
        timeout_s=0.2,
        write_timeout_s=0.2,
        retries=1,
    )

    client.open()
    client.close()

    assert open_calls == [("/dev/cu.usbserial-0001", serial_client_module.os.O_RDWR | serial_client_module.os.O_NOCTTY | serial_client_module.os.O_NONBLOCK)]
    assert "special_baud:921600" in events
    assert "update_dtr" in events
    assert "update_rts" in events
    assert sleep_calls == [client._OPEN_BOOT_SETTLE_S]
    assert events[-2:] == ["reset_input_buffer", "close"]


def test_serial_client_step_motion_round_trip() -> None:
    device = SimulatedSerialDevice(queue_capacity=6)
    client = SerialClient(
        port="/dev/null",
        baudrate=921600,
        timeout_s=0.2,
        write_timeout_s=0.2,
        retries=1,
        serial_factory=lambda: device,
    )
    params = [
        StepMotionMotorParams(
            phases=(
                StepMotionPhase(
                    target_steps=1600,
                    peak_hz=200.0,
                    accel_hz_per_s=150.0,
                    decel_hz_per_s=150.0,
                    hold_ms=0,
                    direction=-1,
                ),
            ),
            start_delay_ms=0,
            trigger_motor=None,
            trigger_steps=0,
        )
    ]

    with client:
        client.hello()
        client.setup(
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
        client.step_motion(params)

    assert device.last_step_motion is not None
    assert len(device.last_step_motion) == 1
    assert device.last_step_motion[0].phases[0].target_steps == 1600
    assert device.last_step_motion[0].phases[0].direction == -1


def test_serial_client_stream_append_preserves_direction_flip_mask() -> None:
    device = SimulatedSerialDevice(queue_capacity=6)
    client = SerialClient(
        port="/dev/null",
        baudrate=921600,
        timeout_s=0.2,
        write_timeout_s=0.2,
        retries=1,
        serial_factory=lambda: device,
    )

    with client:
        hello = client.hello()
        assert hello["feature_flags"] & 0x10
        assert hello["feature_flags"] & 0x20
        client.stream_begin(total_segments=1, requested_credits=1)
        client.stream_append_event_group_batch(
            [
                _event_group(12_000, 220.0, flip_before_restart=True)
            ]
        )

    assert device.last_append_segments is not None
    assert device.last_append_segments[0].changes[0].flip_before_restart is True


def test_stream_song_status_retry_exhaustion_soft_fails_and_recovers() -> None:
    device = _FlakyStatusPollDevice(drop_status_writes=4, queue_capacity=6)
    client = SerialClient(
        port="/dev/null",
        baudrate=921600,
        timeout_s=0.02,
        write_timeout_s=0.02,
        retries=3,
        serial_factory=lambda: device,
    )
    event_groups = [
        _event_group(10_000, 100.0),
        _event_group(10_000, 105.0),
        _event_group(10_000, 110.0),
        _event_group(10_000, 115.0),
        _event_group(10_000, 120.0),
        _event_group(10_000, 125.0),
    ]

    with client:
        client.hello()
        client.setup(
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
        client.stream_song_and_play(
            event_groups,
            lookahead_ms=50,
            metrics_poll_interval_s=0.01,
        )

    assert device.received_segments == len(event_groups)
    assert client.status_soft_fail_count >= 1


def test_serial_client_emits_telemetry_without_blocking_stream() -> None:
    device = SimulatedSerialDevice(queue_capacity=6)
    client = SerialClient(
        port="/dev/null",
        baudrate=921600,
        timeout_s=0.2,
        write_timeout_s=0.2,
        retries=1,
        serial_factory=lambda: device,
    )

    event_groups = [
        _event_group(10_000, 100.0),
        _event_group(10_000, 110.0),
        _event_group(10_000, 120.0),
        _event_group(10_000, 130.0),
    ]
    telemetry_events: list[tuple[int, int, int | None]] = []

    def telemetry_cb(progress, status, metrics) -> None:
        telemetry_events.append(
            (
                progress.playhead_us,
                status.queue_depth,
                metrics.underrun_count if metrics is not None else None,
            )
        )
        # Callback failures must not break playback.
        raise RuntimeError("ignore telemetry callback failure")

    with client:
        client.hello()
        client.setup(
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
        client.stream_song_and_play(
            event_groups,
            lookahead_ms=50,
            telemetry_cb=telemetry_cb,
            metrics_poll_interval_s=0.01,
        )

    assert device.received_segments == len(event_groups)
    assert len(telemetry_events) > 0
    assert any(entry[2] is not None for entry in telemetry_events)


def test_serial_client_home_command_round_trip() -> None:
    device = SimulatedSerialDevice(queue_capacity=6, support_home=True)
    client = SerialClient(
        port="/dev/null",
        baudrate=921600,
        timeout_s=0.2,
        write_timeout_s=0.2,
        retries=1,
        serial_factory=lambda: device,
    )

    with client:
        hello = client.hello()
        assert hello["feature_flags"] & 0x02
        client.setup(
            motors=6,
            idle_mode="idle",
            min_note=21,
            max_note=108,
            transpose=0,
            playback_run_accel_hz_per_s=8000.0,
            playback_launch_start_hz=60.0,
            playback_launch_accel_hz_per_s=5000.0,
            playback_launch_crossover_hz=180.0,
        )
        client.home(steps_per_rev=800, home_hz=80.0, start_hz=60.0, accel_hz_per_s=200.0)

    assert device.last_home is not None
    assert device.last_home.steps_per_rev == 800
    assert device.last_home.start_freq_dhz == 600
    assert device.last_home.home_freq_dhz == 800
    assert device.last_home.accel_hz_per_s_dhz == 2000


def test_stream_append_does_not_retry_after_ambiguous_timeout() -> None:
    device = SimulatedSerialDevice(queue_capacity=6, drop_first_append_ack=True)
    client = SerialClient(
        port="/dev/null",
        baudrate=921600,
        timeout_s=0.02,
        write_timeout_s=0.02,
        retries=2,
        serial_factory=lambda: device,
    )
    event_groups = [_event_group(10_000, 100.0)]

    with client:
        client.hello()
        client.setup(
            motors=6,
            idle_mode="idle",
            min_note=21,
            max_note=108,
            transpose=0,
            playback_run_accel_hz_per_s=8000.0,
            playback_launch_start_hz=60.0,
            playback_launch_accel_hz_per_s=5000.0,
            playback_launch_crossover_hz=180.0,
        )
        client.stream_begin(total_segments=1, requested_credits=1)
        with pytest.raises(SerialClientError, match="ambiguous command outcome"):
            client.stream_append_event_group_batch(event_groups)

    assert device.received_segments == 1
    assert device.append_call_count == 1


class _StaticProgressDevice(SimulatedSerialDevice):
    def __init__(self, queue_capacity: int = 6) -> None:
        super().__init__(queue_capacity=queue_capacity)
        self._status_calls = 0

    def _status_payload(self) -> bytes:
        self._status_calls += 1
        if self._status_calls >= 8:
            self.playing = False
        state_flags = 0
        if self.playing:
            state_flags |= 0x01
        if not self.stream_end:
            state_flags |= 0x02
        if self.stream_end:
            state_flags |= 0x04
        payload = bytes([1, state_flags, 8, 0])
        payload += (0).to_bytes(2, "little")
        payload += self.queue_capacity.to_bytes(2, "little")
        payload += self.queue_capacity.to_bytes(2, "little")
        payload += bytes([1 if self.playing else 0, 0])
        payload += (0).to_bytes(4, "little")
        return payload


def test_stream_song_long_segment_does_not_false_stall(monkeypatch) -> None:
    class _FakeClock:
        def __init__(self) -> None:
            self.now = 0.0

        def monotonic(self) -> float:
            return self.now

        def sleep(self, seconds: float) -> None:
            self.now += max(seconds, 5.0)

    fake_clock = _FakeClock()
    monkeypatch.setattr(serial_client_module.time, "monotonic", fake_clock.monotonic)
    monkeypatch.setattr(serial_client_module.time, "sleep", fake_clock.sleep)

    device = _StaticProgressDevice(queue_capacity=6)
    client = SerialClient(
        port="/dev/null",
        baudrate=921600,
        timeout_s=0.05,
        write_timeout_s=0.05,
        retries=1,
        serial_factory=lambda: device,
    )
    event_groups = [_event_group(50_000_000, 100.0)]

    with client:
        client.hello()
        client.setup(
            motors=6,
            idle_mode="idle",
            min_note=21,
            max_note=108,
            transpose=0,
            playback_run_accel_hz_per_s=8000.0,
            playback_launch_start_hz=60.0,
            playback_launch_accel_hz_per_s=5000.0,
            playback_launch_crossover_hz=180.0,
        )
        client.stream_song_and_play(event_groups, lookahead_ms=50)


def test_stream_song_batches_event_groups_by_encoded_payload_size() -> None:
    device = SimulatedSerialDevice(queue_capacity=128)
    client = SerialClient(
        port="/dev/null",
        baudrate=921600,
        timeout_s=0.2,
        write_timeout_s=0.2,
        retries=1,
        serial_factory=lambda: device,
    )
    dense_change_set = tuple(
        PlaybackMotorChange(motor_idx=motor_idx, target_hz=110.0 + (motor_idx * 5.0), flip_before_restart=(motor_idx % 2 == 0))
        for motor_idx in range(6)
    )
    event_groups = [
        PlaybackEventGroup(delta_us=10_000, changes=dense_change_set)
        for _ in range(40)
    ]

    with client:
        client.hello()
        client.setup(
            motors=6,
            idle_mode="idle",
            min_note=21,
            max_note=108,
            transpose=0,
            playback_run_accel_hz_per_s=8000.0,
            playback_launch_start_hz=60.0,
            playback_launch_accel_hz_per_s=5000.0,
            playback_launch_crossover_hz=180.0,
        )
        client.stream_song_and_play(
            event_groups,
            lookahead_ms=50,
            metrics_poll_interval_s=0.01,
        )

    assert device.received_segments == len(event_groups)
    assert device.append_call_count >= 2


def test_target_queued_segments_uses_percentile_strategy() -> None:
    durations_us = [500, 500, 10_000]
    avg_target = SerialClient._target_queued_segments(
        lookahead_ms=200,
        durations_us=durations_us,
        lookahead_strategy="average",
        lookahead_min_ms=0,
        lookahead_percentile=90,
        lookahead_min_segments=1,
    )
    p95_target = SerialClient._target_queued_segments(
        lookahead_ms=200,
        durations_us=durations_us,
        lookahead_strategy="p95",
        lookahead_min_ms=0,
        lookahead_percentile=90,
        lookahead_min_segments=1,
    )
    assert p95_target <= avg_target


def test_target_queued_segments_honors_minimum_lookahead_ms() -> None:
    durations_us = [5_000]
    target = SerialClient._target_queued_segments(
        lookahead_ms=50,
        durations_us=durations_us,
        lookahead_strategy="average",
        lookahead_min_ms=250,
        lookahead_percentile=90,
        lookahead_min_segments=1,
    )
    assert target == 50


def test_target_queued_segments_honors_minimum_event_group_floor() -> None:
    durations_us = [500_000, 500_000]
    target = SerialClient._target_queued_segments(
        lookahead_ms=200,
        durations_us=durations_us,
        lookahead_strategy="p95",
        lookahead_min_ms=200,
        lookahead_percentile=95,
        lookahead_min_segments=24,
    )
    assert target == 24


def test_target_queued_segments_uses_short_tail_guard_for_bursty_segments() -> None:
    durations_us = [500, 600, 700, 80_000, 90_000, 100_000]
    target = SerialClient._target_queued_segments(
        lookahead_ms=200,
        durations_us=durations_us,
        lookahead_strategy="p95",
        lookahead_min_ms=200,
        lookahead_percentile=95,
        lookahead_min_segments=24,
    )
    assert target > 24

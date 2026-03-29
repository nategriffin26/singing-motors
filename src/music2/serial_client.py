from __future__ import annotations

from dataclasses import dataclass, replace
import errno
import fcntl
import math
import os
import sys
import termios
import time
from typing import TYPE_CHECKING, Callable, Protocol

import serial

from .models import (
    IdleMode,
    LookaheadStrategy,
    PlaybackEventGroup,
    PlaybackMetrics,
    PlaybackStartAnchor,
    Segment,
    StreamStatus,
)
from .protocol import (
    Ack,
    Command,
    MAX_EVENT_GROUPS_PER_APPEND,
    MAX_SEGMENTS_PER_APPEND,
    Packet,
    ProtocolError,
    StepMotionMotorParams,
    WarmupMotorParams,
    decode_frame,
    encode_home_payload,
    encode_frame,
    encode_hello_payload,
    encode_play_at_payload,
    encode_setup_payload,
    encode_stream_append_event_groups_payload,
    encode_stream_append_payload,
    encode_stream_begin_payload,
    encode_step_motion_payload,
    encode_warmup_payload,
    parse_ack,
    parse_err,
    parse_hello_ack,
    parse_metrics_payload,
    parse_status_payload,
)

if TYPE_CHECKING:
    from .playback_program import PlaybackPlan


class SerialClientError(Exception):
    pass


class AmbiguousCommandError(SerialClientError):
    def __init__(self, *, command: Command, attempts: int) -> None:
        super().__init__(
            f"ambiguous command outcome for {command.name} after {attempts} attempt(s); "
            "refusing automatic resend to avoid duplicate motor commands"
        )
        self.command = command
        self.attempts = attempts


class DeviceError(SerialClientError):
    def __init__(self, *, command: Command, code: int, message: str, credits: int | None, queue_depth: int | None) -> None:
        super().__init__(
            f"device error command=0x{int(command):02x} code={code}"
            + (f" message={message}" if message else "")
        )
        self.command = command
        self.code = code
        self.message = message
        self.credits = credits
        self.queue_depth = queue_depth


class SerialPortLike(Protocol):
    in_waiting: int

    def write(self, data: bytes) -> int:
        ...

    def read_until(self, expected: bytes = b"\n") -> bytes:
        ...

    def reset_input_buffer(self) -> None:
        ...

    def close(self) -> None:
        ...

    def flush(self) -> None:
        ...


@dataclass(frozen=True)
class StreamProgress:
    sent_segments: int
    total_segments: int
    queue_depth: int
    credits: int
    active_motors: int
    playhead_us: int


TelemetryCallback = Callable[[StreamProgress, StreamStatus, PlaybackMetrics | None], None]
StartAnchorCallback = Callable[[PlaybackStartAnchor], None]


@dataclass(frozen=True)
class ClockSyncEstimate:
    host_to_device_offset_us: int
    round_trip_us: int
    sample_count: int


class SerialClient:
    _OPEN_BOOT_SETTLE_S = 2.00
    _HOME_ACK_TIMEOUT_S = 600.0
    _IDEMPOTENT_COMMANDS = frozenset(
        {
            Command.HELLO,
            Command.STATUS,
            Command.METRICS,
            Command.STOP,
        }
    )

    def __init__(
        self,
        port: str,
        baudrate: int,
        timeout_s: float,
        write_timeout_s: float,
        retries: int,
        serial_factory: Callable[[], SerialPortLike] | None = None,
    ) -> None:
        self._port_name = port
        self._baudrate = baudrate
        self._timeout_s = timeout_s
        self._write_timeout_s = write_timeout_s
        self._retries = retries
        self._serial_factory = serial_factory
        self._serial: SerialPortLike | None = None
        self._seq = 1
        self._last_status_soft_fail_count = 0
        self._frame_version = 2

    def _should_use_termios_open_fallback(self, exc: BaseException, ser: object) -> bool:
        return (
            sys.platform == "darwin"
            and isinstance(exc, termios.error)
            and bool(exc.args)
            and exc.args[0] == errno.EINVAL
            and hasattr(ser, "_set_special_baudrate")
        )

    def _open_serial_with_termios_fallback(self, ser: object) -> None:
        fd: int | None = None
        pipe_abort_read_r: int | None = None
        pipe_abort_read_w: int | None = None
        pipe_abort_write_r: int | None = None
        pipe_abort_write_w: int | None = None

        try:
            fd = os.open(self._port_name, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
            setattr(ser, "fd", fd)
            pipe_abort_read_r, pipe_abort_read_w = os.pipe()
            pipe_abort_write_r, pipe_abort_write_w = os.pipe()
            fcntl.fcntl(pipe_abort_read_r, fcntl.F_SETFL, os.O_NONBLOCK)
            fcntl.fcntl(pipe_abort_write_r, fcntl.F_SETFL, os.O_NONBLOCK)
            setattr(ser, "pipe_abort_read_r", pipe_abort_read_r)
            setattr(ser, "pipe_abort_read_w", pipe_abort_read_w)
            setattr(ser, "pipe_abort_write_r", pipe_abort_write_r)
            setattr(ser, "pipe_abort_write_w", pipe_abort_write_w)
            setattr(ser, "is_open", True)
            getattr(ser, "_set_special_baudrate")(self._baudrate)
            try:
                if not getattr(ser, "_dsrdtr", False):
                    getattr(ser, "_update_dtr_state")()
                if not getattr(ser, "_rtscts", False):
                    getattr(ser, "_update_rts_state")()
            except OSError as exc:
                if exc.errno not in (errno.EINVAL, errno.ENOTTY):
                    raise
        except BaseException:
            if pipe_abort_read_w is not None:
                os.close(pipe_abort_read_w)
            if pipe_abort_read_r is not None:
                os.close(pipe_abort_read_r)
            if pipe_abort_write_w is not None:
                os.close(pipe_abort_write_w)
            if pipe_abort_write_r is not None:
                os.close(pipe_abort_write_r)
            if fd is not None:
                os.close(fd)
            setattr(ser, "fd", None)
            setattr(ser, "is_open", False)
            raise

    def __enter__(self) -> "SerialClient":
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def open(self) -> None:
        if self._serial is not None:
            return

        if self._serial_factory is not None:
            self._serial = self._serial_factory()
            return

        # Configure line levels before open so ESP32 auto-reset circuitry is not
        # accidentally held in reset/download mode on connect.
        ser = serial.Serial()
        ser.port = self._port_name
        ser.baudrate = self._baudrate
        ser.timeout = self._timeout_s
        ser.write_timeout = self._write_timeout_s
        ser.dtr = False
        ser.rts = False
        try:
            ser.open()
        except BaseException as exc:
            if not self._should_use_termios_open_fallback(exc, ser):
                raise
            self._open_serial_with_termios_fallback(ser)
        # USB-UART open often resets ESP32 boards; wait for boot chatter to
        # finish before protocol exchange.
        time.sleep(self._OPEN_BOOT_SETTLE_S)
        ser.reset_input_buffer()
        self._serial = ser

    def close(self) -> None:
        if self._serial is None:
            return
        self._serial.close()
        self._serial = None

    def _serial_port(self) -> SerialPortLike:
        if self._serial is None:
            raise SerialClientError("serial client is not open")
        return self._serial

    def _next_seq(self) -> int:
        seq = self._seq
        self._seq = (self._seq + 1) & 0xFFFF
        if self._seq == 0:
            self._seq = 1
        return seq

    @property
    def status_soft_fail_count(self) -> int:
        return self._last_status_soft_fail_count

    def _read_packet(self, *, deadline: float | None = None) -> Packet:
        ser = self._serial_port()
        while True:
            frame = ser.read_until(expected=b"\x00")
            if not frame:
                if deadline is not None and time.monotonic() >= deadline:
                    raise SerialClientError("serial timeout waiting for response")
                continue

            try:
                return decode_frame(frame)
            except ProtocolError:
                if deadline is not None and time.monotonic() >= deadline:
                    raise SerialClientError("serial timeout waiting for valid frame")
                continue

    def _send_and_wait(self, command: Command, payload: bytes = b"", *, timeout_s: float | None = None) -> Packet:
        ser = self._serial_port()
        attempt = 0
        retry_safe = command in self._IDEMPOTENT_COMMANDS
        effective_timeout_s = timeout_s if timeout_s is not None else max(1.0, self._timeout_s * 6.0)
        while True:
            attempt += 1
            seq = self._next_seq()
            write_succeeded = False
            try:
                frame = encode_frame(command, seq=seq, payload=payload, version=self._frame_version)
                ser.write(frame)
                ser.flush()
                write_succeeded = True

                read_deadline = time.monotonic() + effective_timeout_s
                while True:
                    packet = self._read_packet(deadline=read_deadline)
                    if packet.seq != seq:
                        continue
                    return packet

            except (SerialClientError, serial.SerialException) as exc:
                can_retry = attempt <= self._retries and (retry_safe or not write_succeeded)
                if can_retry:
                    time.sleep(0.02)
                    ser.reset_input_buffer()
                    continue
                if write_succeeded and not retry_safe:
                    raise AmbiguousCommandError(command=command, attempts=attempt) from exc
                raise SerialClientError(
                    f"request failed command={command.name} attempts={attempt}"
                ) from exc

    def _request_ack(self, command: Command, payload: bytes = b"", *, timeout_s: float | None = None) -> Ack:
        packet = self._send_and_wait(command, payload, timeout_s=timeout_s)

        if packet.command == Command.ERR:
            err = parse_err(packet.payload)
            raise DeviceError(
                command=err.for_command,
                code=err.error_code,
                message=err.message or "",
                credits=err.credits,
                queue_depth=err.queue_depth,
            )

        if packet.command != Command.ACK:
            raise SerialClientError(
                f"unexpected response for {command.name}: got {packet.command.name}"
            )

        ack = parse_ack(packet.payload)
        if ack.for_command != command:
            raise SerialClientError(
                f"ACK for wrong command expected={command.name} got={ack.for_command.name}"
            )
        return ack

    def hello(self, host_version: str = "music2-host/0.1.0") -> dict[str, int]:
        previous_version = self._frame_version
        self._frame_version = 2
        ack = self._request_ack(Command.HELLO, encode_hello_payload(host_version))
        info = parse_hello_ack(ack)
        if info is None:
            self._frame_version = previous_version
            return {}
        self._frame_version = 3 if int(info.protocol_version) >= 3 else 2
        return {
            "protocol_version": info.protocol_version,
            "motor_count": info.motor_count,
            "feature_flags": info.feature_flags,
            "queue_capacity": info.queue_capacity,
            "scheduler_tick_us": info.scheduler_tick_us,
            "playback_run_accel_dhz_per_s": info.playback_run_accel_dhz_per_s,
            "playback_accel_dhz_per_s": info.playback_accel_dhz_per_s,
            "playback_motor_count": info.playback_motor_count,
            "exact_motion_flags": info.exact_motion_flags,
            "credits": ack.credits or 0,
        }

    def setup(
        self,
        motors: int,
        idle_mode: IdleMode,
        min_note: int,
        max_note: int,
        transpose: int,
        *,
        playback_run_accel_hz_per_s: float | None = None,
        playback_launch_start_hz: float | None = None,
        playback_launch_accel_hz_per_s: float | None = None,
        playback_launch_crossover_hz: float | None = None,
        speech_assist_control_interval_us: int | None = None,
        speech_assist_release_accel_hz_per_s: float | None = None,
    ) -> Ack:
        payload = encode_setup_payload(
            motors=motors,
            idle_mode=idle_mode,
            min_note=min_note,
            max_note=max_note,
            transpose=transpose,
            playback_run_accel_hz_per_s=playback_run_accel_hz_per_s,
            playback_launch_start_hz=playback_launch_start_hz,
            playback_launch_accel_hz_per_s=playback_launch_accel_hz_per_s,
            playback_launch_crossover_hz=playback_launch_crossover_hz,
            speech_assist_control_interval_us=speech_assist_control_interval_us,
            speech_assist_release_accel_hz_per_s=speech_assist_release_accel_hz_per_s,
        )
        return self._request_ack(Command.SETUP, payload)

    def stream_begin(self, total_segments: int, requested_credits: int) -> Ack:
        payload = encode_stream_begin_payload(
            total_segments=total_segments,
            requested_credits=requested_credits,
        )
        return self._request_ack(Command.STREAM_BEGIN, payload)

    def stream_append_batch(self, segments: list[Segment]) -> Ack:
        payload = encode_stream_append_payload(segments)
        return self._request_ack(Command.STREAM_APPEND, payload)

    def stream_append_event_group_batch(self, event_groups: list[PlaybackEventGroup]) -> Ack:
        payload = encode_stream_append_event_groups_payload(event_groups)
        return self._request_ack(Command.STREAM_APPEND, payload)

    def stream_end(self) -> Ack:
        return self._request_ack(Command.STREAM_END)

    def play(self) -> Ack:
        return self._request_ack(Command.PLAY)

    def play_at(self, scheduled_start_device_us: int) -> int:
        ack = self._request_ack(Command.PLAY_AT, encode_play_at_payload(scheduled_start_device_us))
        if len(ack.extra) == 8:
            return int.from_bytes(ack.extra, byteorder="little", signed=False)
        return int(scheduled_start_device_us)

    def stop(self) -> Ack:
        return self._request_ack(Command.STOP)

    def home(
        self,
        *,
        steps_per_rev: int,
        home_hz: float,
        start_hz: float | None = None,
        accel_hz_per_s: float | None = None,
    ) -> Ack:
        payload = encode_home_payload(
            steps_per_rev=steps_per_rev,
            home_hz=home_hz,
            start_hz=start_hz,
            accel_hz_per_s=accel_hz_per_s,
        )
        return self._request_ack(Command.HOME, payload, timeout_s=self._HOME_ACK_TIMEOUT_S)

    def warmup(self, motor_params: list[WarmupMotorParams], *, timeout_s: float | None = None) -> Ack:
        """Execute a firmware-driven warmup using per-motor trapezoidal profiles.

        The command blocks until all motors have decelerated back to zero.
        ``timeout_s`` defaults to 60 s to accommodate the longest warmup sequences.
        """
        payload = encode_warmup_payload(motor_params)
        effective_timeout = timeout_s if timeout_s is not None else 60.0
        return self._request_ack(Command.WARMUP, payload, timeout_s=effective_timeout)

    def step_motion(self, motor_params: list[StepMotionMotorParams], *, timeout_s: float | None = None) -> Ack:
        """Execute step-targeted multi-phase motion profiles."""
        payload = encode_step_motion_payload(motor_params)
        effective_timeout = timeout_s if timeout_s is not None else 60.0
        return self._request_ack(Command.STEP_MOTION, payload, timeout_s=effective_timeout)

    def status(self, *, timeout_s: float | None = None) -> StreamStatus:
        packet = self._send_and_wait(Command.STATUS, timeout_s=timeout_s)
        if packet.command == Command.ERR:
            err = parse_err(packet.payload)
            raise DeviceError(
                command=err.for_command,
                code=err.error_code,
                message=err.message or "",
                credits=err.credits,
                queue_depth=err.queue_depth,
            )
        if packet.command != Command.STATUS:
            raise SerialClientError(f"unexpected STATUS response: {packet.command.name}")
        return parse_status_payload(packet.payload)

    def metrics(self, *, timeout_s: float | None = None) -> PlaybackMetrics:
        packet = self._send_and_wait(Command.METRICS, timeout_s=timeout_s)
        if packet.command == Command.ERR:
            err = parse_err(packet.payload)
            raise DeviceError(
                command=err.for_command,
                code=err.error_code,
                message=err.message or "",
                credits=err.credits,
                queue_depth=err.queue_depth,
            )
        if packet.command != Command.METRICS:
            raise SerialClientError(f"unexpected METRICS response: {packet.command.name}")
        return parse_metrics_payload(packet.payload)

    def estimate_device_clock(
        self,
        *,
        samples: int = 8,
        timeout_s: float | None = None,
    ) -> ClockSyncEstimate:
        effective_samples = max(1, int(samples))
        best_offset_us: int | None = None
        best_rtt_us: int | None = None
        for _ in range(effective_samples):
            host_send_mono = time.monotonic()
            status = self.status(timeout_s=timeout_s)
            host_recv_mono = time.monotonic()
            if status.device_time_us <= 0:
                continue
            midpoint_mono_us = int(round(((host_send_mono + host_recv_mono) * 0.5) * 1_000_000.0))
            offset_us = int(status.device_time_us) - midpoint_mono_us
            rtt_us = max(0, int(round((host_recv_mono - host_send_mono) * 1_000_000.0)))
            if best_rtt_us is None or rtt_us < best_rtt_us:
                best_offset_us = offset_us
                best_rtt_us = rtt_us
        if best_offset_us is None or best_rtt_us is None:
            raise SerialClientError("device clock sync failed: STATUS did not include device_time_us")
        return ClockSyncEstimate(
            host_to_device_offset_us=best_offset_us,
            round_trip_us=best_rtt_us,
            sample_count=effective_samples,
        )

    @staticmethod
    def _percentile(sorted_values: list[int], pct: int) -> float:
        if not sorted_values:
            return 0.0
        if len(sorted_values) == 1:
            return float(sorted_values[0])
        clamped_pct = max(0, min(100, pct))
        idx = (len(sorted_values) - 1) * (clamped_pct / 100.0)
        lower = int(math.floor(idx))
        upper = int(math.ceil(idx))
        if lower == upper:
            return float(sorted_values[lower])
        alpha = idx - lower
        return (1.0 - alpha) * sorted_values[lower] + alpha * sorted_values[upper]

    @classmethod
    def _target_queued_segments(
        cls,
        *,
        lookahead_ms: int,
        durations_us: list[int],
        lookahead_strategy: LookaheadStrategy,
        lookahead_min_ms: int,
        lookahead_percentile: int,
        lookahead_min_segments: int,
    ) -> int:
        if not durations_us:
            return 1
        durations_us = sorted(max(1, int(duration_us)) for duration_us in durations_us)
        avg_us = sum(durations_us) / len(durations_us)
        if lookahead_strategy == "average":
            characteristic_us = avg_us
        elif lookahead_strategy == "p95":
            characteristic_us = cls._percentile(durations_us, 95)
        elif lookahead_strategy == "p90":
            characteristic_us = cls._percentile(durations_us, 90)
        else:
            characteristic_us = cls._percentile(durations_us, lookahead_percentile)

        if characteristic_us <= 0:
            return 1
        effective_lookahead_ms = max(1, lookahead_ms, lookahead_min_ms)
        by_duration = max(1, int(round((effective_lookahead_ms * 1000.0) / characteristic_us)))
        # Guard against short-segment bursts that can drain credits much faster
        # than p90/p95 estimates suggest.
        short_window_ms = max(lookahead_min_ms, int(round(effective_lookahead_ms * 0.35)))
        p10_duration_us = max(1.0, cls._percentile(durations_us, 10))
        by_short_tail = max(1, int(math.ceil((short_window_ms * 1000.0) / p10_duration_us)))
        return max(by_duration, by_short_tail, max(1, int(lookahead_min_segments)))

    @staticmethod
    def _fit_event_group_batch_count(
        event_groups: list[PlaybackEventGroup],
        start_idx: int,
        max_count: int,
    ) -> int:
        max_count = max(0, min(max_count, len(event_groups) - start_idx))
        if max_count <= 0:
            return 0

        best_count = 0
        for count in range(1, max_count + 1):
            try:
                encode_stream_append_event_groups_payload(event_groups[start_idx : start_idx + count])
            except ValueError as exc:
                if "maximum protocol payload" in str(exc):
                    break
                raise
            best_count = count

        if best_count <= 0:
            raise SerialClientError(
                f"single event group at index {start_idx} exceeds maximum protocol payload"
            )
        return best_count

    def stream_song_and_play(
        self,
        event_groups: list[PlaybackEventGroup],
        *,
        lookahead_ms: int,
        lookahead_strategy: LookaheadStrategy = "p90",
        lookahead_min_ms: int = 250,
        lookahead_percentile: int = 90,
        lookahead_min_segments: int = 24,
        progress_cb: Callable[[StreamProgress], None] | None = None,
        telemetry_cb: TelemetryCallback | None = None,
        start_anchor_cb: StartAnchorCallback | None = None,
        metrics_poll_interval_s: float = 0.25,
        status_poll_interval_s: float = 0.02,
        scheduled_start_guard_ms: float = 150.0,
        clock_sync_samples: int = 8,
    ) -> None:
        if not event_groups:
            raise SerialClientError("refusing to stream empty song")
        self._last_status_soft_fail_count = 0

        segment_durations_us = sorted(max(1, int(group.delta_us)) for group in event_groups)
        short_tail_duration_us = max(1, int(round(self._percentile(segment_durations_us, 10))))
        target_segments = self._target_queued_segments(
            lookahead_ms=lookahead_ms,
            durations_us=segment_durations_us,
            lookahead_strategy=lookahead_strategy,
            lookahead_min_ms=lookahead_min_ms,
            lookahead_percentile=lookahead_percentile,
            lookahead_min_segments=lookahead_min_segments,
        )
        begin_ack = self.stream_begin(total_segments=len(event_groups), requested_credits=target_segments)
        if begin_ack.credits is not None and begin_ack.credits > 0:
            target_segments = min(target_segments, begin_ack.credits)

        credits = begin_ack.credits if begin_ack.credits is not None else target_segments
        queue_depth = max(0, begin_ack.queue_depth if begin_ack.queue_depth is not None else 0)
        queue_capacity = max(1, credits + queue_depth, target_segments)
        if credits <= 0:
            status = self.status(timeout_s=max(0.05, self._timeout_s))
            credits = status.credits
            queue_depth = max(0, status.queue_depth)
            queue_capacity = max(queue_capacity, status.queue_capacity)

        sent = 0

        # Prefill buffer prior to play.
        while sent < len(event_groups) and credits > 0:
            chunk_limit = min(
                credits,
                len(event_groups) - sent,
                MAX_EVENT_GROUPS_PER_APPEND,
                target_segments,
            )
            chunk_count = self._fit_event_group_batch_count(event_groups, sent, chunk_limit)
            try:
                ack = self.stream_append_event_group_batch(event_groups[sent : sent + chunk_count])
            except AmbiguousCommandError as exc:
                raise SerialClientError(
                    "stream append ack was lost after send; playback aborted to prevent duplicate steps"
                ) from exc
            sent += chunk_count
            queue_depth = (
                max(0, ack.queue_depth)
                if ack.queue_depth is not None
                else min(queue_capacity, queue_depth + chunk_count)
            )
            credits = ack.credits if ack.credits is not None else max(0, queue_capacity - queue_depth)

        last_status = StreamStatus(
            playing=False,
            stream_open=True,
            stream_end_received=False,
            motor_count=8,
            queue_depth=max(0, queue_depth),
            queue_capacity=max(queue_capacity, max(1, queue_depth + credits)),
            credits=max(0, credits),
            active_motors=0,
            playhead_us=0,
        )

        supports_scheduled_start = self._frame_version >= 3
        anchor_sent = False
        clock_sync: ClockSyncEstimate | None = None
        scheduled_start_device_us = 0
        scheduled_start_host_mono = 0.0
        if supports_scheduled_start:
            clock_sync = self.estimate_device_clock(
                samples=clock_sync_samples,
                timeout_s=max(0.05, self._timeout_s),
            )
            scheduled_start_host_mono = time.monotonic() + max(0.010, scheduled_start_guard_ms / 1000.0)
            scheduled_start_device_us = (
                int(round(scheduled_start_host_mono * 1_000_000.0))
                + clock_sync.host_to_device_offset_us
            )
            accepted_start_device_us = self.play_at(scheduled_start_device_us)
            if accepted_start_device_us > 0:
                scheduled_start_device_us = accepted_start_device_us
                scheduled_start_host_mono = (
                    (scheduled_start_device_us - clock_sync.host_to_device_offset_us) / 1_000_000.0
                )
            last_status = replace(
                last_status,
                playing=True,
                scheduled_start_device_us=scheduled_start_device_us,
            )
            if start_anchor_cb is not None:
                start_anchor_cb(
                    PlaybackStartAnchor(
                        scheduled_start_device_us=scheduled_start_device_us,
                        scheduled_start_host_mono=scheduled_start_host_mono,
                        scheduled_start_unix_ms=int(
                            round((time.time() - time.monotonic() + scheduled_start_host_mono) * 1000.0)
                        ),
                        host_to_device_offset_us=clock_sync.host_to_device_offset_us,
                        sync_rtt_us=clock_sync.round_trip_us,
                        strategy="scheduled_start_v1",
                    )
                )
                anchor_sent = True
        else:
            self.play()
            last_status = replace(last_status, playing=True)
        stream_end_sent = False
        playback_started_at = time.monotonic()
        total_duration_s = sum(group.delta_us for group in event_groups) / 1_000_000.0
        max_runtime_s = max(30.0, (total_duration_s * 2.5) + 5.0)
        last_metrics: PlaybackMetrics | None = None
        next_metrics_poll_at = time.monotonic()
        metrics_poll_interval_s = max(0.05, metrics_poll_interval_s)
        status_poll_interval_s = max(0.005, status_poll_interval_s)
        status_timeout_s = max(0.05, min(0.5, status_poll_interval_s * 6.0))
        metrics_timeout_s = max(0.1, min(0.8, metrics_poll_interval_s * 4.0))
        status_soft_fail_count = 0

        try:
            while True:
                status_error: Exception | None = None
                status_recovered_from_metrics = False
                try:
                    status = self.status(timeout_s=status_timeout_s)
                    last_status = status
                    if (not anchor_sent) and start_anchor_cb is not None and status.playhead_us > 0:
                        detected_host_mono = time.monotonic() - (status.playhead_us / 1_000_000.0)
                        start_anchor_cb(
                            PlaybackStartAnchor(
                                scheduled_start_device_us=int(status.scheduled_start_device_us),
                                scheduled_start_host_mono=detected_host_mono,
                                scheduled_start_unix_ms=int(
                                    round((time.time() - time.monotonic() + detected_host_mono) * 1000.0)
                                ),
                                host_to_device_offset_us=0,
                                sync_rtt_us=0,
                                strategy="legacy_poll_v1",
                            )
                        )
                        anchor_sent = True
                except (SerialClientError, DeviceError) as exc:
                    # STATUS loss can happen around aggressive passages; keep
                    # streaming from the last known state and recover on later polls.
                    status_error = exc
                    status_soft_fail_count += 1
                    status = last_status

                credits = max(0, status.credits)
                now = time.monotonic()
                if now >= next_metrics_poll_at or status_error is not None:
                    try:
                        last_metrics = self.metrics(timeout_s=metrics_timeout_s)
                        if status_error is not None:
                            status = replace(
                                status,
                                queue_depth=max(0, last_metrics.queue_depth),
                                credits=max(0, last_metrics.credits),
                            )
                            last_status = status
                            status_recovered_from_metrics = True
                    except (SerialClientError, DeviceError):
                        # Keep playback moving even when METRICS temporarily fails.
                        pass
                    next_metrics_poll_at = now + metrics_poll_interval_s

                credits = max(0, status.credits)
                progress = StreamProgress(
                    sent_segments=sent,
                    total_segments=len(event_groups),
                    queue_depth=status.queue_depth,
                    credits=status.credits,
                    active_motors=status.active_motors,
                    playhead_us=status.playhead_us,
                )

                if progress_cb is not None:
                    progress_cb(progress)

                if telemetry_cb is not None:
                    try:
                        telemetry_cb(progress, status, last_metrics)
                    except Exception:
                        # UI telemetry must never stall the serial stream loop.
                        pass

                # Keep queue depth near lookahead target while credits are available.
                if sent < len(event_groups) and credits > 0 and status.queue_depth < target_segments:
                    chunk_limit = min(
                        credits,
                        len(event_groups) - sent,
                        MAX_EVENT_GROUPS_PER_APPEND,
                        max(1, target_segments - status.queue_depth),
                    )
                    chunk_count = self._fit_event_group_batch_count(event_groups, sent, chunk_limit)
                    try:
                        ack = self.stream_append_event_group_batch(event_groups[sent : sent + chunk_count])
                    except DeviceError as exc:
                        if exc.command == Command.STREAM_APPEND and exc.code == 5:
                            time.sleep(0.003)
                            continue
                        raise
                    except AmbiguousCommandError as exc:
                        raise SerialClientError(
                            "stream append ack was lost after send; playback aborted to prevent duplicate steps"
                        ) from exc
                    sent += chunk_count
                    queue_depth = (
                        max(0, ack.queue_depth)
                        if ack.queue_depth is not None
                        else min(status.queue_capacity, status.queue_depth + chunk_count)
                    )
                    credits = ack.credits if ack.credits is not None else max(0, status.queue_capacity - queue_depth)
                    status = replace(status, queue_depth=queue_depth, credits=credits)
                    last_status = status

                if sent >= len(event_groups) and not stream_end_sent:
                    try:
                        self.stream_end()
                    except AmbiguousCommandError as exc:
                        raise SerialClientError(
                            "stream end ack was lost after send; session outcome is ambiguous"
                        ) from exc
                    stream_end_sent = True

                playback_likely_complete_without_status = (
                    stream_end_sent
                    and sent >= len(event_groups)
                    and status.queue_depth == 0
                    and status_error is not None
                    and status_recovered_from_metrics
                    and (now - playback_started_at) >= total_duration_s
                )
                if stream_end_sent and status.queue_depth == 0 and ((not status.playing) or playback_likely_complete_without_status):
                    break

                if now - playback_started_at > max_runtime_s:
                    raise SerialClientError(
                        f"playback exceeded safety runtime ({max_runtime_s:.1f}s); aborting stalled session"
                    )

                sleep_s = status_poll_interval_s
                if status.queue_depth > 0:
                    queue_coverage_s = (status.queue_depth * short_tail_duration_us) / 1_000_000.0
                    sleep_s = min(sleep_s, max(0.001, queue_coverage_s / 4.0))
                if status.queue_depth < max(2, target_segments // 4):
                    sleep_s = min(sleep_s, 0.003)
                time.sleep(max(0.001, sleep_s))
        finally:
            self._last_status_soft_fail_count = status_soft_fail_count

    def stream_playback_plan(
        self,
        playback_plan: "PlaybackPlan",
        *,
        lookahead_ms: int,
        lookahead_strategy: LookaheadStrategy = "p90",
        lookahead_min_ms: int = 250,
        lookahead_percentile: int = 90,
        lookahead_min_segments: int = 24,
        progress_cb: Callable[[StreamProgress], None] | None = None,
        telemetry_cb: TelemetryCallback | None = None,
        metrics_poll_interval_s: float = 0.25,
        status_poll_interval_s: float = 0.02,
        start_anchor_cb: StartAnchorCallback | None = None,
        scheduled_start_guard_ms: float = 150.0,
        clock_sync_samples: int = 8,
    ) -> None:
        self.stream_song_and_play(
            list(playback_plan.event_groups),
            lookahead_ms=lookahead_ms,
            lookahead_strategy=lookahead_strategy,
            lookahead_min_ms=lookahead_min_ms,
            lookahead_percentile=lookahead_percentile,
            lookahead_min_segments=lookahead_min_segments,
            progress_cb=progress_cb,
            telemetry_cb=telemetry_cb,
            start_anchor_cb=start_anchor_cb,
            metrics_poll_interval_s=metrics_poll_interval_s,
            status_poll_interval_s=status_poll_interval_s,
            scheduled_start_guard_ms=scheduled_start_guard_ms,
            clock_sync_samples=clock_sync_samples,
        )

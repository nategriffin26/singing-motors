from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
import struct

from .models import IdleMode, PlaybackEventGroup, PlaybackMetrics, PlaybackMotorChange, Segment, StreamStatus


class ProtocolError(Exception):
    pass


PROTOCOL_VERSION = 0x03
MIN_PROTOCOL_VERSION = 0x02
PROTOCOL_MAGIC = 0x4D32
HEADER_STRUCT = struct.Struct("<BBHHBH")
CRC_STRUCT = struct.Struct("<H")
HEADER_SIZE = HEADER_STRUCT.size
CRC_SIZE = CRC_STRUCT.size
MAX_PAYLOAD = 1024
SEGMENT_STRUCT = struct.Struct("<I8H")
SEGMENT_WITH_DIRECTION_STRUCT = struct.Struct("<IB8H")
MAX_SEGMENTS_PER_APPEND = (MAX_PAYLOAD - 1) // SEGMENT_WITH_DIRECTION_STRUCT.size
EVENT_GROUP_HEADER_STRUCT = struct.Struct("<IB")
EVENT_MOTOR_CHANGE_STRUCT = struct.Struct("<BHB")
MAX_EVENT_GROUPS_PER_APPEND = (MAX_PAYLOAD - 1) // (
    EVENT_GROUP_HEADER_STRUCT.size + EVENT_MOTOR_CHANGE_STRUCT.size
)
HELLO_ACK_LEGACY_EXTRA_STRUCT = struct.Struct("<BBBHH")
HELLO_ACK_PLAYBACK_V2_EXTRA_STRUCT = struct.Struct("<BBBHHI")
HELLO_ACK_CONTINUOUS_PLAYBACK_EXTRA_STRUCT = struct.Struct("<BBBHHIB")
HELLO_ACK_EXACT_MOTION_EXTRA_STRUCT = struct.Struct("<BBBHHIBB")
PLAY_AT_STRUCT = struct.Struct("<Q")
SETUP_BASE_STRUCT = struct.Struct("<BBBBb")
SETUP_PLAYBACK_PROFILE_STRUCT = struct.Struct("<IHIH")
SETUP_SPEECH_ASSIST_STRUCT = struct.Struct("<HI")
HOME_V1_STRUCT = struct.Struct("<HH")
HOME_V2_STRUCT = struct.Struct("<HHHH")
WARMUP_PHASE_STRUCT = struct.Struct("<HHHH")   # peak_dhz, accel_dhz_per_s, decel_dhz_per_s, hold_ms
WARMUP_MOTOR_HDR_STRUCT = struct.Struct("<HBHB")   # start_delay_ms, trigger_motor, trigger_steps, phase_count
WARMUP_NO_TRIGGER = 0xFF
WARMUP_MAX_PHASES = 4
STEP_MOTION_MAX_PHASES = 8
STEP_MOTION_PHASE_STRUCT = struct.Struct("<BHHHHH")  # flags, target_steps, peak_dhz, accel_dhz_per_s, decel_dhz_per_s, hold_ms

FEATURE_FLAG_TIMED_STREAMING = 0x01
FEATURE_FLAG_PLAYBACK_EVENT_STREAMING = FEATURE_FLAG_TIMED_STREAMING
FEATURE_FLAG_HOME = 0x02
FEATURE_FLAG_WARMUP = 0x04
FEATURE_FLAG_STEP_MOTION = 0x08
FEATURE_FLAG_DIRECTION_FLIP = 0x10
FEATURE_FLAG_CONTINUOUS_PLAYBACK_ENGINE = 0x20
FEATURE_FLAG_PLAYBACK_SETUP_PROFILE = 0x40
FEATURE_FLAG_SPEECH_ASSIST = 0x80
EVENT_CHANGE_FLAG_FLIP_BEFORE_RESTART = 0x01
EXACT_MOTION_FEATURE_DIRECTIONAL_STEP_MOTION = 0x01
STEP_MOTION_PHASE_FLAG_REVERSE = 0x01


class Command(IntEnum):
    HELLO = 0x01
    SETUP = 0x02
    STREAM_BEGIN = 0x03
    STREAM_APPEND = 0x04
    STREAM_END = 0x05
    PLAY = 0x06
    STOP = 0x07
    STATUS = 0x08
    METRICS = 0x09
    HOME = 0x0A
    WARMUP = 0x0B
    STEP_MOTION = 0x0C
    PLAY_AT = 0x0D
    ACK = 0x7E
    ERR = 0x7F


@dataclass(frozen=True)
class Packet:
    version: int
    command: Command
    seq: int
    flags: int
    payload: bytes


@dataclass(frozen=True)
class Ack:
    for_command: Command
    credits: int | None
    queue_depth: int | None
    extra: bytes


@dataclass(frozen=True)
class ErrPacket:
    for_command: Command
    error_code: int
    credits: int | None
    queue_depth: int | None
    message: str | None


@dataclass(frozen=True)
class SetupPayload:
    motors: int
    idle_mode: IdleMode
    min_note: int
    max_note: int
    transpose: int
    playback_run_accel_dhz_per_s: int | None = None
    playback_launch_start_dhz: int | None = None
    playback_launch_accel_dhz_per_s: int | None = None
    playback_launch_crossover_dhz: int | None = None
    speech_assist_control_interval_us: int | None = None
    speech_assist_release_accel_dhz_per_s: int | None = None


@dataclass(frozen=True)
class StreamBeginPayload:
    total_segments: int
    requested_credits: int


@dataclass(frozen=True)
class HomePayload:
    steps_per_rev: int
    start_freq_dhz: int
    home_freq_dhz: int
    accel_hz_per_s_dhz: int


@dataclass(frozen=True)
class WarmupPhase:
    """One trapezoidal velocity phase: accel → hold → decel → 0.

    After this phase the motor is at rest and the next phase (if any) begins.
    Set ``peak_hz=0`` for a silent idle phase (only ``hold_ms`` has effect).
    """

    peak_hz: float        # target frequency (Hz); 0 = silent/idle phase
    accel_hz_per_s: float # ramp-up rate (Hz/s); 0 = instant snap to peak
    decel_hz_per_s: float # ramp-down rate (Hz/s); must be > 0 when peak_hz > 0
    hold_ms: int          # ms to hold at peak before decelerating


@dataclass(frozen=True)
class WarmupMotorParams:
    """Per-motor multi-phase warmup profile for the WARMUP command.

    Each motor executes ``phases`` sequentially.  A motor can be given up to
    4 phases — useful for two-stage profiles (e.g. chaos spin then convergence).

    ``start_delay_ms`` delays the start of phase 0 by this many ms from
    command receipt (wall-clock).

    ``trigger_motor`` and ``trigger_steps``: if ``trigger_motor`` is not None,
    this motor's phase 0 additionally waits until the referenced motor has
    accumulated ``trigger_steps`` steps from when *that* motor began its own
    phase 0.  This enables position-cascade effects like the domino ripple
    (trigger when the previous motor passes the 180° mark).

    Set all phases to ``peak_hz=0`` to leave a motor idle.
    """

    phases: tuple[WarmupPhase, ...]  # 1–4 phases executed sequentially
    start_delay_ms: int = 0          # ms delay before phase 0 starts
    trigger_motor: int | None = None # motor index to watch, or None
    trigger_steps: int = 0           # relative step threshold on trigger_motor


@dataclass(frozen=True)
class StepMotionPhase:
    """One step-targeted phase with trapezoidal speed constraints.

    ``target_steps`` is the desired step count for the phase on the active
    motor.  ``hold_ms`` is an optional minimum hold duration at peak speed
    before deceleration can begin. ``direction`` is `1` for forward and `-1`
    for reverse.
    """

    target_steps: int
    peak_hz: float
    accel_hz_per_s: float
    decel_hz_per_s: float
    hold_ms: int = 0
    direction: int = 1


@dataclass(frozen=True)
class StepMotionMotorParams:
    """Per-motor multi-phase step-targeted motion profile."""

    phases: tuple[StepMotionPhase, ...]
    start_delay_ms: int = 0
    trigger_motor: int | None = None
    trigger_steps: int = 0


@dataclass(frozen=True)
class HelloInfo:
    protocol_version: int
    motor_count: int
    feature_flags: int
    queue_capacity: int
    scheduler_tick_us: int
    playback_run_accel_dhz_per_s: int = 0
    playback_motor_count: int = 0
    exact_motion_flags: int = 0

    @property
    def playback_accel_dhz_per_s(self) -> int:
        return self.playback_run_accel_dhz_per_s

    @property
    def exact_direction_step_motion_supported(self) -> bool:
        return bool(self.exact_motion_flags & EXACT_MOTION_FEATURE_DIRECTIONAL_STEP_MOTION)


class IdleModeCode(IntEnum):
    IDLE = 0
    DUPLICATE = 1


def _command_from_byte(value: int, *, field_name: str) -> Command:
    try:
        return Command(value)
    except ValueError as exc:
        raise ProtocolError(f"unknown {field_name}: 0x{value:02x}") from exc


def crc16_ccitt(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


def cobs_encode(data: bytes) -> bytes:
    if not data:
        return b"\x01"

    out = bytearray()
    code_index = 0
    out.append(0)
    code = 1

    for byte in data:
        if byte == 0:
            out[code_index] = code
            code_index = len(out)
            out.append(0)
            code = 1
            continue

        out.append(byte)
        code += 1
        if code == 0xFF:
            out[code_index] = code
            code_index = len(out)
            out.append(0)
            code = 1

    out[code_index] = code
    return bytes(out)


def cobs_decode(data: bytes) -> bytes:
    if not data:
        raise ProtocolError("empty COBS payload")

    out = bytearray()
    idx = 0
    size = len(data)

    while idx < size:
        code = data[idx]
        if code == 0:
            raise ProtocolError("invalid COBS code 0")
        idx += 1

        read_end = idx + code - 1
        if read_end > size:
            raise ProtocolError("COBS payload overrun")

        out.extend(data[idx:read_end])
        idx = read_end
        if code < 0xFF and idx < size:
            out.append(0)

    return bytes(out)


def encode_frame(
    command: Command,
    seq: int,
    payload: bytes = b"",
    flags: int = 0,
    *,
    version: int = PROTOCOL_VERSION,
) -> bytes:
    if len(payload) > MAX_PAYLOAD:
        raise ValueError(f"payload too large: {len(payload)} > {MAX_PAYLOAD}")
    header = HEADER_STRUCT.pack(
        version,
        int(command),
        PROTOCOL_MAGIC,
        seq & 0xFFFF,
        flags & 0xFF,
        len(payload),
    )
    body = header + payload
    crc = CRC_STRUCT.pack(crc16_ccitt(body))
    return cobs_encode(body + crc) + b"\x00"


def decode_frame(frame: bytes) -> Packet:
    if not frame:
        raise ProtocolError("empty frame")
    if frame[-1] == 0:
        frame = frame[:-1]

    raw = cobs_decode(frame)
    if len(raw) < HEADER_SIZE + CRC_SIZE:
        raise ProtocolError("frame too short")

    body = raw[:-CRC_SIZE]
    received_crc = CRC_STRUCT.unpack(raw[-CRC_SIZE:])[0]
    computed_crc = crc16_ccitt(body)
    if received_crc != computed_crc:
        raise ProtocolError("CRC mismatch")

    version, cmd, magic, seq, flags, payload_len = HEADER_STRUCT.unpack(body[:HEADER_SIZE])
    if version < MIN_PROTOCOL_VERSION or version > PROTOCOL_VERSION:
        raise ProtocolError(
            f"protocol version mismatch: expected [{MIN_PROTOCOL_VERSION}, {PROTOCOL_VERSION}] got {version}"
        )
    if magic != PROTOCOL_MAGIC:
        raise ProtocolError("magic mismatch")
    if payload_len != len(body) - HEADER_SIZE:
        raise ProtocolError("payload length mismatch")

    payload = body[HEADER_SIZE:]
    return Packet(
        version=version,
        command=_command_from_byte(cmd, field_name="frame command"),
        seq=seq,
        flags=flags,
        payload=payload,
    )


def parse_ack(payload: bytes) -> Ack:
    if len(payload) < 2:
        raise ProtocolError("ACK payload too short")
    for_command = _command_from_byte(payload[0], field_name="ACK for_command")
    flags = payload[1]
    credits: int | None = None
    queue_depth: int | None = None

    # Firmware always includes credits/depth for operational ACKs.
    if len(payload) >= 6:
        credits = struct.unpack("<H", payload[2:4])[0]
        queue_depth = struct.unpack("<H", payload[4:6])[0]
        extra = payload[6:]
    elif flags & 0x01:
        if len(payload) < 4:
            raise ProtocolError("ACK missing credits")
        credits = struct.unpack("<H", payload[2:4])[0]
        extra = payload[4:]
    else:
        extra = payload[2:]

    return Ack(for_command=for_command, credits=credits, queue_depth=queue_depth, extra=extra)


def parse_err(payload: bytes) -> ErrPacket:
    if len(payload) < 2:
        raise ProtocolError("ERR payload too short")

    for_command = _command_from_byte(payload[0], field_name="ERR for_command")
    error_code = int(payload[1])
    credits: int | None = None
    queue_depth: int | None = None
    message: str | None = None

    if len(payload) >= 6:
        credits = struct.unpack("<H", payload[2:4])[0]
        queue_depth = struct.unpack("<H", payload[4:6])[0]
        if len(payload) > 6:
            message = payload[6:].decode("utf-8", errors="replace")
    elif len(payload) > 2:
        message = payload[2:].decode("utf-8", errors="replace")

    return ErrPacket(
        for_command=for_command,
        error_code=error_code,
        credits=credits,
        queue_depth=queue_depth,
        message=message,
    )


def encode_hello_payload(host_version: str) -> bytes:
    encoded = host_version.encode("utf-8")
    if len(encoded) > 255:
        raise ValueError("host_version too long")
    return bytes([len(encoded)]) + encoded


def parse_hello_ack(ack: Ack) -> HelloInfo | None:
    if len(ack.extra) < HELLO_ACK_LEGACY_EXTRA_STRUCT.size:
        return None
    exact_motion_flags = 0
    if len(ack.extra) == HELLO_ACK_LEGACY_EXTRA_STRUCT.size:
        (
            protocol_version,
            motor_count,
            feature_flags,
            queue_capacity,
            scheduler_tick_us,
        ) = HELLO_ACK_LEGACY_EXTRA_STRUCT.unpack(ack.extra)
        playback_run_accel_dhz_per_s = 0
        playback_motor_count = motor_count
    elif len(ack.extra) == HELLO_ACK_PLAYBACK_V2_EXTRA_STRUCT.size:
        (
            protocol_version,
            motor_count,
            feature_flags,
            queue_capacity,
            scheduler_tick_us,
            playback_run_accel_dhz_per_s,
        ) = HELLO_ACK_PLAYBACK_V2_EXTRA_STRUCT.unpack(ack.extra)
        playback_motor_count = motor_count
    elif len(ack.extra) == HELLO_ACK_CONTINUOUS_PLAYBACK_EXTRA_STRUCT.size:
        (
            protocol_version,
            motor_count,
            feature_flags,
            queue_capacity,
            scheduler_tick_us,
            playback_run_accel_dhz_per_s,
            playback_motor_count,
        ) = HELLO_ACK_CONTINUOUS_PLAYBACK_EXTRA_STRUCT.unpack(ack.extra)
    elif len(ack.extra) == HELLO_ACK_EXACT_MOTION_EXTRA_STRUCT.size:
        (
            protocol_version,
            motor_count,
            feature_flags,
            queue_capacity,
            scheduler_tick_us,
            playback_run_accel_dhz_per_s,
            playback_motor_count,
            exact_motion_flags,
        ) = HELLO_ACK_EXACT_MOTION_EXTRA_STRUCT.unpack(ack.extra)
    else:
        raise ProtocolError("HELLO ACK extra size mismatch")
    return HelloInfo(
        protocol_version=protocol_version,
        motor_count=motor_count,
        feature_flags=feature_flags,
        queue_capacity=queue_capacity,
        scheduler_tick_us=scheduler_tick_us,
        playback_run_accel_dhz_per_s=playback_run_accel_dhz_per_s,
        playback_motor_count=playback_motor_count,
        exact_motion_flags=exact_motion_flags,
    )


def _idle_mode_to_code(idle_mode: IdleMode) -> IdleModeCode:
    return IdleModeCode.DUPLICATE if idle_mode == "duplicate" else IdleModeCode.IDLE


def _code_to_idle_mode(code: int) -> IdleMode:
    if code == IdleModeCode.DUPLICATE:
        return "duplicate"
    if code == IdleModeCode.IDLE:
        return "idle"
    raise ProtocolError(f"unknown idle mode code: {code}")


def encode_setup_payload(
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
) -> bytes:
    if not (0 <= motors <= 255):
        raise ValueError("motors out of range")
    payload = bytearray(
        SETUP_BASE_STRUCT.pack(
        motors,
        int(_idle_mode_to_code(idle_mode)),
        min_note & 0xFF,
        max_note & 0xFF,
        transpose,
        )
    )
    profile_values = (
        playback_run_accel_hz_per_s,
        playback_launch_start_hz,
        playback_launch_accel_hz_per_s,
        playback_launch_crossover_hz,
    )
    if any(value is not None for value in profile_values):
        if any(value is None for value in profile_values):
            raise ValueError("playback setup profile requires all four playback values")
        payload.extend(
            SETUP_PLAYBACK_PROFILE_STRUCT.pack(
                _freq_to_u32_dhz(playback_run_accel_hz_per_s or 0.0),
                _freq_to_dhz(playback_launch_start_hz or 0.0),
                _freq_to_u32_dhz(playback_launch_accel_hz_per_s or 0.0),
                _freq_to_dhz(playback_launch_crossover_hz or 0.0),
            )
        )
    speech_values = (
        speech_assist_control_interval_us,
        speech_assist_release_accel_hz_per_s,
    )
    if any(value is not None for value in speech_values):
        if len(payload) == SETUP_BASE_STRUCT.size:
            raise ValueError("speech assist setup requires the playback setup profile values")
        if any(value is None for value in speech_values):
            raise ValueError("speech assist setup requires both speech assist values")
        if speech_assist_control_interval_us is None or speech_assist_control_interval_us <= 0:
            raise ValueError("speech_assist_control_interval_us must be > 0")
        payload.extend(
            SETUP_SPEECH_ASSIST_STRUCT.pack(
                int(speech_assist_control_interval_us),
                _freq_to_u32_dhz(speech_assist_release_accel_hz_per_s or 0.0),
            )
        )
    return bytes(payload)


def decode_setup_payload(payload: bytes) -> SetupPayload:
    valid_lengths = {
        SETUP_BASE_STRUCT.size,
        SETUP_BASE_STRUCT.size + SETUP_PLAYBACK_PROFILE_STRUCT.size,
        SETUP_BASE_STRUCT.size + SETUP_PLAYBACK_PROFILE_STRUCT.size + SETUP_SPEECH_ASSIST_STRUCT.size,
    }
    if len(payload) not in valid_lengths:
        raise ProtocolError("SETUP payload size mismatch")
    motors, idle_mode_code, min_note, max_note, transpose = SETUP_BASE_STRUCT.unpack(
        payload[: SETUP_BASE_STRUCT.size]
    )
    playback_run_accel_dhz_per_s: int | None = None
    playback_launch_start_dhz: int | None = None
    playback_launch_accel_dhz_per_s: int | None = None
    playback_launch_crossover_dhz: int | None = None
    speech_assist_control_interval_us: int | None = None
    speech_assist_release_accel_dhz_per_s: int | None = None
    if len(payload) > SETUP_BASE_STRUCT.size:
        profile_offset = SETUP_BASE_STRUCT.size
        (
            playback_run_accel_dhz_per_s,
            playback_launch_start_dhz,
            playback_launch_accel_dhz_per_s,
            playback_launch_crossover_dhz,
        ) = SETUP_PLAYBACK_PROFILE_STRUCT.unpack(payload[profile_offset : profile_offset + SETUP_PLAYBACK_PROFILE_STRUCT.size])
    if len(payload) == (SETUP_BASE_STRUCT.size + SETUP_PLAYBACK_PROFILE_STRUCT.size + SETUP_SPEECH_ASSIST_STRUCT.size):
        speech_offset = SETUP_BASE_STRUCT.size + SETUP_PLAYBACK_PROFILE_STRUCT.size
        (
            speech_assist_control_interval_us,
            speech_assist_release_accel_dhz_per_s,
        ) = SETUP_SPEECH_ASSIST_STRUCT.unpack(payload[speech_offset:])
    return SetupPayload(
        motors=motors,
        idle_mode=_code_to_idle_mode(idle_mode_code),
        min_note=min_note,
        max_note=max_note,
        transpose=transpose,
        playback_run_accel_dhz_per_s=playback_run_accel_dhz_per_s,
        playback_launch_start_dhz=playback_launch_start_dhz,
        playback_launch_accel_dhz_per_s=playback_launch_accel_dhz_per_s,
        playback_launch_crossover_dhz=playback_launch_crossover_dhz,
        speech_assist_control_interval_us=speech_assist_control_interval_us,
        speech_assist_release_accel_dhz_per_s=speech_assist_release_accel_dhz_per_s,
    )


def encode_stream_begin_payload(total_segments: int, requested_credits: int) -> bytes:
    return struct.pack("<IH", total_segments, requested_credits)


def decode_stream_begin_payload(payload: bytes) -> StreamBeginPayload:
    if len(payload) != 6:
        raise ProtocolError("STREAM_BEGIN payload size mismatch")
    total, credits = struct.unpack("<IH", payload)
    return StreamBeginPayload(total_segments=total, requested_credits=credits)


def encode_play_at_payload(scheduled_start_device_us: int) -> bytes:
    if scheduled_start_device_us < 0:
        raise ValueError("scheduled_start_device_us must be >= 0")
    return PLAY_AT_STRUCT.pack(int(scheduled_start_device_us))


def decode_play_at_payload(payload: bytes) -> int:
    if len(payload) != PLAY_AT_STRUCT.size:
        raise ProtocolError("PLAY_AT payload size mismatch")
    return int(PLAY_AT_STRUCT.unpack(payload)[0])


def encode_home_payload(
    *,
    steps_per_rev: int,
    home_hz: float,
    start_hz: float | None = None,
    accel_hz_per_s: float | None = None,
) -> bytes:
    if not (1 <= steps_per_rev <= 0xFFFF):
        raise ValueError("steps_per_rev must be in range [1, 65535]")
    home_freq_dhz = _freq_to_dhz(home_hz)
    start_freq_dhz = _freq_to_dhz(start_hz if start_hz is not None else home_hz)
    accel_hz_per_s_dhz = _freq_to_dhz(accel_hz_per_s if accel_hz_per_s is not None else 0.0)
    if home_freq_dhz <= 0:
        raise ValueError("home_hz must be > 0")
    if start_freq_dhz <= 0:
        raise ValueError("start_hz must be > 0")
    if start_freq_dhz > home_freq_dhz:
        raise ValueError("start_hz must be <= home_hz")
    return HOME_V2_STRUCT.pack(steps_per_rev, start_freq_dhz, home_freq_dhz, accel_hz_per_s_dhz)


def decode_home_payload(payload: bytes) -> HomePayload:
    if len(payload) == HOME_V1_STRUCT.size:
        steps_per_rev, home_freq_dhz = HOME_V1_STRUCT.unpack(payload)
        return HomePayload(
            steps_per_rev=steps_per_rev,
            start_freq_dhz=home_freq_dhz,
            home_freq_dhz=home_freq_dhz,
            accel_hz_per_s_dhz=0,
        )
    if len(payload) == HOME_V2_STRUCT.size:
        steps_per_rev, start_freq_dhz, home_freq_dhz, accel_hz_per_s_dhz = HOME_V2_STRUCT.unpack(payload)
        return HomePayload(
            steps_per_rev=steps_per_rev,
            start_freq_dhz=start_freq_dhz,
            home_freq_dhz=home_freq_dhz,
            accel_hz_per_s_dhz=accel_hz_per_s_dhz,
        )
    if len(payload) not in {HOME_V1_STRUCT.size, HOME_V2_STRUCT.size}:
        raise ProtocolError("HOME payload size mismatch")
    raise ProtocolError("HOME payload size mismatch")


def encode_warmup_payload(motor_params: list[WarmupMotorParams]) -> bytes:
    """Encode a list of per-motor multi-phase profiles into a WARMUP payload.

    ``motor_params`` must have 1–8 entries (one per physical motor slot, in
    motor index order).  Each entry may have 1–4 phases.
    """
    if not motor_params:
        raise ValueError("motor_params cannot be empty")
    if len(motor_params) > 8:
        raise ValueError(f"too many motor params: {len(motor_params)} (max 8)")

    encoded = bytearray([len(motor_params) & 0xFF])
    for i, p in enumerate(motor_params):
        if not p.phases:
            raise ValueError(f"motor {i}: phases cannot be empty")
        if len(p.phases) > STEP_MOTION_MAX_PHASES:
            raise ValueError(f"motor {i}: too many phases ({len(p.phases)} > {STEP_MOTION_MAX_PHASES})")
        if not (0 <= p.start_delay_ms <= 0xFFFF):
            raise ValueError(f"motor {i}: start_delay_ms out of range: {p.start_delay_ms}")
        if p.trigger_motor is not None and not (0 <= p.trigger_motor <= 7):
            raise ValueError(f"motor {i}: trigger_motor out of range: {p.trigger_motor}")
        if not (0 <= p.trigger_steps <= 0xFFFF):
            raise ValueError(f"motor {i}: trigger_steps out of range: {p.trigger_steps}")

        trigger_byte = WARMUP_NO_TRIGGER if p.trigger_motor is None else (p.trigger_motor & 0xFF)
        # Header: start_delay_ms (H), trigger_motor (B), trigger_steps (H), phase_count (B)
        encoded.extend(
            WARMUP_MOTOR_HDR_STRUCT.pack(
                p.start_delay_ms & 0xFFFF,
                trigger_byte,
                p.trigger_steps & 0xFFFF,
                len(p.phases) & 0xFF,
            )
        )
        for j, ph in enumerate(p.phases):
            peak_dhz = _freq_to_dhz(ph.peak_hz)
            accel_dhz_per_s = _freq_to_dhz(ph.accel_hz_per_s)
            decel_dhz_per_s = _freq_to_dhz(ph.decel_hz_per_s)
            if ph.peak_hz > 0 and decel_dhz_per_s == 0:
                raise ValueError(
                    f"motor {i} phase {j}: decel_hz_per_s must be > 0 when peak_hz > 0"
                )
            if not (0 <= ph.hold_ms <= 0xFFFF):
                raise ValueError(f"motor {i} phase {j}: hold_ms out of range: {ph.hold_ms}")
            encoded.extend(
                WARMUP_PHASE_STRUCT.pack(
                    peak_dhz,
                    accel_dhz_per_s,
                    decel_dhz_per_s,
                    ph.hold_ms & 0xFFFF,
                )
            )
    return bytes(encoded)


def decode_warmup_payload(payload: bytes) -> list[WarmupMotorParams]:
    """Decode a WARMUP payload back into per-motor parameter objects."""
    if len(payload) < 1:
        raise ProtocolError("WARMUP payload too short")
    motor_count = payload[0]
    if motor_count == 0:
        raise ProtocolError("WARMUP motor_count must be > 0")
    if motor_count > 8:
        raise ProtocolError("WARMUP motor_count must be <= 8")

    result: list[WarmupMotorParams] = []
    cursor = 1
    hdr_size = WARMUP_MOTOR_HDR_STRUCT.size
    phase_size = WARMUP_PHASE_STRUCT.size

    for motor_idx in range(motor_count):
        if cursor + hdr_size > len(payload):
            raise ProtocolError(f"WARMUP payload truncated at motor {motor_idx} header")
        start_delay_ms, trigger_byte, trigger_steps, phase_count = (
            WARMUP_MOTOR_HDR_STRUCT.unpack(payload[cursor : cursor + hdr_size])
        )
        cursor += hdr_size

        if phase_count == 0 or phase_count > WARMUP_MAX_PHASES:
            raise ProtocolError(f"WARMUP motor {motor_idx}: invalid phase_count {phase_count}")

        trigger_motor: int | None = None if trigger_byte == WARMUP_NO_TRIGGER else int(trigger_byte)

        phases: list[WarmupPhase] = []
        for ph_idx in range(phase_count):
            if cursor + phase_size > len(payload):
                raise ProtocolError(
                    f"WARMUP payload truncated at motor {motor_idx} phase {ph_idx}"
                )
            peak_dhz, accel_dhz_per_s, decel_dhz_per_s, hold_ms = (
                WARMUP_PHASE_STRUCT.unpack(payload[cursor : cursor + phase_size])
            )
            cursor += phase_size
            phases.append(
                WarmupPhase(
                    peak_hz=peak_dhz / 10.0,
                    accel_hz_per_s=accel_dhz_per_s / 10.0,
                    decel_hz_per_s=decel_dhz_per_s / 10.0,
                    hold_ms=hold_ms,
                )
            )

        result.append(
            WarmupMotorParams(
                phases=tuple(phases),
                start_delay_ms=start_delay_ms,
                trigger_motor=trigger_motor,
                trigger_steps=trigger_steps,
            )
        )
    return result


def encode_step_motion_payload(motor_params: list[StepMotionMotorParams]) -> bytes:
    if not motor_params:
        raise ValueError("motor_params cannot be empty")
    if len(motor_params) > 8:
        raise ValueError(f"too many motor params: {len(motor_params)} (max 8)")

    encoded = bytearray([len(motor_params) & 0xFF])
    for i, p in enumerate(motor_params):
        if not p.phases:
            raise ValueError(f"motor {i}: phases cannot be empty")
        if len(p.phases) > WARMUP_MAX_PHASES:
            raise ValueError(f"motor {i}: too many phases ({len(p.phases)} > {WARMUP_MAX_PHASES})")
        if not (0 <= p.start_delay_ms <= 0xFFFF):
            raise ValueError(f"motor {i}: start_delay_ms out of range: {p.start_delay_ms}")
        if p.trigger_motor is not None and not (0 <= p.trigger_motor <= 7):
            raise ValueError(f"motor {i}: trigger_motor out of range: {p.trigger_motor}")
        if not (0 <= p.trigger_steps <= 0xFFFF):
            raise ValueError(f"motor {i}: trigger_steps out of range: {p.trigger_steps}")

        trigger_byte = WARMUP_NO_TRIGGER if p.trigger_motor is None else (p.trigger_motor & 0xFF)
        encoded.extend(
            WARMUP_MOTOR_HDR_STRUCT.pack(
                p.start_delay_ms & 0xFFFF,
                trigger_byte,
                p.trigger_steps & 0xFFFF,
                len(p.phases) & 0xFF,
            )
        )
        for j, ph in enumerate(p.phases):
            peak_dhz = _freq_to_dhz(ph.peak_hz)
            accel_dhz_per_s = _freq_to_dhz(ph.accel_hz_per_s)
            decel_dhz_per_s = _freq_to_dhz(ph.decel_hz_per_s)
            if ph.direction not in (-1, 1):
                raise ValueError(f"motor {i} phase {j}: direction must be -1 or 1")
            if not (0 <= ph.target_steps <= 0xFFFF):
                raise ValueError(f"motor {i} phase {j}: target_steps out of range: {ph.target_steps}")
            if peak_dhz == 0 and ph.target_steps != 0:
                raise ValueError(
                    f"motor {i} phase {j}: target_steps must be 0 when peak_hz is 0"
                )
            if peak_dhz > 0 and ph.target_steps == 0:
                raise ValueError(
                    f"motor {i} phase {j}: target_steps must be > 0 when peak_hz > 0"
                )
            if peak_dhz > 0 and decel_dhz_per_s == 0:
                raise ValueError(
                    f"motor {i} phase {j}: decel_hz_per_s must be > 0 when peak_hz > 0"
                )
            if not (0 <= ph.hold_ms <= 0xFFFF):
                raise ValueError(f"motor {i} phase {j}: hold_ms out of range: {ph.hold_ms}")
            phase_flags = STEP_MOTION_PHASE_FLAG_REVERSE if ph.direction < 0 else 0
            encoded.extend(
                STEP_MOTION_PHASE_STRUCT.pack(
                    phase_flags & 0xFF,
                    ph.target_steps & 0xFFFF,
                    peak_dhz,
                    accel_dhz_per_s,
                    decel_dhz_per_s,
                    ph.hold_ms & 0xFFFF,
                )
            )
    return bytes(encoded)


def decode_step_motion_payload(payload: bytes) -> list[StepMotionMotorParams]:
    if len(payload) < 1:
        raise ProtocolError("STEP_MOTION payload too short")
    motor_count = payload[0]
    if motor_count == 0:
        raise ProtocolError("STEP_MOTION motor_count must be > 0")
    if motor_count > 8:
        raise ProtocolError("STEP_MOTION motor_count must be <= 8")

    result: list[StepMotionMotorParams] = []
    cursor = 1
    hdr_size = WARMUP_MOTOR_HDR_STRUCT.size
    phase_size = STEP_MOTION_PHASE_STRUCT.size

    for motor_idx in range(motor_count):
        if cursor + hdr_size > len(payload):
            raise ProtocolError(f"STEP_MOTION payload truncated at motor {motor_idx} header")
        start_delay_ms, trigger_byte, trigger_steps, phase_count = (
            WARMUP_MOTOR_HDR_STRUCT.unpack(payload[cursor : cursor + hdr_size])
        )
        cursor += hdr_size
        if phase_count == 0 or phase_count > STEP_MOTION_MAX_PHASES:
            raise ProtocolError(f"STEP_MOTION motor {motor_idx}: invalid phase_count {phase_count}")
        trigger_motor: int | None = None if trigger_byte == WARMUP_NO_TRIGGER else int(trigger_byte)
        phases: list[StepMotionPhase] = []
        for ph_idx in range(phase_count):
            if cursor + phase_size > len(payload):
                raise ProtocolError(
                    f"STEP_MOTION payload truncated at motor {motor_idx} phase {ph_idx}"
                )
            phase_flags, target_steps, peak_dhz, accel_dhz_per_s, decel_dhz_per_s, hold_ms = (
                STEP_MOTION_PHASE_STRUCT.unpack(payload[cursor : cursor + phase_size])
            )
            cursor += phase_size
            if phase_flags & ~STEP_MOTION_PHASE_FLAG_REVERSE:
                raise ProtocolError(
                    f"STEP_MOTION motor {motor_idx} phase {ph_idx}: reserved flags 0x{phase_flags:02x}"
                )
            phases.append(
                StepMotionPhase(
                    target_steps=target_steps,
                    peak_hz=peak_dhz / 10.0,
                    accel_hz_per_s=accel_dhz_per_s / 10.0,
                    decel_hz_per_s=decel_dhz_per_s / 10.0,
                    hold_ms=hold_ms,
                    direction=-1 if (phase_flags & STEP_MOTION_PHASE_FLAG_REVERSE) else 1,
                )
            )

        result.append(
            StepMotionMotorParams(
                phases=tuple(phases),
                start_delay_ms=start_delay_ms,
                trigger_motor=trigger_motor,
                trigger_steps=trigger_steps,
            )
        )
    return result


def _freq_to_dhz(freq_hz: float) -> int:
    value = int(round(max(0.0, freq_hz) * 10.0))
    if value > 0xFFFF:
        return 0xFFFF
    return value


def _freq_to_u32_dhz(freq_hz: float) -> int:
    value = int(round(max(0.0, freq_hz) * 10.0))
    if value > 0xFFFFFFFF:
        return 0xFFFFFFFF
    return value


def encode_stream_append_payload(segments: list[Segment]) -> bytes:
    if not segments:
        raise ValueError("segments batch cannot be empty")
    if len(segments) > MAX_SEGMENTS_PER_APPEND:
        raise ValueError(f"too many segments for one payload: {len(segments)}")

    include_direction_flip_mask = any(segment.direction_flip_mask != 0 for segment in segments)
    segment_struct = SEGMENT_WITH_DIRECTION_STRUCT if include_direction_flip_mask else SEGMENT_STRUCT
    encoded = bytearray([len(segments) & 0xFF])
    for segment in segments:
        if len(segment.motor_freq_hz) > 8:
            raise ValueError("STREAM_APPEND supports at most 8 motors")
        freqs = list(segment.motor_freq_hz)
        if len(freqs) < 8:
            freqs.extend([0.0] * (8 - len(freqs)))
        freqs_dhz = [_freq_to_dhz(hz) for hz in freqs]
        if include_direction_flip_mask:
            encoded.extend(
                segment_struct.pack(
                    segment.duration_us,
                    segment.direction_flip_mask & 0xFF,
                    *freqs_dhz,
                )
            )
        else:
            encoded.extend(segment_struct.pack(segment.duration_us, *freqs_dhz))
    return bytes(encoded)


def decode_stream_append_payload(payload: bytes) -> list[Segment]:
    if len(payload) < 1:
        raise ProtocolError("STREAM_APPEND payload too short")
    count = payload[0]
    if count == 0:
        raise ProtocolError("STREAM_APPEND segment_count must be > 0")

    legacy_expected = 1 + count * SEGMENT_STRUCT.size
    extended_expected = 1 + count * SEGMENT_WITH_DIRECTION_STRUCT.size
    if len(payload) == extended_expected:
        segment_struct = SEGMENT_WITH_DIRECTION_STRUCT
        include_direction_flip_mask = True
    elif len(payload) == legacy_expected:
        segment_struct = SEGMENT_STRUCT
        include_direction_flip_mask = False
    else:
        raise ProtocolError("STREAM_APPEND payload size mismatch")

    segments: list[Segment] = []
    cursor = 1
    for _ in range(count):
        if include_direction_flip_mask:
            duration_us, direction_flip_mask, *freqs = segment_struct.unpack(
                payload[cursor : cursor + segment_struct.size]
            )
        else:
            duration_us, *freqs = segment_struct.unpack(payload[cursor : cursor + segment_struct.size])
            direction_flip_mask = 0
        cursor += segment_struct.size
        segments.append(
            Segment(
                duration_us=duration_us,
                motor_freq_hz=tuple(freq / 10.0 for freq in freqs),
                direction_flip_mask=direction_flip_mask,
            )
        )
    return segments


def encode_stream_append_event_groups_payload(event_groups: list[PlaybackEventGroup]) -> bytes:
    if not event_groups:
        raise ValueError("event_groups batch cannot be empty")
    if len(event_groups) > MAX_EVENT_GROUPS_PER_APPEND:
        raise ValueError(f"too many event groups for one payload: {len(event_groups)}")

    encoded = bytearray([len(event_groups) & 0xFF])
    for group in event_groups:
        if len(group.changes) > 8:
            raise ValueError(f"too many motor changes in event group: {len(group.changes)}")
        encoded.extend(EVENT_GROUP_HEADER_STRUCT.pack(group.delta_us, len(group.changes) & 0xFF))
        for change in group.changes:
            flags = EVENT_CHANGE_FLAG_FLIP_BEFORE_RESTART if change.flip_before_restart else 0
            encoded.extend(
                EVENT_MOTOR_CHANGE_STRUCT.pack(
                    change.motor_idx & 0xFF,
                    _freq_to_dhz(change.target_hz),
                    flags,
                )
            )
        if len(encoded) > MAX_PAYLOAD:
            raise ValueError("event group payload exceeds maximum protocol payload")
    return bytes(encoded)


def decode_stream_append_event_groups_payload(payload: bytes) -> list[PlaybackEventGroup]:
    if len(payload) < 1:
        raise ProtocolError("STREAM_APPEND event-group payload too short")
    count = payload[0]
    if count == 0:
        raise ProtocolError("STREAM_APPEND event_group_count must be > 0")
    if count > MAX_EVENT_GROUPS_PER_APPEND:
        raise ProtocolError("STREAM_APPEND event_group_count exceeds payload capacity")

    event_groups: list[PlaybackEventGroup] = []
    cursor = 1
    for group_idx in range(count):
        if cursor + EVENT_GROUP_HEADER_STRUCT.size > len(payload):
            raise ProtocolError(f"STREAM_APPEND event group {group_idx} truncated")
        delta_us, change_count = EVENT_GROUP_HEADER_STRUCT.unpack(
            payload[cursor : cursor + EVENT_GROUP_HEADER_STRUCT.size]
        )
        cursor += EVENT_GROUP_HEADER_STRUCT.size
        if change_count == 0:
            raise ProtocolError(
                f"STREAM_APPEND event group {group_idx} change_count must be > 0"
            )
        if change_count > 8:
            raise ProtocolError(
                f"STREAM_APPEND event group {group_idx} has too many motor changes"
            )
        changes: list[PlaybackMotorChange] = []
        for change_idx in range(change_count):
            if cursor + EVENT_MOTOR_CHANGE_STRUCT.size > len(payload):
                raise ProtocolError(
                    f"STREAM_APPEND event group {group_idx} change {change_idx} truncated"
                )
            motor_idx, target_dhz, change_flags = EVENT_MOTOR_CHANGE_STRUCT.unpack(
                payload[cursor : cursor + EVENT_MOTOR_CHANGE_STRUCT.size]
            )
            cursor += EVENT_MOTOR_CHANGE_STRUCT.size
            if (change_flags & ~EVENT_CHANGE_FLAG_FLIP_BEFORE_RESTART) != 0:
                raise ProtocolError(
                    f"STREAM_APPEND event group {group_idx} change {change_idx} has reserved flags set"
                )
            try:
                changes.append(
                    PlaybackMotorChange(
                        motor_idx=int(motor_idx),
                        target_hz=target_dhz / 10.0,
                        flip_before_restart=bool(
                            change_flags & EVENT_CHANGE_FLAG_FLIP_BEFORE_RESTART
                        ),
                    )
                )
            except ValueError as exc:
                raise ProtocolError(
                    f"STREAM_APPEND event group {group_idx} change {change_idx} is invalid"
                ) from exc
        try:
            event_groups.append(
                PlaybackEventGroup(delta_us=int(delta_us), changes=tuple(changes))
            )
        except ValueError as exc:
            raise ProtocolError(
                f"STREAM_APPEND event group {group_idx} is invalid"
            ) from exc
    if cursor != len(payload):
        raise ProtocolError("STREAM_APPEND event-group payload size mismatch")
    return event_groups


def parse_status_payload(payload: bytes) -> StreamStatus:
    if len(payload) < 16:
        raise ProtocolError("STATUS payload too short")
    version, state_flags, motor_count, _reserved = struct.unpack("<BBBB", payload[0:4])
    _ = version
    queue_depth, queue_capacity, credits = struct.unpack("<HHH", payload[4:10])
    active_motors, _reserved2 = struct.unpack("<BB", payload[10:12])
    playhead_us = struct.unpack("<I", payload[12:16])[0]
    device_time_us = 0
    scheduled_start_device_us = 0
    if len(payload) >= 32:
        device_time_us, scheduled_start_device_us = struct.unpack("<QQ", payload[16:32])
    return StreamStatus(
        playing=bool(state_flags & 0x01),
        stream_open=bool(state_flags & 0x02),
        stream_end_received=bool(state_flags & 0x04),
        motor_count=int(motor_count),
        queue_depth=int(queue_depth),
        queue_capacity=int(queue_capacity),
        credits=int(credits),
        active_motors=int(active_motors),
        playhead_us=int(playhead_us),
        device_time_us=int(device_time_us),
        scheduled_start_device_us=int(scheduled_start_device_us),
    )


def parse_metrics_payload(payload: bytes) -> PlaybackMetrics:
    if len(payload) < 24:
        raise ProtocolError("METRICS payload too short")
    (
        underrun_count,
        queue_high_water,
        _reserved,
        scheduling_late_max_us,
        crc_parse_errors,
        _rx_errors_unused,
        queue_depth,
        credits,
    ) = struct.unpack("<IHHIIIHH", payload[:24])
    timer_empty_events = 0
    timer_restart_count = 0
    event_groups_started = 0
    scheduler_guard_hits = 0
    control_late_max_us = 0
    control_overrun_count = 0
    wave_period_update_count = 0
    motor_start_count = 0
    motor_stop_count = 0
    flip_restart_count = 0
    launch_guard_count = 0
    engine_fault_count = 0
    engine_fault_mask = 0
    engine_fault_attach_count = 0
    engine_fault_detach_count = 0
    engine_fault_period_count = 0
    engine_fault_force_count = 0
    engine_fault_timer_count = 0
    engine_fault_invalid_change_count = 0
    engine_fault_last_reason = 0
    engine_fault_last_motor = 0
    inferred_pulse_total = 0
    measured_pulse_total = 0
    measured_pulse_drift_total = 0
    measured_pulse_active_mask = 0
    exact_position_lost_mask = 0
    playback_position_unreliable_mask = 0
    playback_signed_position_drift_total = 0
    if len(payload) >= 36:
        timer_empty_events, timer_restart_count, event_groups_started = struct.unpack("<III", payload[24:36])
    if len(payload) >= 48:
        scheduler_guard_hits, control_late_max_us, control_overrun_count = struct.unpack("<III", payload[36:48])
    if len(payload) >= 52:
        wave_period_update_count = struct.unpack("<I", payload[48:52])[0]
    if len(payload) >= 56:
        motor_start_count = struct.unpack("<I", payload[52:56])[0]
    if len(payload) >= 60:
        motor_stop_count = struct.unpack("<I", payload[56:60])[0]
    if len(payload) >= 64:
        flip_restart_count = struct.unpack("<I", payload[60:64])[0]
    if len(payload) >= 68:
        launch_guard_count = struct.unpack("<I", payload[64:68])[0]
    if len(payload) >= 72:
        engine_fault_count = struct.unpack("<I", payload[68:72])[0]
    if len(payload) >= 76:
        engine_fault_mask = struct.unpack("<I", payload[72:76])[0]
    if len(payload) >= 80:
        engine_fault_attach_count = struct.unpack("<I", payload[76:80])[0]
    if len(payload) >= 84:
        engine_fault_detach_count = struct.unpack("<I", payload[80:84])[0]
    if len(payload) >= 88:
        engine_fault_period_count = struct.unpack("<I", payload[84:88])[0]
    if len(payload) >= 92:
        engine_fault_force_count = struct.unpack("<I", payload[88:92])[0]
    if len(payload) >= 96:
        engine_fault_timer_count = struct.unpack("<I", payload[92:96])[0]
    if len(payload) >= 100:
        engine_fault_invalid_change_count = struct.unpack("<I", payload[96:100])[0]
    if len(payload) >= 104:
        engine_fault_last_reason = struct.unpack("<I", payload[100:104])[0]
    if len(payload) >= 108:
        engine_fault_last_motor = struct.unpack("<I", payload[104:108])[0]
    if len(payload) >= 112:
        inferred_pulse_total = struct.unpack("<I", payload[108:112])[0]
    if len(payload) >= 116:
        measured_pulse_total = struct.unpack("<I", payload[112:116])[0]
    if len(payload) >= 120:
        measured_pulse_drift_total = struct.unpack("<I", payload[116:120])[0]
    if len(payload) >= 124:
        measured_pulse_active_mask = struct.unpack("<I", payload[120:124])[0]
    if len(payload) >= 128:
        exact_position_lost_mask = struct.unpack("<I", payload[124:128])[0]
    if len(payload) >= 132:
        playback_position_unreliable_mask = struct.unpack("<I", payload[128:132])[0]
    if len(payload) >= 136:
        playback_signed_position_drift_total = struct.unpack("<I", payload[132:136])[0]
    return PlaybackMetrics(
        underrun_count=int(underrun_count),
        queue_high_water=int(queue_high_water),
        scheduling_late_max_us=int(scheduling_late_max_us),
        crc_parse_errors=int(crc_parse_errors),
        queue_depth=int(queue_depth),
        credits=int(credits),
        rx_parse_errors=int(_rx_errors_unused),
        timer_empty_events=int(timer_empty_events),
        timer_restart_count=int(timer_restart_count),
        event_groups_started=int(event_groups_started),
        scheduler_guard_hits=int(scheduler_guard_hits),
        control_late_max_us=int(control_late_max_us),
        control_overrun_count=int(control_overrun_count),
        wave_period_update_count=int(wave_period_update_count),
        motor_start_count=int(motor_start_count),
        motor_stop_count=int(motor_stop_count),
        flip_restart_count=int(flip_restart_count),
        launch_guard_count=int(launch_guard_count),
        engine_fault_count=int(engine_fault_count),
        engine_fault_mask=int(engine_fault_mask),
        engine_fault_attach_count=int(engine_fault_attach_count),
        engine_fault_detach_count=int(engine_fault_detach_count),
        engine_fault_period_count=int(engine_fault_period_count),
        engine_fault_force_count=int(engine_fault_force_count),
        engine_fault_timer_count=int(engine_fault_timer_count),
        engine_fault_invalid_change_count=int(engine_fault_invalid_change_count),
        engine_fault_last_reason=int(engine_fault_last_reason),
        engine_fault_last_motor=int(engine_fault_last_motor),
        inferred_pulse_total=int(inferred_pulse_total),
        measured_pulse_total=int(measured_pulse_total),
        measured_pulse_drift_total=int(measured_pulse_drift_total),
        measured_pulse_active_mask=int(measured_pulse_active_mask),
        exact_position_lost_mask=int(exact_position_lost_mask),
        playback_position_unreliable_mask=int(playback_position_unreliable_mask),
        playback_signed_position_drift_total=int(playback_signed_position_drift_total),
    )

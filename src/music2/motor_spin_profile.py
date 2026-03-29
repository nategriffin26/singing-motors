from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .models import Segment

_STEP_QUANTA = 10_000_000  # steps = (sum(dhz * duration_us)) / _STEP_QUANTA


@dataclass(frozen=True)
class SpinProfileConfig:
    motor_indices: tuple[int, ...] = (0, 1, 2, 3, 4, 5)
    steps_per_motor: int = 1600
    peak_hz: float = 400.0
    ramp_segments: int = 10
    ramp_segment_ms: int = 100
    dwell_ms: int = 250
    motor_slots: int = 8

    def __post_init__(self) -> None:
        if not self.motor_indices:
            raise ValueError("motor_indices must not be empty")
        if self.steps_per_motor < 1:
            raise ValueError("steps_per_motor must be >= 1")
        if self.peak_hz <= 0.0:
            raise ValueError("peak_hz must be > 0")
        if self.ramp_segments < 1:
            raise ValueError("ramp_segments must be >= 1")
        if self.ramp_segment_ms < 1:
            raise ValueError("ramp_segment_ms must be >= 1")
        if self.dwell_ms < 0:
            raise ValueError("dwell_ms must be >= 0")
        if self.motor_slots < 1 or self.motor_slots > 8:
            raise ValueError("motor_slots must be in range [1, 8]")
        if min(self.motor_indices) < 0:
            raise ValueError("motor_indices must be >= 0")
        if max(self.motor_indices) >= self.motor_slots:
            raise ValueError("motor index exceeds available motor_slots")


def _to_dhz(freq_hz: float) -> int:
    return int(round(freq_hz * 10.0))


def _build_ramp_dhz(peak_dhz: int, ramp_segments: int) -> list[int]:
    if peak_dhz % ramp_segments != 0:
        raise ValueError(
            f"peak_hz ({peak_dhz / 10.0:.1f}) must divide evenly across "
            f"ramp_segments ({ramp_segments}) at 0.1 Hz resolution"
        )
    step = peak_dhz // ramp_segments
    if step <= 0:
        raise ValueError("ramp frequency increment must be > 0")
    return [step * (idx + 1) for idx in range(ramp_segments)]


def _segment_for_motor(motor_idx: int, *, dhz: int, duration_us: int, motor_slots: int) -> Segment:
    freqs = [0.0] * motor_slots
    freqs[motor_idx] = dhz / 10.0
    return Segment(duration_us=duration_us, motor_freq_hz=tuple(freqs))


def build_spin_test_segments(cfg: SpinProfileConfig) -> list[Segment]:
    peak_dhz = _to_dhz(cfg.peak_hz)
    if peak_dhz <= 0:
        raise ValueError("peak_hz must round to at least 0.1 Hz")

    ramp_freqs_dhz = _build_ramp_dhz(peak_dhz, cfg.ramp_segments)
    ramp_us = cfg.ramp_segment_ms * 1000
    ramp_step_quanta = sum(dhz * ramp_us for dhz in ramp_freqs_dhz)
    target_quanta = cfg.steps_per_motor * _STEP_QUANTA

    cruise_quanta = target_quanta - (2 * ramp_step_quanta)
    if cruise_quanta < 0:
        raise ValueError(
            "steps_per_motor is too low for requested ramp shape; lower ramp or peak_hz"
        )
    if cruise_quanta % peak_dhz != 0:
        raise ValueError(
            "requested profile cannot achieve exact step count with integer microseconds; "
            "adjust steps_per_motor, peak_hz, or ramp settings"
        )
    cruise_us = cruise_quanta // peak_dhz
    if cruise_us <= 0:
        raise ValueError("cruise segment must be > 0 us")

    segments: list[Segment] = []
    for motor_idx in cfg.motor_indices:
        for dhz in ramp_freqs_dhz:
            segments.append(
                _segment_for_motor(
                    motor_idx,
                    dhz=dhz,
                    duration_us=ramp_us,
                    motor_slots=cfg.motor_slots,
                )
            )
        segments.append(
            _segment_for_motor(
                motor_idx,
                dhz=peak_dhz,
                duration_us=cruise_us,
                motor_slots=cfg.motor_slots,
            )
        )
        for dhz in reversed(ramp_freqs_dhz):
            segments.append(
                _segment_for_motor(
                    motor_idx,
                    dhz=dhz,
                    duration_us=ramp_us,
                    motor_slots=cfg.motor_slots,
                )
            )
        if cfg.dwell_ms > 0:
            segments.append(Segment(duration_us=cfg.dwell_ms * 1000, motor_freq_hz=(0.0,) * cfg.motor_slots))

    expected = cfg.steps_per_motor
    for motor_idx, steps in per_motor_step_counts(segments, cfg.motor_indices).items():
        if steps != expected:
            raise RuntimeError(
                f"profile bug: motor {motor_idx} expected {expected} steps but planned {steps}"
            )
    return segments


def per_motor_step_counts(segments: Iterable[Segment], motor_indices: Iterable[int]) -> dict[int, int]:
    totals = {idx: 0 for idx in motor_indices}
    for segment in segments:
        for idx in totals:
            dhz = _to_dhz(segment.motor_freq_hz[idx])
            totals[idx] += dhz * segment.duration_us
    return {idx: int(round(total / _STEP_QUANTA)) for idx, total in totals.items()}

from __future__ import annotations

from array import array
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Iterator, Literal
import wave

from .compiler import compile_segments
from .config import HostConfig
from .instrument_profile import load_instrument_profile
from .midi import analyze_midi, midi_note_to_freq
from .models import (
    CompileOptions,
    CompileReport,
    MidiAnalysisReport,
    NoteEvent,
    PlaybackMotorChange,
)
from .playback_modes import build_default_playback_program
from .playback_program import PlaybackPlan, PlaybackProgram

_MOTOR_SLOTS = 8
_U16_MAX = 0xFFFF
_STOP_RAMP_HALF_PERIOD_US = 50_000
_MICROSTEP_RATIO = 16.0
_INT16_MAX = 32_767
_MODEL_ID = "stepper_mic_v3_close"
_TAU = math.tau


@dataclass(frozen=True)
class RenderWavOptions:
    sample_rate: int = 48_000
    normalize: bool = True
    firmware_emulate: bool = True
    max_accel_dhz_per_s: int = 80_000
    launch_accel_dhz_per_s: int = 50_000
    launch_start_hz: float = 60.0
    launch_crossover_hz: float = 180.0
    safe_max_freq_hz: float | None = None
    clamp_frequencies: bool = False
    model: Literal["stepper_mic_v3_close"] = _MODEL_ID

    def __post_init__(self) -> None:
        if self.sample_rate < 8_000:
            raise ValueError("sample_rate must be >= 8000")
        if self.max_accel_dhz_per_s < 0:
            raise ValueError("max_accel_dhz_per_s must be >= 0")
        if self.launch_accel_dhz_per_s < 0:
            raise ValueError("launch_accel_dhz_per_s must be >= 0")
        if self.launch_start_hz < 0.0:
            raise ValueError("launch_start_hz must be >= 0")
        if self.launch_crossover_hz < 0.0:
            raise ValueError("launch_crossover_hz must be >= 0")
        if self.launch_crossover_hz < self.launch_start_hz:
            raise ValueError("launch_crossover_hz must be >= launch_start_hz")
        if self.safe_max_freq_hz is not None and self.safe_max_freq_hz < 0.0:
            raise ValueError("safe_max_freq_hz must be >= 0")


@dataclass(frozen=True)
class RenderWavResult:
    wav_path: Path
    metadata_path: Path
    duration_s: float
    sample_rate: int
    peak: float
    rms: float
    segment_count: int


@dataclass(frozen=True)
class _EffectiveSegment:
    duration_us: int
    freq_hz: tuple[float, ...]
    freq_dhz: tuple[int, ...]
    boundary_changes: tuple[PlaybackMotorChange, ...]


@dataclass
class _MotorRampState:
    current_hz: float = 0.0
    start_hz: float = 0.0
    target_hz: float = 0.0
    ramp_total_s: float = 0.0
    ramp_elapsed_s: float = 0.0
    launch_stage_target_hz: float = 0.0
    pending_restart_target_hz: float = 0.0
    launch_start_hz: float = 0.0
    launch_crossover_hz: float = 0.0
    launch_accel_dhz_per_s: int = 0
    run_accel_dhz_per_s: int = 0
    stop_after_ramp: bool = False
    restart_after_ramp: bool = False

    def force_stop(self) -> None:
        self.current_hz = 0.0
        self.start_hz = 0.0
        self.target_hz = 0.0
        self.ramp_total_s = 0.0
        self.ramp_elapsed_s = 0.0
        self.launch_stage_target_hz = 0.0
        self.pending_restart_target_hz = 0.0
        self.launch_start_hz = 0.0
        self.launch_crossover_hz = 0.0
        self.launch_accel_dhz_per_s = 0
        self.run_accel_dhz_per_s = 0
        self.stop_after_ramp = False
        self.restart_after_ramp = False

    def _start_ramp(self, start_hz: float, target_hz: float, *, accel_dhz_per_s: int) -> None:
        start_hz = max(0.0, float(start_hz))
        target_hz = max(0.0, float(target_hz))
        self.current_hz = start_hz
        self.start_hz = start_hz
        self.target_hz = target_hz
        self.ramp_total_s = _compute_ramp_s(
            start_hz=self.start_hz,
            target_hz=self.target_hz,
            max_accel_dhz_per_s=accel_dhz_per_s,
        )
        self.ramp_elapsed_s = 0.0
        if self.ramp_total_s <= 0.0:
            self.current_hz = self.target_hz
            self.ramp_total_s = 0.0

    def _start_launch(
        self,
        target_hz: float,
        *,
        launch_start_hz: float,
        launch_crossover_hz: float,
        launch_accel_dhz_per_s: int,
        run_accel_dhz_per_s: int,
    ) -> None:
        target_hz = max(0.0, float(target_hz))
        if target_hz <= 0.0:
            self.force_stop()
            return
        if target_hz <= launch_start_hz or launch_accel_dhz_per_s <= 0:
            self.current_hz = target_hz
            self.start_hz = target_hz
            self.target_hz = target_hz
            self.ramp_total_s = 0.0
            self.ramp_elapsed_s = 0.0
            self.launch_stage_target_hz = 0.0
            return

        launch_entry_hz = launch_start_hz
        if target_hz <= launch_crossover_hz:
            self.launch_stage_target_hz = 0.0
            self._start_ramp(
                launch_entry_hz,
                target_hz,
                accel_dhz_per_s=launch_accel_dhz_per_s,
            )
            return

        self.launch_stage_target_hz = target_hz
        self._start_ramp(
            launch_entry_hz,
            launch_crossover_hz,
            accel_dhz_per_s=launch_accel_dhz_per_s,
        )
        if self.ramp_total_s <= 0.0:
            self.launch_stage_target_hz = 0.0
            self._start_ramp(
                launch_crossover_hz,
                target_hz,
                accel_dhz_per_s=run_accel_dhz_per_s,
            )

    def apply_target(
        self,
        target_hz: float,
        *,
        flip_before_restart: bool,
        run_accel_dhz_per_s: int,
        launch_accel_dhz_per_s: int,
        launch_start_hz: float,
        launch_crossover_hz: float,
    ) -> None:
        target_hz = max(0.0, float(target_hz))
        self.launch_start_hz = launch_start_hz
        self.launch_crossover_hz = launch_crossover_hz
        self.launch_accel_dhz_per_s = launch_accel_dhz_per_s
        self.run_accel_dhz_per_s = run_accel_dhz_per_s
        if target_hz <= 0.0:
            if self.current_hz <= 0.0:
                self.force_stop()
                return
            self.launch_stage_target_hz = 0.0
            self.pending_restart_target_hz = 0.0
            self.restart_after_ramp = False
            self._start_ramp(self.current_hz, 0.0, accel_dhz_per_s=run_accel_dhz_per_s)
            self.stop_after_ramp = True
            return

        if flip_before_restart:
            if self.current_hz <= 0.0:
                self.pending_restart_target_hz = 0.0
                self.restart_after_ramp = False
                self.stop_after_ramp = False
                self._start_launch(
                    target_hz,
                    launch_start_hz=launch_start_hz,
                    launch_crossover_hz=launch_crossover_hz,
                    launch_accel_dhz_per_s=launch_accel_dhz_per_s,
                    run_accel_dhz_per_s=run_accel_dhz_per_s,
                )
                return
            self.pending_restart_target_hz = target_hz
            self.launch_stage_target_hz = 0.0
            self.restart_after_ramp = True
            self._start_ramp(self.current_hz, 0.0, accel_dhz_per_s=run_accel_dhz_per_s)
            self.stop_after_ramp = True
            return

        if self.current_hz <= 0.0:
            self.pending_restart_target_hz = 0.0
            self.restart_after_ramp = False
            self.stop_after_ramp = False
            self._start_launch(
                target_hz,
                launch_start_hz=launch_start_hz,
                launch_crossover_hz=launch_crossover_hz,
                launch_accel_dhz_per_s=launch_accel_dhz_per_s,
                run_accel_dhz_per_s=run_accel_dhz_per_s,
            )
            return

        self.pending_restart_target_hz = 0.0
        self.launch_stage_target_hz = 0.0
        self.stop_after_ramp = False
        self.restart_after_ramp = False
        self._start_ramp(self.current_hz, target_hz, accel_dhz_per_s=run_accel_dhz_per_s)

    def advance(self, dt_s: float) -> float:
        if self.ramp_total_s > 0.0 and self.ramp_elapsed_s < self.ramp_total_s:
            self.ramp_elapsed_s = min(self.ramp_total_s, self.ramp_elapsed_s + dt_s)
            frac = self.ramp_elapsed_s / self.ramp_total_s
            self.current_hz = self.start_hz + ((self.target_hz - self.start_hz) * frac)
        else:
            self.current_hz = self.target_hz

        if self.stop_after_ramp and self.ramp_elapsed_s >= self.ramp_total_s:
            restart_target = self.pending_restart_target_hz
            should_restart = self.restart_after_ramp and restart_target > 0.0
            launch_start_hz = self.launch_start_hz
            launch_crossover_hz = self.launch_crossover_hz
            launch_accel_dhz_per_s = self.launch_accel_dhz_per_s
            run_accel_dhz_per_s = self.run_accel_dhz_per_s
            self.force_stop()
            if should_restart:
                self._start_launch(
                    restart_target,
                    launch_start_hz=launch_start_hz,
                    launch_crossover_hz=launch_crossover_hz,
                    launch_accel_dhz_per_s=launch_accel_dhz_per_s,
                    run_accel_dhz_per_s=run_accel_dhz_per_s,
                )
        elif (
            self.launch_stage_target_hz > 0.0
            and self.ramp_elapsed_s >= self.ramp_total_s
            and math.isclose(self.current_hz, self.target_hz, abs_tol=1e-9)
        ):
            final_target_hz = self.launch_stage_target_hz
            self.launch_stage_target_hz = 0.0
            self._start_ramp(
                self.current_hz,
                final_target_hz,
                accel_dhz_per_s=self.run_accel_dhz_per_s,
            )
        return self.current_hz


@dataclass
class _Biquad:
    b0: float
    b1: float
    b2: float
    a1: float
    a2: float
    z1: float = 0.0
    z2: float = 0.0

    def process(self, x: float) -> float:
        y = (self.b0 * x) + self.z1
        self.z1 = (self.b1 * x) - (self.a1 * y) + self.z2
        self.z2 = (self.b2 * x) - (self.a2 * y)
        return y


def _make_biquad_lowpass(sample_rate: int, cutoff_hz: float, q: float) -> _Biquad:
    nyquist_guard = (sample_rate * 0.5) - 10.0
    cutoff = max(10.0, min(cutoff_hz, nyquist_guard))
    q = max(0.1, q)
    omega = (_TAU * cutoff) / float(sample_rate)
    sin_w = math.sin(omega)
    cos_w = math.cos(omega)
    alpha = sin_w / (2.0 * q)
    b0 = (1.0 - cos_w) * 0.5
    b1 = 1.0 - cos_w
    b2 = (1.0 - cos_w) * 0.5
    a0 = 1.0 + alpha
    a1 = -2.0 * cos_w
    a2 = 1.0 - alpha
    inv_a0 = 1.0 / a0
    return _Biquad(
        b0=b0 * inv_a0,
        b1=b1 * inv_a0,
        b2=b2 * inv_a0,
        a1=a1 * inv_a0,
        a2=a2 * inv_a0,
    )


def _make_biquad_highpass(sample_rate: int, cutoff_hz: float, q: float) -> _Biquad:
    nyquist_guard = (sample_rate * 0.5) - 10.0
    cutoff = max(10.0, min(cutoff_hz, nyquist_guard))
    q = max(0.1, q)
    omega = (_TAU * cutoff) / float(sample_rate)
    sin_w = math.sin(omega)
    cos_w = math.cos(omega)
    alpha = sin_w / (2.0 * q)
    b0 = (1.0 + cos_w) * 0.5
    b1 = -(1.0 + cos_w)
    b2 = (1.0 + cos_w) * 0.5
    a0 = 1.0 + alpha
    a1 = -2.0 * cos_w
    a2 = 1.0 - alpha
    inv_a0 = 1.0 / a0
    return _Biquad(
        b0=b0 * inv_a0,
        b1=b1 * inv_a0,
        b2=b2 * inv_a0,
        a1=a1 * inv_a0,
        a2=a2 * inv_a0,
    )


def _make_biquad_bandpass(sample_rate: int, center_hz: float, q: float) -> _Biquad:
    nyquist_guard = (sample_rate * 0.5) - 10.0
    center = max(10.0, min(center_hz, nyquist_guard))
    q = max(0.1, q)
    omega = (_TAU * center) / float(sample_rate)
    sin_w = math.sin(omega)
    cos_w = math.cos(omega)
    alpha = sin_w / (2.0 * q)
    b0 = alpha
    b1 = 0.0
    b2 = -alpha
    a0 = 1.0 + alpha
    a1 = -2.0 * cos_w
    a2 = 1.0 - alpha
    inv_a0 = 1.0 / a0
    return _Biquad(
        b0=b0 * inv_a0,
        b1=b1 * inv_a0,
        b2=b2 * inv_a0,
        a1=a1 * inv_a0,
        a2=a2 * inv_a0,
    )


def _lcg_next(seed: int) -> int:
    return ((1664525 * seed) + 1013904223) & 0xFFFFFFFF


def _lcg_to_unit(seed: int) -> float:
    return (((seed >> 8) & 0xFFFF) / 32767.5) - 1.0


def _hz_to_dhz(freq_hz: float) -> int:
    quantized = int(round(max(0.0, freq_hz) * 10.0))
    return min(_U16_MAX, quantized)


def _stop_floor_hz() -> float:
    stop_dhz = 5_000_000.0 / (_STOP_RAMP_HALF_PERIOD_US * _MICROSTEP_RATIO)
    return stop_dhz / 10.0


def _compute_ramp_s(*, start_hz: float, target_hz: float, max_accel_dhz_per_s: int) -> float:
    if max_accel_dhz_per_s <= 0:
        return 0.0
    delta_dhz = abs(_hz_to_dhz(start_hz) - _hz_to_dhz(target_hz))
    if delta_dhz <= 0:
        return 0.0
    ramp_us = (delta_dhz * 1_000_000.0) / float(max_accel_dhz_per_s)
    if ramp_us < 500.0:
        return 0.0
    return ramp_us / 1_000_000.0


def _unclamped_analysis(analysis: MidiAnalysisReport) -> MidiAnalysisReport:
    unclamped_notes: list[NoteEvent] = []
    for note in analysis.notes:
        raw_freq_hz = midi_note_to_freq(note.transposed_note)
        unclamped_notes.append(replace(note, frequency_hz=raw_freq_hz))
    return replace(analysis, notes=unclamped_notes, clamped_note_count=0)


def _compile_for_render(
    cfg: HostConfig,
    midi_path: Path,
    *,
    clamp_frequencies: bool,
) -> tuple[MidiAnalysisReport, CompileReport, PlaybackProgram]:
    instrument_profile = load_instrument_profile(cfg.instrument_profile_path)
    analysis, _ = analyze_midi(
        midi_path=midi_path,
        min_freq_hz=cfg.min_freq_hz,
        max_freq_hz=cfg.max_freq_hz,
        transpose_override=cfg.transpose_override,
        auto_transpose=cfg.auto_transpose,
    )
    effective_analysis = analysis if clamp_frequencies else _unclamped_analysis(analysis)
    compiled = compile_segments(
        effective_analysis.notes,
        CompileOptions(
            connected_motors=cfg.connected_motors,
            idle_mode=cfg.idle_mode,
            overflow_mode=cfg.overflow_mode,
            sticky_gap_s=cfg.sticky_gap_ms / 1000.0,
            melody_doubling_enabled=cfg.double_melody,
            flip_direction_on_note_change=cfg.flip_direction_on_note_change,
            suppress_tight_direction_flips=cfg.suppress_tight_direction_flips,
            direction_flip_safety_margin_ms=cfg.direction_flip_safety_margin_ms,
            direction_flip_cooldown_ms=cfg.direction_flip_cooldown_ms,
            playback_run_accel_hz_per_s=cfg.playback_run_accel_hz_per_s,
            playback_launch_start_hz=cfg.playback_launch_start_hz,
            playback_launch_accel_hz_per_s=cfg.playback_launch_accel_hz_per_s,
            playback_launch_crossover_hz=cfg.playback_launch_crossover_hz,
        ),
        instrument_profile=instrument_profile,
    )
    if compiled.playback_plan is None or not compiled.playback_plan.event_groups:
        raise RuntimeError("compiled song is empty")
    program = build_default_playback_program(
        analysis=effective_analysis,
        compiled=compiled,
    )
    return effective_analysis, compiled, program


def _effective_segments(
    playback_plan: PlaybackPlan,
    *,
    playback_duration_us: int,
    safe_max_freq_hz: float,
    clamp_frequencies: bool,
) -> tuple[list[_EffectiveSegment], dict[str, int | float | str]]:
    safe_max_dhz = _hz_to_dhz(safe_max_freq_hz)
    command_hash = hashlib.sha256()

    quantized_freq_count = 0
    safety_clamp_count = 0
    u16_clamp_count = 0
    u16_overflow_count = 0
    rendered_duration_us = 0
    max_effective_freq_hz = 0.0
    direction_flip_change_count = 0
    effective: list[_EffectiveSegment] = []
    current_freq_hz = [0.0] * _MOTOR_SLOTS
    elapsed_us = 0
    pending_boundary_changes: tuple[PlaybackMotorChange, ...] = ()

    def _coerce_change(change: PlaybackMotorChange) -> PlaybackMotorChange:
        nonlocal quantized_freq_count, safety_clamp_count, u16_clamp_count, u16_overflow_count
        source_hz = max(0.0, float(change.target_hz))
        raw_dhz = int(round(source_hz * 10.0))
        if not math.isclose(source_hz * 10.0, float(raw_dhz), abs_tol=1e-9):
            quantized_freq_count += 1

        dhz = raw_dhz
        if clamp_frequencies:
            if dhz > _U16_MAX:
                dhz = _U16_MAX
                u16_clamp_count += 1
            if dhz > safe_max_dhz:
                dhz = safe_max_dhz
                safety_clamp_count += 1
            target_hz = dhz / 10.0
        else:
            if dhz > _U16_MAX:
                u16_overflow_count += 1
            target_hz = source_hz
        return PlaybackMotorChange(
            motor_idx=change.motor_idx,
            target_hz=target_hz,
            flip_before_restart=change.flip_before_restart,
        )

    def append_interval(duration_us: int, boundary_changes: tuple[PlaybackMotorChange, ...]) -> None:
        nonlocal rendered_duration_us, quantized_freq_count, safety_clamp_count
        nonlocal u16_clamp_count, u16_overflow_count, max_effective_freq_hz
        if duration_us <= 0:
            return
        rendered_duration_us += duration_us
        freq_dhz: list[int] = []
        freq_hz: list[float] = []
        for raw_hz in current_freq_hz:
            source_hz = max(0.0, float(raw_hz))
            dhz = int(round(source_hz * 10.0))
            hz = source_hz

            max_effective_freq_hz = max(max_effective_freq_hz, hz)
            freq_dhz.append(dhz)
            freq_hz.append(hz)

        command_hash.update(int(duration_us).to_bytes(4, "little", signed=False))
        command_hash.update(len(boundary_changes).to_bytes(1, "little", signed=False))
        for change in boundary_changes:
            command_hash.update(int(change.motor_idx).to_bytes(1, "little", signed=False))
            command_hash.update(_hz_to_dhz(change.target_hz).to_bytes(4, "little", signed=False))
            command_hash.update((1 if change.flip_before_restart else 0).to_bytes(1, "little", signed=False))
        for dhz in freq_dhz:
            command_hash.update(int(dhz).to_bytes(4, "little", signed=False))

        effective.append(
            _EffectiveSegment(
                duration_us=duration_us,
                freq_hz=tuple(freq_hz),
                freq_dhz=tuple(freq_dhz),
                boundary_changes=boundary_changes,
            )
        )

    for group in playback_plan.event_groups:
        append_interval(group.delta_us, pending_boundary_changes)
        elapsed_us += group.delta_us
        applied_changes: list[PlaybackMotorChange] = []
        for change in group.changes:
            if change.motor_idx < _MOTOR_SLOTS:
                coerced = _coerce_change(change)
                current_freq_hz[change.motor_idx] = coerced.target_hz
                applied_changes.append(coerced)
                if coerced.flip_before_restart:
                    direction_flip_change_count += 1
        pending_boundary_changes = tuple(applied_changes)

    append_interval(max(0, playback_duration_us - elapsed_us), pending_boundary_changes)

    summary: dict[str, int | float | str] = {
        "event_group_count": playback_plan.event_group_count,
        "effective_interval_count": len(effective),
        "duration_us": rendered_duration_us,
        "duration_s": rendered_duration_us / 1_000_000.0,
        "quantized_freq_count": quantized_freq_count,
        "u16_clamp_count": u16_clamp_count,
        "u16_overflow_count": u16_overflow_count,
        "safety_clamp_count": safety_clamp_count,
        "max_effective_freq_hz": max_effective_freq_hz,
        "direction_flip_change_count": direction_flip_change_count,
        "timeline_hash_sha256": command_hash.hexdigest(),
    }
    return effective, summary


def _iter_samples(
    segments: list[_EffectiveSegment],
    *,
    options: RenderWavOptions,
) -> Iterator[float]:
    dt_s = 1.0 / float(options.sample_rate)
    run_accel_dhz_per_s = options.max_accel_dhz_per_s if options.firmware_emulate else 0
    launch_accel_dhz_per_s = options.launch_accel_dhz_per_s if options.firmware_emulate else 0
    launch_start_hz = options.launch_start_hz if options.firmware_emulate else 0.0
    launch_crossover_hz = options.launch_crossover_hz if options.firmware_emulate else 0.0
    phases = [0.0] * _MOTOR_SLOTS
    amplitudes = [0.0] * _MOTOR_SLOTS
    states = [_MotorRampState() for _ in range(_MOTOR_SLOTS)]
    motor_noise_seed = [0xABCDEF01 + (idx * 2654435761) for idx in range(_MOTOR_SLOTS)]
    global_noise_seed = 0x13579BDF

    attack_coeff = dt_s / (0.0035 + dt_s)
    release_coeff = dt_s / (0.014 + dt_s)

    mic_hp = _make_biquad_highpass(options.sample_rate, cutoff_hz=70.0, q=0.707)
    mic_lp = _make_biquad_lowpass(options.sample_rate, cutoff_hz=5_200.0, q=0.707)
    mech_res_low = _make_biquad_bandpass(options.sample_rate, center_hz=220.0, q=1.0)
    mech_res_high = _make_biquad_bandpass(options.sample_rate, center_hz=1_100.0, q=1.2)

    for segment in segments:
        for change in segment.boundary_changes:
            states[change.motor_idx].apply_target(
                change.target_hz,
                flip_before_restart=change.flip_before_restart,
                run_accel_dhz_per_s=run_accel_dhz_per_s,
                launch_accel_dhz_per_s=launch_accel_dhz_per_s,
                launch_start_hz=launch_start_hz,
                launch_crossover_hz=launch_crossover_hz,
            )

        frames = max(1, int(round(segment.duration_us * options.sample_rate / 1_000_000.0)))
        for _ in range(frames):
            mix = 0.0
            for motor_idx in range(_MOTOR_SLOTS):
                hz = states[motor_idx].advance(dt_s)

                target_amp = 0.0
                if hz > 0.0:
                    freq_scale = min(1.0, hz / 700.0)
                    target_amp = 0.22 + (0.78 * freq_scale)
                amp_coeff = attack_coeff if target_amp >= amplitudes[motor_idx] else release_coeff
                amplitudes[motor_idx] += (target_amp - amplitudes[motor_idx]) * amp_coeff

                if hz <= 0.0 and amplitudes[motor_idx] < 1e-5:
                    continue

                phase = phases[motor_idx] + ((_TAU * hz) / float(options.sample_rate))
                if phase >= _TAU:
                    phase %= _TAU
                phases[motor_idx] = phase

                motor_noise_seed[motor_idx] = _lcg_next(motor_noise_seed[motor_idx])
                motor_noise = _lcg_to_unit(motor_noise_seed[motor_idx])

                fundamental = math.sin(phase)
                second = math.sin(phase * 2.0)
                third = math.sin((phase * 3.0) + (0.19 * motor_idx))
                edge = 1.0 if phase < math.pi else -1.0
                freq_tilt = min(1.0, hz / 800.0)
                rotor_tone = (
                    (0.78 * fundamental)
                    + (0.16 * second)
                    + (0.07 * third)
                    + (0.07 * edge)
                )
                rotor_tone *= (0.50 + (0.50 * freq_tilt))
                rotor_tone += motor_noise * (0.003 + (0.004 * freq_tilt))
                mix += rotor_tone * amplitudes[motor_idx]

            dry = mix * 0.31
            hp = mic_hp.process(dry)
            lp = mic_lp.process(hp)
            res_low = mech_res_low.process(hp)
            res_high = mech_res_high.process(hp)

            global_noise_seed = _lcg_next(global_noise_seed)
            mic_hiss = _lcg_to_unit(global_noise_seed) * 0.0010

            sample = (
                (0.84 * lp)
                + (0.10 * res_low)
                + (0.05 * res_high)
                + mic_hiss
            )
            yield sample


def _estimate_peak(samples: Iterator[float]) -> float:
    peak = 0.0
    for sample in samples:
        peak = max(peak, abs(sample))
    return peak


def _write_wav(
    wav_path: Path,
    samples: Iterator[float],
    *,
    sample_rate: int,
    gain: float,
) -> tuple[float, float, int]:
    wav_path.parent.mkdir(parents=True, exist_ok=True)
    rms_sum = 0.0
    peak = 0.0
    count = 0
    pcm = array("h")

    with wave.open(str(wav_path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)

        for sample in samples:
            scaled = max(-1.0, min(1.0, sample * gain))
            peak = max(peak, abs(scaled))
            rms_sum += scaled * scaled
            count += 1
            pcm.append(int(round(scaled * _INT16_MAX)))
            if len(pcm) >= 8_192:
                wav.writeframesraw(pcm.tobytes())
                pcm = array("h")

        if pcm:
            wav.writeframesraw(pcm.tobytes())
        wav.writeframes(b"")

    rms = math.sqrt(rms_sum / count) if count > 0 else 0.0
    return peak, rms, count


def _metadata_path(out_wav: Path) -> Path:
    return Path(f"{out_wav}.meta.json")


def render_midi_to_stepper_wav(
    *,
    midi_path: Path,
    cfg: HostConfig,
    out_wav: Path,
    options: RenderWavOptions,
) -> RenderWavResult:
    midi_path = midi_path.expanduser().resolve()
    if not midi_path.exists():
        raise RuntimeError(f"MIDI file not found: {midi_path}")

    safe_max_freq_hz = options.safe_max_freq_hz if options.safe_max_freq_hz is not None else cfg.max_freq_hz
    analysis, compiled, playback_program = _compile_for_render(
        cfg,
        midi_path,
        clamp_frequencies=options.clamp_frequencies,
    )
    playback_plan = playback_program.playback_plan
    effective_segments, command_summary = _effective_segments(
        playback_plan,
        playback_duration_us=int(round(analysis.duration_s * 1_000_000.0)),
        safe_max_freq_hz=safe_max_freq_hz,
        clamp_frequencies=options.clamp_frequencies,
    )

    pre_gain_peak = 0.0
    gain = 1.0
    if options.normalize:
        pre_gain_peak = _estimate_peak(_iter_samples(effective_segments, options=options))
        if pre_gain_peak > 1e-9:
            gain = 0.98 / pre_gain_peak

    post_gain_peak, rms, sample_count = _write_wav(
        out_wav,
        _iter_samples(effective_segments, options=options),
        sample_rate=options.sample_rate,
        gain=gain,
    )
    if not options.normalize:
        pre_gain_peak = post_gain_peak

    metadata_path = _metadata_path(out_wav)
    metadata = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_midi_path": str(midi_path),
        "output_wav_path": str(out_wav.expanduser().resolve()),
        "render_options": asdict(options),
        "config": {
            "connected_motors": cfg.connected_motors,
            "idle_mode": cfg.idle_mode,
            "overflow_mode": cfg.overflow_mode,
            "min_freq_hz": cfg.min_freq_hz,
            "max_freq_hz": cfg.max_freq_hz,
            "transpose_override": cfg.transpose_override,
            "auto_transpose": cfg.auto_transpose,
            "flip_direction_on_note_change": cfg.flip_direction_on_note_change,
        },
        "analysis": {
            "note_count": analysis.note_count,
            "duration_s": analysis.duration_s,
            "max_polyphony": analysis.max_polyphony,
            "transpose_semitones": analysis.transpose_semitones,
            "clamped_note_count": analysis.clamped_note_count,
        },
        "compile": {
            "event_group_count": playback_plan.event_group_count,
            "shadow_segment_count": playback_plan.shadow_segment_count,
            "motor_change_count": playback_plan.motor_change_count,
            "stolen_note_count": compiled.stolen_note_count,
            "dropped_note_count": compiled.dropped_note_count,
            "tight_boundary_warning_count": compiled.tight_boundary_warning_count,
        },
        "playback_program": {
            "mode_id": playback_program.mode_id,
            "display_name": playback_program.display_name,
            "section_count": len(playback_program.sections),
            "duration_total_us": playback_program.total_duration_us,
            "sections": [
                {
                    "section_id": section.section_id,
                    "display_name": section.display_name,
                    "start_offset_us": section.start_offset_us,
                    "duration_us": section.duration_us,
                    "event_group_count": section.playback_plan.event_group_count,
                }
                for section in playback_program.sections
            ],
        },
        "command_timeline": command_summary,
        "audio": {
            "sample_rate": options.sample_rate,
            "sample_count": sample_count,
            "duration_s": sample_count / float(options.sample_rate),
            "pre_gain_peak": pre_gain_peak,
            "gain": gain,
            "peak": post_gain_peak,
            "rms": rms,
            "model": options.model,
            "render_chain": {
                "firmware_emulate": options.firmware_emulate,
                "run_accel_dhz_per_s": options.max_accel_dhz_per_s,
                "launch_accel_dhz_per_s": options.launch_accel_dhz_per_s,
                "launch_start_hz": options.launch_start_hz,
                "launch_crossover_hz": options.launch_crossover_hz,
                "motor_attack_s": 0.0035,
                "motor_release_s": 0.014,
                "mic_highpass_hz": 70.0,
                "mic_lowpass_hz": 5200.0,
                "mechanical_resonance_hz": [220.0, 1100.0],
                "room_mix": 0.0,
                "output_saturation": "none",
            },
        },
    }

    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")

    return RenderWavResult(
        wav_path=out_wav.expanduser().resolve(),
        metadata_path=metadata_path.expanduser().resolve(),
        duration_s=sample_count / float(options.sample_rate),
        sample_rate=options.sample_rate,
        peak=post_gain_peak,
        rms=rms,
        segment_count=len(effective_segments),
    )

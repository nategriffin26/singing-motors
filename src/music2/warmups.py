from __future__ import annotations

from collections.abc import Callable
import math
from typing import Literal, Mapping, Sequence, cast

from .protocol import StepMotionMotorParams, StepMotionPhase, WarmupMotorParams, WarmupPhase

WarmupId = Literal[
    "slot_machine_lock_in",
    "domino_ripple",
    "turbine_spool_up",
    "center_splash",
    "phase_alignment",
    "chord_bloom",
    "pentatonic_cascade",
    "whole_tone_shimmer",
    "blues_stagger",
    "zigzag_march",
    "wave_cascade",
    "mirror_sweep",
    "harmonic_series",
    "chromatic_converge",
    "minor_cascade",
    "gradient_tilt",
    "pendulum_sync",
    "scatter_bloom",
]

WARMUP_IDS: tuple[WarmupId, ...] = (
    "slot_machine_lock_in",
    "domino_ripple",
    "turbine_spool_up",
    "center_splash",
    "phase_alignment",
    "chord_bloom",
    "pentatonic_cascade",
    "whole_tone_shimmer",
    "blues_stagger",
    "zigzag_march",
    "wave_cascade",
    "mirror_sweep",
    "harmonic_series",
    "chromatic_converge",
    "minor_cascade",
    "gradient_tilt",
    "pendulum_sync",
    "scatter_bloom",
)

# Firmware pulse engine microstep scaling constant.  Both host and firmware use
# this same value so the RPM↔Hz conversion is self-consistent.
# See firmware/esp32/src/pulse_engine.c: `MICROSTEP_RATIO 16u`.
_PULSE_ENGINE_MICROSTEP_RATIO = 16.0

# Maximum RPM that stays within the firmware's safe pulse ceiling (8000 dHz =
# 800 Hz).  At 800 steps/rev with the ratio above, 800 Hz ≈ 384 RPM.
_DEFAULT_MAX_RPM = 300.0
_EXACT_STEP_MAX_RPM = 216.0
_EXACT_STEP_CHORD_BLOOM_ACCEL_TIME_S = 0.45
_EXACT_STEP_CHORD_BLOOM_DECEL_TIME_S = 0.35
_EXACT_STEP_SCALE_ACCEL_TIME_S = 0.35
_EXACT_STEP_SCALE_DECEL_TIME_S = 0.30


def _hz_for_rpm(rpm: float, *, steps_per_rev: int) -> float:
    """Convert mechanical RPM to step-pulse Hz using the firmware microstep ratio."""
    rev_per_s = max(0.0, rpm) / 60.0
    return rev_per_s * (float(steps_per_rev) / _PULSE_ENGINE_MICROSTEP_RATIO)


def _active_motor_count(connected_motors: int) -> int:
    return max(0, min(6, connected_motors))


def _normalize_motor_order(
    *,
    connected_motors: int,
    motor_order: Sequence[int] | None,
) -> tuple[int, ...]:
    n = _active_motor_count(connected_motors)
    if n == 0:
        return ()
    if motor_order is None or len(motor_order) == 0:
        return tuple(range(n))
    order = tuple(int(index) for index in motor_order)
    if len(order) != n:
        raise ValueError(
            f"warmup motor_order must contain exactly {n} entries for {n} active motors"
        )
    if len(set(order)) != len(order):
        raise ValueError("warmup motor_order must not contain duplicates")
    for index in order:
        if index < 0 or index >= connected_motors:
            raise ValueError(
                f"warmup motor_order index out of range [0, {connected_motors - 1}]: {index}"
            )
    return order


def _idle_motor(accel: float) -> WarmupMotorParams:
    """Return a do-nothing motor entry (peak_hz=0 single phase)."""
    return WarmupMotorParams(
        phases=(WarmupPhase(peak_hz=0.0, accel_hz_per_s=accel,
                            decel_hz_per_s=accel, hold_ms=1),),
    )


def _idle_step_motion_motor(accel: float) -> StepMotionMotorParams:
    """Return a do-nothing exact-motion motor entry."""
    safe_accel = max(1.0, accel)
    return StepMotionMotorParams(
        phases=(StepMotionPhase(
            target_steps=0,
            peak_hz=0.0,
            accel_hz_per_s=safe_accel,
            decel_hz_per_s=safe_accel,
            hold_ms=1,
            direction=1,
        ),),
    )


# ---------------------------------------------------------------------------
# 1. Slot Machine Lock-In
#
# Spec:
#   0–1.7 s  All six motors spin at slightly different high RPMs (chaos blur).
#   1.7–2.6 s  Left-to-right (motor 0 first), each abruptly brakes and locks
#              in, with a brisk cadence between successive stops.  HOME snaps
#              to 12 o'clock.
#
# Implementation:
#   Each motor gets a single phase.  The chaos window lasts 1.7 s from t=0.
#   Motor i stops (end of decel) at t = 1.7 + i * 0.18 s.
#   hold_ms = stop_time - accel_time - decel_time.
#   High decel rate → "abrupt brake" feel.
# ---------------------------------------------------------------------------

def _slot_machine_lock_in(
    connected_motors: int,
    steps_per_rev: int,
    accel_hz_per_s: float,
) -> list[WarmupMotorParams]:
    n = _active_motor_count(connected_motors)

    # Slightly different chaos RPMs per motor (spread across a tight band so
    # the wall reads as one blur before the quick lock-in sweep).
    chaos_rpm = [252.0, 266.0, 278.0, 284.0, 270.0, 258.0]

    params: list[WarmupMotorParams] = []
    for i in range(connected_motors):
        if i >= n:
            params.append(_idle_motor(accel_hz_per_s))
            continue

        peak_hz = _hz_for_rpm(chaos_rpm[i], steps_per_rev=steps_per_rev)
        # Exit low-frequency jitter bands quickly at phase start while
        # preserving the left-to-right lock timing envelope.
        accel_rate = max(accel_hz_per_s * 4.0, peak_hz / 0.22)
        decel_rate = max(accel_rate * 1.30, accel_hz_per_s * 5.0)

        # Time for accel ramp (0 → peak) and decel ramp (peak → 0).
        accel_time_ms = int(round((peak_hz / accel_rate) * 1000.0))
        decel_time_ms = int(round((peak_hz / decel_rate) * 1000.0))

        # Motor i's decel completes at 1700 + i * 180 ms from command start.
        stop_ms = 1700 + i * 180
        # Decel begins at (stop_ms - decel_time_ms).
        # Hold begins at accel_time_ms.
        # hold = decel_start - accel_time = (stop_ms - decel_time_ms) - accel_time_ms
        hold_ms = max(0, stop_ms - decel_time_ms - accel_time_ms)

        params.append(WarmupMotorParams(
            phases=(WarmupPhase(
                peak_hz=peak_hz,
                accel_hz_per_s=accel_rate,
                decel_hz_per_s=decel_rate,
                hold_ms=hold_ms,
            ),),
            start_delay_ms=0,
        ))
    return params


def _remap_motor_profiles(
    params: list[WarmupMotorParams],
    *,
    connected_motors: int,
    motor_order: tuple[int, ...],
) -> list[WarmupMotorParams]:
    n = _active_motor_count(connected_motors)
    if n == 0 or motor_order == tuple(range(n)):
        return params

    remapped: list[WarmupMotorParams] = []
    for i in range(connected_motors):
        base_accel = params[i].phases[0].accel_hz_per_s if params[i].phases else 1.0
        remapped.append(_idle_motor(max(1.0, base_accel)))

    for logical_idx in range(n):
        src = params[logical_idx]
        physical_idx = motor_order[logical_idx]
        trigger_motor = src.trigger_motor
        if trigger_motor is not None:
            if trigger_motor < 0 or trigger_motor >= n:
                raise ValueError(f"invalid trigger motor index {trigger_motor} for remap")
            trigger_motor = motor_order[trigger_motor]
        remapped[physical_idx] = WarmupMotorParams(
            phases=src.phases,
            start_delay_ms=src.start_delay_ms,
            trigger_motor=trigger_motor,
            trigger_steps=src.trigger_steps,
        )

    return remapped


def _remap_step_motion_profiles(
    params: list[StepMotionMotorParams],
    *,
    connected_motors: int,
    motor_order: tuple[int, ...],
) -> list[StepMotionMotorParams]:
    n = _active_motor_count(connected_motors)
    if n == 0 or motor_order == tuple(range(n)):
        return params

    remapped: list[StepMotionMotorParams] = []
    for i in range(connected_motors):
        base_accel = params[i].phases[0].accel_hz_per_s if params[i].phases else 1.0
        remapped.append(_idle_step_motion_motor(base_accel))

    for logical_idx in range(n):
        src = params[logical_idx]
        physical_idx = motor_order[logical_idx]
        trigger_motor = src.trigger_motor
        if trigger_motor is not None:
            if trigger_motor < 0 or trigger_motor >= n:
                raise ValueError(f"invalid trigger motor index {trigger_motor} for remap")
            trigger_motor = motor_order[trigger_motor]
        remapped[physical_idx] = StepMotionMotorParams(
            phases=src.phases,
            start_delay_ms=src.start_delay_ms,
            trigger_motor=trigger_motor,
            trigger_steps=src.trigger_steps,
        )

    return remapped


def _domino_single_rotation_phase(
    *,
    peak_hz: float,
    accel_hz_per_s: float,
    steps_per_rev: int,
) -> WarmupPhase:
    if peak_hz <= 0.0:
        accel_rate = max(1.0, accel_hz_per_s)
        return WarmupPhase(
            peak_hz=0.0,
            accel_hz_per_s=accel_rate,
            decel_hz_per_s=accel_rate,
            hold_ms=0,
        )

    step_rate = peak_hz * _PULSE_ENGINE_MICROSTEP_RATIO
    if step_rate <= 0.0:
        accel_rate = max(1.0, accel_hz_per_s)
        return WarmupPhase(
            peak_hz=peak_hz,
            accel_hz_per_s=accel_rate,
            decel_hz_per_s=accel_rate,
            hold_ms=0,
        )

    one_rev_steps = float(steps_per_rev)
    accel_rate = max(1.0, accel_hz_per_s)
    # Respect configured accel cap. If requested peak is too fast to fit a
    # single-rev trapezoid at this accel, clamp peak instead of forcing accel
    # higher (which can induce physical skip on heavier pointers).
    peak_cap = ((accel_rate * one_rev_steps) / _PULSE_ENGINE_MICROSTEP_RATIO) ** 0.5
    effective_peak_hz = min(max(0.0, peak_hz), peak_cap)
    effective_step_rate = effective_peak_hz * _PULSE_ENGINE_MICROSTEP_RATIO
    if effective_step_rate <= 0.0:
        return WarmupPhase(
            peak_hz=0.0,
            accel_hz_per_s=accel_rate,
            decel_hz_per_s=accel_rate,
            hold_ms=0,
        )

    accel_time_s = effective_peak_hz / accel_rate
    ramp_steps = 0.5 * effective_step_rate * accel_time_s
    hold_steps = max(0.0, one_rev_steps - (2.0 * ramp_steps))
    hold_ms = max(0, int(round((hold_steps / effective_step_rate) * 1000.0)))

    return WarmupPhase(
        peak_hz=effective_peak_hz,
        accel_hz_per_s=accel_rate,
        decel_hz_per_s=accel_rate,
        hold_ms=hold_ms,
    )


def _phase_for_target_duration(
    *,
    peak_hz: float,
    accel_hz_per_s: float,
    decel_hz_per_s: float,
    target_total_ms: int,
    steps_per_rev: int,
) -> WarmupPhase:
    if peak_hz <= 0.0:
        accel_rate = max(1.0, accel_hz_per_s, decel_hz_per_s)
        return WarmupPhase(
            peak_hz=0.0,
            accel_hz_per_s=accel_rate,
            decel_hz_per_s=accel_rate,
            hold_ms=max(0, int(target_total_ms)),
        )

    accel_rate = max(1.0, accel_hz_per_s)
    decel_rate = max(1.0, decel_hz_per_s)
    step_rate = peak_hz * _PULSE_ENGINE_MICROSTEP_RATIO
    if step_rate <= 0.0:
        return WarmupPhase(
            peak_hz=peak_hz,
            accel_hz_per_s=accel_rate,
            decel_hz_per_s=decel_rate,
            hold_ms=0,
        )

    accel_s = peak_hz / accel_rate
    decel_s = peak_hz / decel_rate
    desired_total_s = max(0.0, float(target_total_ms) / 1000.0)
    desired_steps = step_rate * max(0.0, desired_total_s - (0.5 * (accel_s + decel_s)))
    rev_steps = max(1, steps_per_rev)
    total_steps = max(float(rev_steps), round(desired_steps / float(rev_steps)) * float(rev_steps))
    ramp_steps = step_rate * ((0.5 * accel_s) + (0.5 * decel_s))
    hold_steps = max(0.0, total_steps - ramp_steps)
    hold_ms = max(0, int(round((hold_steps / step_rate) * 1000.0)))

    return WarmupPhase(
        peak_hz=peak_hz,
        accel_hz_per_s=accel_rate,
        decel_hz_per_s=decel_rate,
        hold_ms=hold_ms,
    )


def _phase_total_ms(phase: WarmupPhase) -> int:
    accel_ms = int(round((phase.peak_hz / phase.accel_hz_per_s) * 1000.0)) if phase.accel_hz_per_s > 0.0 else 0
    decel_ms = int(round((phase.peak_hz / phase.decel_hz_per_s) * 1000.0)) if phase.decel_hz_per_s > 0.0 else 0
    return accel_ms + phase.hold_ms + decel_ms


def _retune_domino_ripple(
    params: list[WarmupMotorParams],
    *,
    steps_per_rev: int,
) -> list[WarmupMotorParams]:
    tuned: list[WarmupMotorParams] = []
    for p in params:
        if not p.phases:
            tuned.append(p)
            continue
        new_phases: list[WarmupPhase] = []
        for ph in p.phases:
            if ph.peak_hz <= 0.0:
                # Preserve idle/gap phases unchanged.
                new_phases.append(ph)
                continue
            new_phases.append(
                _domino_single_rotation_phase(
                    peak_hz=ph.peak_hz,
                    accel_hz_per_s=ph.accel_hz_per_s,
                    steps_per_rev=steps_per_rev,
                )
            )
        tuned.append(
            WarmupMotorParams(
                phases=tuple(new_phases),
                start_delay_ms=p.start_delay_ms,
                trigger_motor=p.trigger_motor,
                trigger_steps=p.trigger_steps,
            )
        )
    return tuned


def _retune_chord_bloom_for_step_motion(
    params: list[WarmupMotorParams],
    *,
    steps_per_rev: int,
) -> list[WarmupMotorParams]:
    active_peaks = [
        ph.peak_hz
        for p in params
        for ph in p.phases
        if ph.peak_hz > 0.0
    ]
    if not active_peaks:
        return params

    safe_max_hz = _hz_for_rpm(_EXACT_STEP_MAX_RPM, steps_per_rev=steps_per_rev)
    scale = min(1.0, safe_max_hz / max(active_peaks))

    tuned: list[WarmupMotorParams] = []
    for p in params:
        new_phases: list[WarmupPhase] = []
        for ph in p.phases:
            if ph.peak_hz <= 0.0:
                new_phases.append(ph)
                continue
            peak_hz = ph.peak_hz * scale
            accel_rate = max(1.0, peak_hz / _EXACT_STEP_CHORD_BLOOM_ACCEL_TIME_S)
            decel_rate = max(1.0, peak_hz / _EXACT_STEP_CHORD_BLOOM_DECEL_TIME_S)
            new_phases.append(
                _phase_for_target_duration(
                    peak_hz=peak_hz,
                    accel_hz_per_s=accel_rate,
                    decel_hz_per_s=decel_rate,
                    target_total_ms=_phase_total_ms(ph),
                    steps_per_rev=steps_per_rev,
                )
            )
        tuned.append(
            WarmupMotorParams(
                phases=tuple(new_phases),
                start_delay_ms=p.start_delay_ms,
                trigger_motor=p.trigger_motor,
                trigger_steps=p.trigger_steps,
            )
        )
    return tuned


def _retune_scale_for_step_motion(
    params: list[WarmupMotorParams],
    *,
    steps_per_rev: int,
) -> list[WarmupMotorParams]:
    """Scale musical-scale warmups to the safe exact-step RPM ceiling."""
    active_peaks = [
        ph.peak_hz
        for p in params
        for ph in p.phases
        if ph.peak_hz > 0.0
    ]
    if not active_peaks:
        return params

    safe_max_hz = _hz_for_rpm(_EXACT_STEP_MAX_RPM, steps_per_rev=steps_per_rev)
    scale = min(1.0, safe_max_hz / max(active_peaks))

    tuned: list[WarmupMotorParams] = []
    for p in params:
        new_phases: list[WarmupPhase] = []
        for ph in p.phases:
            if ph.peak_hz <= 0.0:
                new_phases.append(ph)
                continue
            peak_hz = ph.peak_hz * scale
            accel_rate = max(1.0, peak_hz / _EXACT_STEP_SCALE_ACCEL_TIME_S)
            decel_rate = max(1.0, peak_hz / _EXACT_STEP_SCALE_DECEL_TIME_S)
            new_phases.append(
                _phase_for_target_duration(
                    peak_hz=peak_hz,
                    accel_hz_per_s=accel_rate,
                    decel_hz_per_s=decel_rate,
                    target_total_ms=_phase_total_ms(ph),
                    steps_per_rev=steps_per_rev,
                )
            )
        tuned.append(
            WarmupMotorParams(
                phases=tuple(new_phases),
                start_delay_ms=p.start_delay_ms,
                trigger_motor=p.trigger_motor,
                trigger_steps=p.trigger_steps,
            )
        )
    return tuned


def _retune_for_step_motion(
    params: list[WarmupMotorParams],
    *,
    steps_per_rev: int,
    warmup_id: WarmupId,
) -> list[WarmupMotorParams]:
    if warmup_id == "chord_bloom":
        return _retune_chord_bloom_for_step_motion(params, steps_per_rev=steps_per_rev)
    if warmup_id in {
        "pentatonic_cascade", "whole_tone_shimmer", "blues_stagger",
        "harmonic_series", "chromatic_converge", "minor_cascade",
    }:
        return _retune_scale_for_step_motion(params, steps_per_rev=steps_per_rev)
    return params


# ---------------------------------------------------------------------------
# 2. Domino Ripple
#
# Spec:
#   Motor 0 begins one rapid 360° rotation.  Exactly as motor 0 passes 120°
#   (one third of a turn), motor 1 begins its 360° rotation.  The cascade
#   continues left-to-right.  Once the rightmost motor passes 120°, the
#   cascade reverses: the rightmost motor acts as the turnaround pivot
#   (no second spin), then the next motor inward starts the return wave,
#   and so on back to motor 0.
#
# Implementation:
#   Forward wave: position triggers cascade left-to-right (same as before).
#   Return wave:  timed idle gaps bridge each motor from the end of its
#                 forward spin to the start of its return spin, with the
#                 motor just left of the rightmost pivot returning first and
#                 the leftmost last.
#
#   Motors 0..(n-2) get 3 phases:
#     Phase 0: forward 360° spin  (position-triggered cascade)
#     Phase 1: idle gap           (timed so return wave cascades right-to-left)
#     Phase 2: return 360° spin
#
#   Motor (n-1) is the turnaround pivot: it performs only the forward 360°
#   and does not spin again at wave reversal.
#
#   Each spin phase is sized to complete exactly one revolution.
# ---------------------------------------------------------------------------

def _domino_ripple(
    connected_motors: int,
    steps_per_rev: int,
    accel_hz_per_s: float,
) -> list[WarmupMotorParams]:
    n = _active_motor_count(connected_motors)

    # "Rapid" rotation: 180 RPM keeps each motor's single revolution quick so
    # the full cascade finishes roughly twice as fast as the original 90 RPM.
    spin_rpm = 180.0
    peak_hz = _hz_for_rpm(spin_rpm, steps_per_rev=steps_per_rev)
    # Faster accel/decel gives the domino cascade a snappier, punchier feel.
    accel_multiplier = 5.0
    effective_accel = accel_hz_per_s * accel_multiplier
    spin_phase = _domino_single_rotation_phase(
        peak_hz=peak_hz,
        accel_hz_per_s=effective_accel,
        steps_per_rev=steps_per_rev,
    )

    # Position trigger threshold: one third of a revolution in steps.
    one_third_rev_steps = max(1, int(round(float(steps_per_rev) / 3.0)))

    # Estimate the cascade delay (time for one motor to reach 1/3 revolution).
    # Used to compute idle gaps for the return wave.
    eff_peak = spin_phase.peak_hz
    eff_accel = spin_phase.accel_hz_per_s
    step_rate = eff_peak * _PULSE_ENGINE_MICROSTEP_RATIO
    if step_rate > 0.0 and eff_accel > 0.0:
        accel_time_s = eff_peak / eff_accel
        accel_steps = 0.5 * step_rate * accel_time_s
        if accel_steps >= one_third_rev_steps:
            # 1/3 rev reached during accel (quadratic step accumulation).
            cascade_s = (2.0 * one_third_rev_steps * accel_time_s / step_rate) ** 0.5
        else:
            # 1/3 rev reached during hold (constant speed).
            cascade_s = accel_time_s + (one_third_rev_steps - accel_steps) / step_rate
        cascade_ms = max(1, int(round(cascade_s * 1000.0)))
    else:
        cascade_ms = 100

    params: list[WarmupMotorParams] = []
    for i in range(connected_motors):
        if i >= n:
            params.append(_idle_motor(accel_hz_per_s))
            continue

        trigger_motor = None if i == 0 else (i - 1)
        trigger_steps = 0 if i == 0 else one_third_rev_steps

        # Turnaround pivot: do not spin twice on the right edge.
        if i == (n - 1):
            params.append(WarmupMotorParams(
                phases=(spin_phase,),
                start_delay_ms=0,
                trigger_motor=trigger_motor,
                trigger_steps=trigger_steps,
            ))
            continue

        # Idle gap so the return wave cascades right-to-left.
        # Motor (n-2) starts the return immediately after the pivot motor
        # completes its single forward spin (gap=0). Each motor further left
        # starts one additional return-cascade interval later.
        gap_ms = max(0, (2 * ((n - 2) - i)) * cascade_ms)

        idle_phase = WarmupPhase(
            peak_hz=0.0,
            accel_hz_per_s=accel_hz_per_s,
            decel_hz_per_s=accel_hz_per_s,
            hold_ms=gap_ms,
        )

        params.append(WarmupMotorParams(
            phases=(spin_phase, idle_phase, spin_phase),
            start_delay_ms=0,
            trigger_motor=trigger_motor,
            trigger_steps=trigger_steps,
        ))
    return params


# ---------------------------------------------------------------------------
# 3. Turbine Spool-Up
#
# Spec:
#   0–2.0 s  All motors start creeping, accelerate together in perfect unison
#            — faster and faster until the pointers are a complete blur.
#   2.0–2.45 s  Sudden simultaneous hard brake.  All six motors stop dead at
#               exactly 12 o'clock (HOME handles the final snap).
#
# Implementation:
#   Single phase.  Accel rate stays long enough to read as a spool-up, but the
#   full gesture now resolves in under 2.5 s.  hold_ms = 0.
# ---------------------------------------------------------------------------

def _turbine_spool_up(
    connected_motors: int,
    steps_per_rev: int,
    accel_hz_per_s: float,
) -> list[WarmupMotorParams]:
    n = _active_motor_count(connected_motors)

    # Blur target: 280 RPM (within safe ceiling) for a faster, punchier spool.
    peak_rpm = 280.0
    peak_hz = _hz_for_rpm(peak_rpm, steps_per_rev=steps_per_rev)

    # Spool-up over ~2.0 s.
    spool_accel = peak_hz / 2.0

    # Hard brake over ~0.45 s.
    spool_decel = peak_hz / 0.45

    params: list[WarmupMotorParams] = []
    for i in range(connected_motors):
        if i >= n:
            params.append(_idle_motor(accel_hz_per_s))
            continue
        params.append(WarmupMotorParams(
            phases=(WarmupPhase(
                peak_hz=peak_hz,
                accel_hz_per_s=spool_accel,
                decel_hz_per_s=spool_decel,
                hold_ms=0,
            ),),
            start_delay_ms=0,
        ))
    return params


# ---------------------------------------------------------------------------
# 4. Center Splash
#
# Spec:
#   0–1.2 s  Center pair (motors 2,3) → next out (1,4) → outer edges (0,5).
#            Each pair does a synchronized 360° spin.
#   1.2–2.5 s  Reverse: outer (0,5) → middle (1,4) → center (2,3).
#
# Implementation:
#   Each motor gets 3 phases:
#     Phase 0: outward-wave spin (start_delay_ms based on outward position).
#     Phase 1: silent idle gap (hold_ms = time until inward wave turn).
#     Phase 2: inward-wave spin.
#
#   Motors in the same outward group share the same start_delay_ms for phase 0
#   and therefore finish phase 0 simultaneously.  Phase 1 bridges from phase 0
#   end to the inward wave start time for this pair.  Phase 2 is the spin.
# ---------------------------------------------------------------------------

def _center_splash(
    connected_motors: int,
    steps_per_rev: int,
    accel_hz_per_s: float,
) -> list[WarmupMotorParams]:
    n = _active_motor_count(connected_motors)

    # One 360° spin per slot at a brisker speed, paced so the outward and
    # inward splashes both read clearly inside a sub-3-second envelope.
    spin_rpm = 160.0
    peak_hz = _hz_for_rpm(spin_rpm, steps_per_rev=steps_per_rev)
    group_delay_ms = 360  # center -> middle -> outer, then reverse at the same cadence
    desired_accel_ms = max(110, int(round(group_delay_ms * 0.5)))
    accel_rate = max(accel_hz_per_s, peak_hz / (desired_accel_ms / 1000.0))

    accel_time_s = peak_hz / accel_rate if accel_rate > 0.0 else 0.0
    accel_time_ms = max(0, int(round(accel_time_s * 1000.0)))
    decel_time_ms = accel_time_ms
    # hold_ms so active spin covers one full revolution.  Use the exact
    # floating-point accel_time_s for the step calculation rather than the
    # rounded accel_time_ms — otherwise the ramp-step estimate diverges from
    # the firmware's actual ramp and the rounding error cascades into hold_ms.
    step_rate = peak_hz * _PULSE_ENGINE_MICROSTEP_RATIO
    ramp_steps = 0.5 * step_rate * accel_time_s
    hold_steps = max(0.0, float(steps_per_rev) - 2.0 * ramp_steps)
    hold_ms = max(0, int(round((hold_steps / step_rate) * 1000.0))) if step_rate > 0 else 0

    phase_duration_ms = accel_time_ms + hold_ms + decel_time_ms

    # Outward start delays for each motor (0-indexed, 6 motors).
    # Center pair = motors 2,3 → outward group 0 (delay 0).
    # Middle pair = motors 1,4 → outward group 1 (delay 1*group_delay).
    # Outer pair  = motors 0,5 → outward group 2 (delay 2*group_delay).
    outward_group = {0: 2, 1: 1, 2: 0, 3: 0, 4: 1, 5: 2}
    outward_start = {i: outward_group.get(i, 0) * group_delay_ms for i in range(6)}
    outward_end = {i: outward_start[i] + phase_duration_ms for i in range(6)}

    # Inward wave turns back early so the full ripple resolves quickly.
    inward_wave_start_ms = 1200

    # Inward start delays relative to inward_wave_start_ms:
    # Outer pair (0,5) → inward group 0 (delay 0 into inward wave).
    # Middle pair (1,4) → inward group 1.
    # Center pair (2,3) → inward group 2.
    inward_group = {0: 0, 1: 1, 2: 2, 3: 2, 4: 1, 5: 0}
    inward_abs_start = {i: inward_wave_start_ms + inward_group.get(i, 0) * group_delay_ms
                        for i in range(6)}

    spin_phase = WarmupPhase(
        peak_hz=peak_hz,
        accel_hz_per_s=accel_rate,
        decel_hz_per_s=accel_rate,
        hold_ms=hold_ms,
    )

    params: list[WarmupMotorParams] = []
    for i in range(connected_motors):
        if i >= n:
            params.append(_idle_motor(accel_hz_per_s))
            continue

        # Gap between end of outward spin and start of inward spin.
        gap_ms = max(0, inward_abs_start[i] - outward_end[i])

        idle_phase = WarmupPhase(
            peak_hz=0.0,
            accel_hz_per_s=accel_hz_per_s,
            decel_hz_per_s=accel_hz_per_s,
            hold_ms=gap_ms,
        )

        params.append(WarmupMotorParams(
            phases=(spin_phase, idle_phase, spin_phase),
            start_delay_ms=outward_start[i],
        ))
    return params


# ---------------------------------------------------------------------------
# 5. Phase Alignment
#
# Spec:
#   0–1.7 s  All motors start simultaneously at gradient speeds:
#          motor 0 = 30 RPM, motor 1 = 60, ... motor 5 = 180 RPM.
#          Pointers fall out of phase → churning visual chaos.
#   1.7–2.6 s  Speeds snap toward a shared RPM.  Spin in sync for one full
#              rotation, then stop upright together.
#
# Implementation:
#   Two phases per motor.
#   Phase 0: gradient speed, hold for ~3 s total (accounting for accel/decel).
#   Phase 1: convergence speed (120 RPM), hold for one revolution, then decel.
#   Fast decel at end of phase 0 (< 100 ms) keeps the "stop" between phases
#   short so it reads as a speed snap rather than a full stop.
#   All motors start at t=0 with start_delay_ms=0.
# ---------------------------------------------------------------------------

def _phase_alignment(
    connected_motors: int,
    steps_per_rev: int,
    accel_hz_per_s: float,
) -> list[WarmupMotorParams]:
    n = _active_motor_count(connected_motors)

    # Spec: motor i at (i+1)*30 RPM, 6 motors → 30, 60, 90, 120, 150, 180 RPM.
    gradient_rpm = [30.0 * (i + 1) for i in range(6)]

    # Convergence: 120 RPM.
    convergence_rpm = 120.0
    convergence_hz = _hz_for_rpm(convergence_rpm, steps_per_rev=steps_per_rev)

    # Shorter chaos window so the effect lands quickly without losing the
    # visible phase divergence.
    phase0_total_ms = 1700

    # Fast decel at end of phase 0 so the gap before phase 1 is short.
    # Target decel ≈ 70 ms at convergence_hz-level speeds.
    fast_decel = convergence_hz * 14.0  # very fast

    # Phase 1: converge quickly, hold one full synchronized revolution, then a
    # crisp coordinated stop.
    step_rate_conv = convergence_hz * _PULSE_ENGINE_MICROSTEP_RATIO
    one_rev_ms = int(round((float(steps_per_rev) / step_rate_conv) * 1000.0)) if step_rate_conv > 0 else 500
    phase1_total_ms = 1050
    conv_decel_ms = 150
    conv_accel_ms = max(100, phase1_total_ms - one_rev_ms - conv_decel_ms)
    conv_accel = max(accel_hz_per_s, convergence_hz / (conv_accel_ms / 1000.0))
    conv_decel = max(convergence_hz / (conv_decel_ms / 1000.0), conv_accel * 1.5)
    conv_hold_ms = one_rev_ms

    params: list[WarmupMotorParams] = []
    for i in range(connected_motors):
        if i >= n:
            params.append(_idle_motor(accel_hz_per_s))
            continue

        rpm0 = gradient_rpm[i]
        hz0 = _hz_for_rpm(rpm0, steps_per_rev=steps_per_rev)

        # Accel/decel times for phase 0.
        accel0_ms = int(round((hz0 / accel_hz_per_s) * 1000.0))
        decel0_ms = int(round((hz0 / fast_decel) * 1000.0))

        # hold_ms for phase 0 so total ≈ phase0_total_ms.
        hold0_ms = max(0, phase0_total_ms - accel0_ms - decel0_ms)

        phase0 = WarmupPhase(
            peak_hz=hz0,
            accel_hz_per_s=accel_hz_per_s,
            decel_hz_per_s=fast_decel,
            hold_ms=hold0_ms,
        )
        phase1 = WarmupPhase(
            peak_hz=convergence_hz,
            accel_hz_per_s=conv_accel,
            decel_hz_per_s=conv_decel,
            hold_ms=conv_hold_ms,
        )

        params.append(WarmupMotorParams(
            phases=(phase0, phase1),
            start_delay_ms=0,
        ))
    return params


# ---------------------------------------------------------------------------
# 6. Chord Bloom
#
# Spec:
#   Over ~2.5 s, all six motors join one at a time to build a single
#   sustained chord. Earlier motors ring longer; the last voice arrives near
#   the tail and the whole stack resolves together at HOME.
#
# Implementation:
#   Single active phase per motor. Timed start delays add one new voice every
#   400 ms. The six voices form a low-to-high Cmaj7 spread
#   (C, G, C, E, G, B) that stays inside the firmware-safe pulse ceiling.
#   Each phase is sized to the nearest whole revolution so step-motion warmups
#   preserve the intended timing envelope.
# ---------------------------------------------------------------------------

def _chord_bloom(
    connected_motors: int,
    steps_per_rev: int,
    accel_hz_per_s: float,
) -> list[WarmupMotorParams]:
    n = _active_motor_count(connected_motors)
    chord_hz = (58.27, 87.31, 116.54, 146.83, 174.61, 220.00)
    entry_delay_ms = 300
    target_end_ms = 2100
    bloom_accel = accel_hz_per_s * 4.0
    bloom_decel = bloom_accel * 1.25

    params: list[WarmupMotorParams] = []
    for i in range(connected_motors):
        if i >= n:
            params.append(_idle_motor(accel_hz_per_s))
            continue

        start_delay_ms = i * entry_delay_ms
        phase = _phase_for_target_duration(
            peak_hz=chord_hz[i],
            accel_hz_per_s=bloom_accel,
            decel_hz_per_s=bloom_decel,
            target_total_ms=max(1000, target_end_ms - start_delay_ms),
            steps_per_rev=steps_per_rev,
        )
        params.append(
            WarmupMotorParams(
                phases=(phase,),
                start_delay_ms=start_delay_ms,
            )
        )
    return params


# ---------------------------------------------------------------------------
# 7. Pentatonic Cascade
#
# Spec:
#   Ascending C-major pentatonic run cascading left to right.  Each motor
#   enters 200 ms after the previous, playing its scale degree.  Motors
#   overlap to build a warm, harmonious chord that swells then decays.
#
# Implementation:
#   Single active phase per motor with staggered start_delay_ms.  Earlier
#   motors ring longer.  Punchy accel keeps the cascading entries crisp.
# ---------------------------------------------------------------------------

def _pentatonic_cascade(
    connected_motors: int,
    steps_per_rev: int,
    accel_hz_per_s: float,
) -> list[WarmupMotorParams]:
    n = _active_motor_count(connected_motors)

    # C major pentatonic: C3, D3, E3, G3, A3, C4
    scale_hz = (130.81, 146.83, 164.81, 196.00, 220.00, 261.63)
    entry_delay_ms = 200
    target_end_ms = 2000
    cascade_accel = accel_hz_per_s * 5.0
    cascade_decel = cascade_accel * 1.2

    params: list[WarmupMotorParams] = []
    for i in range(connected_motors):
        if i >= n:
            params.append(_idle_motor(accel_hz_per_s))
            continue

        start_delay = i * entry_delay_ms
        ring_ms = max(800, target_end_ms - start_delay)
        phase = _phase_for_target_duration(
            peak_hz=scale_hz[i],
            accel_hz_per_s=cascade_accel,
            decel_hz_per_s=cascade_decel,
            target_total_ms=ring_ms,
            steps_per_rev=steps_per_rev,
        )
        params.append(WarmupMotorParams(
            phases=(phase,),
            start_delay_ms=start_delay,
        ))
    return params


# ---------------------------------------------------------------------------
# 8. Whole Tone Shimmer
#
# Spec:
#   All six motors play whole-tone scale notes simultaneously, building an
#   ethereal, dreamlike chord.  Slow accel gives a gradual swell; moderate
#   hold sustains the shimmer; then decel fades it out.
#
# Implementation:
#   Single phase per motor, all starting at t=0.  Slow accel rate for the
#   distinctive building swell of the whole-tone scale.
# ---------------------------------------------------------------------------

def _whole_tone_shimmer(
    connected_motors: int,
    steps_per_rev: int,
    accel_hz_per_s: float,
) -> list[WarmupMotorParams]:
    n = _active_motor_count(connected_motors)

    # Whole-tone scale: C3, D3, E3, F#3, G#3, A#3
    scale_hz = (130.81, 146.83, 164.81, 185.00, 207.65, 233.08)
    shimmer_accel = accel_hz_per_s * 1.5  # slow build for ethereal swell
    shimmer_decel = accel_hz_per_s * 2.5  # slightly quicker fade
    target_total_ms = 2000

    params: list[WarmupMotorParams] = []
    for i in range(connected_motors):
        if i >= n:
            params.append(_idle_motor(accel_hz_per_s))
            continue

        phase = _phase_for_target_duration(
            peak_hz=scale_hz[i],
            accel_hz_per_s=shimmer_accel,
            decel_hz_per_s=shimmer_decel,
            target_total_ms=target_total_ms,
            steps_per_rev=steps_per_rev,
        )
        params.append(WarmupMotorParams(
            phases=(phase,),
            start_delay_ms=0,
        ))
    return params


# ---------------------------------------------------------------------------
# 9. Blues Stagger
#
# Spec:
#   C blues scale (C, Eb, F, Gb, G, Bb) with entries from the extremes
#   inward.  Outer motors (0 and 5) enter first with root and minor 7th,
#   then middle pair (1 and 4) with minor 3rd and 5th, finally the center
#   pair (2 and 3) with the blue notes (4th and flat 5th).  Punchy accel
#   for a gritty, rhythmic feel.
#
# Implementation:
#   Single phase per motor.  Three entry groups with 250 ms cadence.
# ---------------------------------------------------------------------------

def _blues_stagger(
    connected_motors: int,
    steps_per_rev: int,
    accel_hz_per_s: float,
) -> list[WarmupMotorParams]:
    n = _active_motor_count(connected_motors)

    # C blues: C3, Eb3, F3, Gb3, G3, Bb3
    scale_hz = (130.81, 155.56, 174.61, 185.00, 196.00, 233.08)
    # Entry from extremes inward: outer pair -> middle pair -> center pair.
    entry_group = {0: 0, 5: 0, 1: 1, 4: 1, 2: 2, 3: 2}
    group_delay_ms = 250
    target_end_ms = 2000
    blues_accel = accel_hz_per_s * 6.0   # punchy attack
    blues_decel = blues_accel

    params: list[WarmupMotorParams] = []
    for i in range(connected_motors):
        if i >= n:
            params.append(_idle_motor(accel_hz_per_s))
            continue

        group = entry_group.get(i, 0)
        start_delay = group * group_delay_ms
        ring_ms = max(800, target_end_ms - start_delay)
        phase = _phase_for_target_duration(
            peak_hz=scale_hz[i],
            accel_hz_per_s=blues_accel,
            decel_hz_per_s=blues_decel,
            target_total_ms=ring_ms,
            steps_per_rev=steps_per_rev,
        )
        params.append(WarmupMotorParams(
            phases=(phase,),
            start_delay_ms=start_delay,
        ))
    return params


# ---------------------------------------------------------------------------
# 10. Zigzag March
#
# Spec:
#   Exact-step warmup.  Even motors rotate clockwise while odd motors rotate
#   counterclockwise in a synchronized quarter-revolution step.  At the hold
#   the pointers form a zigzag: even at 3 o'clock, odd at 9 o'clock.  Then
#   all motors complete the revolution back to 12 o'clock.
#
# Implementation:
#   Three phases per motor: quick 1/4-rev move, idle hold to admire the
#   zigzag, then a graceful 3/4-rev return.  Direction alternates by motor
#   index (applied by _step_motion_phase_direction).
# ---------------------------------------------------------------------------

def _zigzag_march(
    connected_motors: int,
    steps_per_rev: int,
    accel_hz_per_s: float,
) -> list[WarmupMotorParams]:
    n = _active_motor_count(connected_motors)

    move_accel = accel_hz_per_s * 3.0
    quarter_steps = max(1, round(float(steps_per_rev) / 4.0))
    three_quarter_steps = max(1, steps_per_rev - quarter_steps)

    # Triangular profile: peak_hz = sqrt(target_steps * accel / microstep_ratio)
    out_peak = max(1.0, (quarter_steps * move_accel / _PULSE_ENGINE_MICROSTEP_RATIO) ** 0.5)
    ret_peak = max(1.0, (three_quarter_steps * move_accel / _PULSE_ENGINE_MICROSTEP_RATIO) ** 0.5)

    out_phase = WarmupPhase(
        peak_hz=out_peak,
        accel_hz_per_s=move_accel,
        decel_hz_per_s=move_accel,
        hold_ms=0,
    )
    hold_phase = WarmupPhase(
        peak_hz=0.0,
        accel_hz_per_s=move_accel,
        decel_hz_per_s=move_accel,
        hold_ms=800,
    )
    ret_phase = WarmupPhase(
        peak_hz=ret_peak,
        accel_hz_per_s=move_accel,
        decel_hz_per_s=move_accel,
        hold_ms=0,
    )

    params: list[WarmupMotorParams] = []
    for i in range(connected_motors):
        if i >= n:
            params.append(_idle_motor(accel_hz_per_s))
            continue
        params.append(WarmupMotorParams(
            phases=(out_phase, hold_phase, ret_phase),
            start_delay_ms=0,
        ))
    return params


# ---------------------------------------------------------------------------
# 11. Wave Cascade
#
# Spec:
#   Exact-step warmup.  Each motor sweeps one-third of a revolution, holds
#   briefly, then returns.  Motors are staggered 80 ms apart so the pattern
#   reads as a traveling wave rippling left to right and back.
#
# Implementation:
#   Three phases per motor: 1/3-rev out, idle hold, 2/3-rev return.  All
#   motors share the same forward direction.  start_delay_ms staggers the
#   cascade.
# ---------------------------------------------------------------------------

def _wave_cascade(
    connected_motors: int,
    steps_per_rev: int,
    accel_hz_per_s: float,
) -> list[WarmupMotorParams]:
    n = _active_motor_count(connected_motors)

    move_accel = accel_hz_per_s * 3.0
    third_steps = max(1, round(float(steps_per_rev) / 3.0))
    two_third_steps = max(1, steps_per_rev - third_steps)
    cascade_stagger_ms = 80

    out_peak = max(1.0, (third_steps * move_accel / _PULSE_ENGINE_MICROSTEP_RATIO) ** 0.5)
    ret_peak = max(1.0, (two_third_steps * move_accel / _PULSE_ENGINE_MICROSTEP_RATIO) ** 0.5)

    out_phase = WarmupPhase(
        peak_hz=out_peak,
        accel_hz_per_s=move_accel,
        decel_hz_per_s=move_accel,
        hold_ms=0,
    )
    hold_phase = WarmupPhase(
        peak_hz=0.0,
        accel_hz_per_s=move_accel,
        decel_hz_per_s=move_accel,
        hold_ms=800,
    )
    ret_phase = WarmupPhase(
        peak_hz=ret_peak,
        accel_hz_per_s=move_accel,
        decel_hz_per_s=move_accel,
        hold_ms=0,
    )

    params: list[WarmupMotorParams] = []
    for i in range(connected_motors):
        if i >= n:
            params.append(_idle_motor(accel_hz_per_s))
            continue
        params.append(WarmupMotorParams(
            phases=(out_phase, hold_phase, ret_phase),
            start_delay_ms=i * cascade_stagger_ms,
        ))
    return params


# ---------------------------------------------------------------------------
# 12. Mirror Sweep
#
# Spec:
#   Exact-step warmup.  Left motors (0,1,2) rotate clockwise while right
#   motors (3,4,5) rotate counterclockwise, creating a symmetric fan at the
#   hold: left pointers at 3 o'clock, right pointers at 9 o'clock.  Pairs
#   cascade from the outside in: outer pair (0,5) first, middle (1,4) next,
#   center (2,3) last.
#
# Implementation:
#   Three phases per motor: 1/4-rev out, idle hold, 3/4-rev return.
#   Pair stagger via start_delay_ms.  Direction by motor half.
# ---------------------------------------------------------------------------

def _mirror_sweep(
    connected_motors: int,
    steps_per_rev: int,
    accel_hz_per_s: float,
) -> list[WarmupMotorParams]:
    n = _active_motor_count(connected_motors)

    move_accel = accel_hz_per_s * 3.0
    quarter_steps = max(1, round(float(steps_per_rev) / 4.0))
    three_quarter_steps = max(1, steps_per_rev - quarter_steps)
    pair_stagger_ms = 150
    # Cascade: outer pair first, middle next, center last.
    pair_group = {0: 0, 5: 0, 1: 1, 4: 1, 2: 2, 3: 2}

    out_peak = max(1.0, (quarter_steps * move_accel / _PULSE_ENGINE_MICROSTEP_RATIO) ** 0.5)
    ret_peak = max(1.0, (three_quarter_steps * move_accel / _PULSE_ENGINE_MICROSTEP_RATIO) ** 0.5)

    out_phase = WarmupPhase(
        peak_hz=out_peak,
        accel_hz_per_s=move_accel,
        decel_hz_per_s=move_accel,
        hold_ms=0,
    )
    hold_phase = WarmupPhase(
        peak_hz=0.0,
        accel_hz_per_s=move_accel,
        decel_hz_per_s=move_accel,
        hold_ms=750,
    )
    ret_phase = WarmupPhase(
        peak_hz=ret_peak,
        accel_hz_per_s=move_accel,
        decel_hz_per_s=move_accel,
        hold_ms=0,
    )

    params: list[WarmupMotorParams] = []
    for i in range(connected_motors):
        if i >= n:
            params.append(_idle_motor(accel_hz_per_s))
            continue
        group = pair_group.get(i, 0)
        params.append(WarmupMotorParams(
            phases=(out_phase, hold_phase, ret_phase),
            start_delay_ms=group * pair_stagger_ms,
        ))
    return params


# ---------------------------------------------------------------------------
# 13. Harmonic Series
#
# Spec:
#   All six motors play the first six harmonics of a low fundamental,
#   creating a rich, organ-like resonance.  Frequencies: f, 2f, 3f, 4f,
#   5f, 6f with f=40 Hz.  Slow accel swells like a pipe organ; moderate
#   decel fades out.
#
# Implementation:
#   Single phase per motor, all starting at t=0.  The mathematical
#   relationships between harmonics produce natural consonance distinct
#   from tempered scales.
# ---------------------------------------------------------------------------

def _harmonic_series(
    connected_motors: int,
    steps_per_rev: int,
    accel_hz_per_s: float,
) -> list[WarmupMotorParams]:
    n = _active_motor_count(connected_motors)

    fundamental = 40.0
    scale_hz = tuple(fundamental * (i + 1) for i in range(6))
    swell_accel = accel_hz_per_s * 2.0
    swell_decel = accel_hz_per_s * 3.0
    target_total_ms = 2000

    params: list[WarmupMotorParams] = []
    for i in range(connected_motors):
        if i >= n:
            params.append(_idle_motor(accel_hz_per_s))
            continue

        phase = _phase_for_target_duration(
            peak_hz=scale_hz[i],
            accel_hz_per_s=swell_accel,
            decel_hz_per_s=swell_decel,
            target_total_ms=target_total_ms,
            steps_per_rev=steps_per_rev,
        )
        params.append(WarmupMotorParams(
            phases=(phase,),
            start_delay_ms=0,
        ))
    return params


# ---------------------------------------------------------------------------
# 14. Chromatic Converge
#
# Spec:
#   Two phases.  Phase 0: motors play widely-spaced chromatic pitches for
#   ~1.2 s (dissonant "tuning up" chaos).  Phase 1: all motors snap to a
#   single unison pitch (E3) for ~0.8 s (resolution).  The chaos-to-order
#   moment is the dramatic payoff.
#
# Implementation:
#   Two phases per motor, all starting at t=0.  Fast decel on phase 0 and
#   fast accel on phase 1 give a snappy transition between chaos and
#   resolution.
# ---------------------------------------------------------------------------

def _chromatic_converge(
    connected_motors: int,
    steps_per_rev: int,
    accel_hz_per_s: float,
) -> list[WarmupMotorParams]:
    n = _active_motor_count(connected_motors)

    # Spread pitches (whole-tone for maximum spread within an octave).
    spread_hz = (130.81, 146.83, 155.56, 185.00, 207.65, 233.08)
    unison_hz = 164.81  # E3
    spread_accel = accel_hz_per_s * 2.5
    spread_decel = accel_hz_per_s * 8.0   # fast transition out of chaos
    unison_accel = accel_hz_per_s * 10.0  # snap to unison
    unison_decel = accel_hz_per_s * 3.0

    params: list[WarmupMotorParams] = []
    for i in range(connected_motors):
        if i >= n:
            params.append(_idle_motor(accel_hz_per_s))
            continue

        phase0 = _phase_for_target_duration(
            peak_hz=spread_hz[i],
            accel_hz_per_s=spread_accel,
            decel_hz_per_s=spread_decel,
            target_total_ms=1200,
            steps_per_rev=steps_per_rev,
        )
        phase1 = _phase_for_target_duration(
            peak_hz=unison_hz,
            accel_hz_per_s=unison_accel,
            decel_hz_per_s=unison_decel,
            target_total_ms=800,
            steps_per_rev=steps_per_rev,
        )
        params.append(WarmupMotorParams(
            phases=(phase0, phase1),
            start_delay_ms=0,
        ))
    return params


# ---------------------------------------------------------------------------
# 15. Minor Cascade
#
# Spec:
#   A natural minor scale (A2, B2, C3, D3, E3, F3) cascading right to left.
#   Motor 5 enters first with F3, then 4, 3, 2, 1, and finally motor 0
#   enters with A2.  The descending physical cascade + descending pitch
#   creates a dark, brooding mood — the tonal mirror of pentatonic_cascade.
#
# Implementation:
#   Single phase per motor with staggered start_delay_ms computed from the
#   right-to-left entry order.  Moderate accel for a contemplative build.
# ---------------------------------------------------------------------------

def _minor_cascade(
    connected_motors: int,
    steps_per_rev: int,
    accel_hz_per_s: float,
) -> list[WarmupMotorParams]:
    n = _active_motor_count(connected_motors)

    # A natural minor: A2, B2, C3, D3, E3, F3
    scale_hz = (110.00, 123.47, 130.81, 146.83, 164.81, 174.61)
    entry_delay_ms = 200
    target_end_ms = 2000
    dark_accel = accel_hz_per_s * 2.0
    dark_decel = dark_accel * 1.5

    params: list[WarmupMotorParams] = []
    for i in range(connected_motors):
        if i >= n:
            params.append(_idle_motor(accel_hz_per_s))
            continue

        # Right-to-left: motor (n-1) enters first, motor 0 last.
        start_delay = (n - 1 - i) * entry_delay_ms
        ring_ms = max(800, target_end_ms - start_delay)
        phase = _phase_for_target_duration(
            peak_hz=scale_hz[i],
            accel_hz_per_s=dark_accel,
            decel_hz_per_s=dark_decel,
            target_total_ms=ring_ms,
            steps_per_rev=steps_per_rev,
        )
        params.append(WarmupMotorParams(
            phases=(phase,),
            start_delay_ms=start_delay,
        ))
    return params


# ---------------------------------------------------------------------------
# 16. Gradient Tilt
#
# Spec:
#   Exact-step warmup.  Each motor steps to a progressively larger angle
#   forming an ascending diagonal staircase: motor 0 at 1/7 rev, motor 1
#   at 2/7, ... motor 5 at 6/7.  Hold to admire the tilt, then all motors
#   continue forward to complete their revolution back to 12 o'clock.
#
# Implementation:
#   Three phases per motor: out (variable step count), idle hold, return
#   (completing one revolution).  All motors start simultaneously with the
#   same direction (forward).
# ---------------------------------------------------------------------------

def _gradient_tilt(
    connected_motors: int,
    steps_per_rev: int,
    accel_hz_per_s: float,
) -> list[WarmupMotorParams]:
    n = _active_motor_count(connected_motors)

    move_accel = accel_hz_per_s * 1.5
    hold_ms = 800

    params: list[WarmupMotorParams] = []
    for i in range(connected_motors):
        if i >= n:
            params.append(_idle_motor(accel_hz_per_s))
            continue

        out_steps = max(1, round((i + 1) * float(steps_per_rev) / 7.0))
        ret_steps = max(1, steps_per_rev - out_steps)

        out_peak = max(1.0, (out_steps * move_accel / _PULSE_ENGINE_MICROSTEP_RATIO) ** 0.5)
        ret_peak = max(1.0, (ret_steps * move_accel / _PULSE_ENGINE_MICROSTEP_RATIO) ** 0.5)

        out_phase = WarmupPhase(
            peak_hz=out_peak,
            accel_hz_per_s=move_accel,
            decel_hz_per_s=move_accel,
            hold_ms=0,
        )
        idle_phase = WarmupPhase(
            peak_hz=0.0,
            accel_hz_per_s=move_accel,
            decel_hz_per_s=move_accel,
            hold_ms=hold_ms,
        )
        ret_phase = WarmupPhase(
            peak_hz=ret_peak,
            accel_hz_per_s=move_accel,
            decel_hz_per_s=move_accel,
            hold_ms=0,
        )

        params.append(WarmupMotorParams(
            phases=(out_phase, idle_phase, ret_phase),
            start_delay_ms=0,
        ))
    return params


# ---------------------------------------------------------------------------
# 17. Pendulum Sync
#
# Spec:
#   Exact-step warmup.  All motors swing forward in unison to 6 o'clock
#   (half revolution), hold, then swing in REVERSE back to 12.  The
#   reversal creates a satisfying pendulum feel — six pointers swinging
#   down together and then back up in perfect synchronisation.
#
# Implementation:
#   Three phases per motor: forward half-rev, idle hold, reverse half-rev.
#   Direction reversal is applied by _step_motion_phase_direction for the
#   last active spin.
# ---------------------------------------------------------------------------

def _pendulum_sync(
    connected_motors: int,
    steps_per_rev: int,
    accel_hz_per_s: float,
) -> list[WarmupMotorParams]:
    n = _active_motor_count(connected_motors)

    move_accel = accel_hz_per_s * 2.0
    half_steps = max(1, round(float(steps_per_rev) / 2.0))
    peak_hz = max(1.0, (half_steps * move_accel / _PULSE_ENGINE_MICROSTEP_RATIO) ** 0.5)

    swing_phase = WarmupPhase(
        peak_hz=peak_hz,
        accel_hz_per_s=move_accel,
        decel_hz_per_s=move_accel,
        hold_ms=0,
    )
    hold_phase = WarmupPhase(
        peak_hz=0.0,
        accel_hz_per_s=move_accel,
        decel_hz_per_s=move_accel,
        hold_ms=800,
    )

    params: list[WarmupMotorParams] = []
    for i in range(connected_motors):
        if i >= n:
            params.append(_idle_motor(accel_hz_per_s))
            continue
        params.append(WarmupMotorParams(
            phases=(swing_phase, hold_phase, swing_phase),
            start_delay_ms=0,
        ))
    return params


# ---------------------------------------------------------------------------
# 18. Scatter Bloom
#
# Spec:
#   Exact-step warmup.  Motors bloom outward to golden-angle positions —
#   each motor separated by 137.5° (the golden angle), creating a
#   naturally aesthetic, maximally-spread arrangement (like sunflower
#   seeds).  Hold to admire the scatter, then all continue forward to
#   complete their revolution back to 12 o'clock.
#
# Implementation:
#   Three phases per motor: out (variable golden-angle step count), idle
#   hold, return (completing one revolution).  All start simultaneously.
# ---------------------------------------------------------------------------

def _scatter_bloom(
    connected_motors: int,
    steps_per_rev: int,
    accel_hz_per_s: float,
) -> list[WarmupMotorParams]:
    n = _active_motor_count(connected_motors)

    move_accel = accel_hz_per_s * 1.5
    hold_ms = 800
    golden_angle = 137.508  # degrees

    params: list[WarmupMotorParams] = []
    for i in range(connected_motors):
        if i >= n:
            params.append(_idle_motor(accel_hz_per_s))
            continue

        angle_deg = ((i + 1) * golden_angle) % 360.0
        out_steps = max(1, round(angle_deg / 360.0 * float(steps_per_rev)))
        ret_steps = max(1, steps_per_rev - out_steps)

        out_peak = max(1.0, (out_steps * move_accel / _PULSE_ENGINE_MICROSTEP_RATIO) ** 0.5)
        ret_peak = max(1.0, (ret_steps * move_accel / _PULSE_ENGINE_MICROSTEP_RATIO) ** 0.5)

        out_phase = WarmupPhase(
            peak_hz=out_peak,
            accel_hz_per_s=move_accel,
            decel_hz_per_s=move_accel,
            hold_ms=0,
        )
        idle_phase = WarmupPhase(
            peak_hz=0.0,
            accel_hz_per_s=move_accel,
            decel_hz_per_s=move_accel,
            hold_ms=hold_ms,
        )
        ret_phase = WarmupPhase(
            peak_hz=ret_peak,
            accel_hz_per_s=move_accel,
            decel_hz_per_s=move_accel,
            hold_ms=0,
        )

        params.append(WarmupMotorParams(
            phases=(out_phase, idle_phase, ret_phase),
            start_delay_ms=0,
        ))
    return params


# ---------------------------------------------------------------------------
# Builder dispatch table
# ---------------------------------------------------------------------------

_BUILDERS: dict[WarmupId, Callable[[int, int, float], list[WarmupMotorParams]]] = {
    "slot_machine_lock_in": _slot_machine_lock_in,
    "domino_ripple":        _domino_ripple,
    "turbine_spool_up":     _turbine_spool_up,
    "center_splash":        _center_splash,
    "phase_alignment":      _phase_alignment,
    "chord_bloom":          _chord_bloom,
    "pentatonic_cascade":   _pentatonic_cascade,
    "whole_tone_shimmer":   _whole_tone_shimmer,
    "blues_stagger":        _blues_stagger,
    "zigzag_march":         _zigzag_march,
    "wave_cascade":         _wave_cascade,
    "mirror_sweep":         _mirror_sweep,
    "harmonic_series":      _harmonic_series,
    "chromatic_converge":   _chromatic_converge,
    "minor_cascade":        _minor_cascade,
    "gradient_tilt":        _gradient_tilt,
    "pendulum_sync":        _pendulum_sync,
    "scatter_bloom":        _scatter_bloom,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_warmup_params(
    sequence: Sequence[str],
    *,
    connected_motors: int,
    steps_per_rev: int,
    motor_order: Sequence[int] | None = None,
    speed_multipliers: Mapping[str, float] | None = None,
    max_accel_hz_per_s: float = 500.0,
) -> list[list[WarmupMotorParams]]:
    """Build per-motor multi-phase warmup profiles for a warmup sequence.

    Returns a list of per-routine parameter lists.  Each inner list has one
    ``WarmupMotorParams`` per connected motor slot (0 … connected_motors-1).
    Routines are executed sequentially — one WARMUP command per routine.

    ``max_accel_hz_per_s`` is the base accel/decel rate passed to each builder.
    Individual routines may scale it internally (e.g. turbine derives its own
    spool rate from peak_hz / target_duration).

    ``speed_multipliers`` scales the ``peak_hz`` of every phase in the named
    routine, capped to a safe maximum.
    """
    if connected_motors < 1 or connected_motors > 8:
        raise ValueError("connected_motors must be in range [1, 8]")
    if steps_per_rev < 1:
        raise ValueError("steps_per_rev must be >= 1")
    if max_accel_hz_per_s <= 0.0:
        raise ValueError("max_accel_hz_per_s must be > 0")
    normalized_order = _normalize_motor_order(
        connected_motors=connected_motors,
        motor_order=motor_order,
    )

    safe_max_hz = _hz_for_rpm(_DEFAULT_MAX_RPM, steps_per_rev=steps_per_rev)

    result: list[list[WarmupMotorParams]] = []
    for raw_id in sequence:
        warmup_id = cast(WarmupId, raw_id)
        try:
            builder = _BUILDERS[warmup_id]
        except KeyError as exc:
            raise ValueError(f"invalid warmup routine: {raw_id}") from exc

        params = builder(connected_motors, steps_per_rev, max_accel_hz_per_s)

        # Apply per-routine speed multiplier.
        speed_factor = 1.0
        if speed_multipliers is not None and raw_id in speed_multipliers:
            speed_factor = float(speed_multipliers[raw_id])
        if speed_factor <= 0.0:
            raise ValueError(f"warmup speed multiplier must be > 0: {raw_id}={speed_factor}")

        if abs(speed_factor - 1.0) > 1e-9 or True:
            # Always re-cap in case any routine exceeds safe_max_hz.
            params = [
                WarmupMotorParams(
                    phases=tuple(
                        WarmupPhase(
                            peak_hz=min(safe_max_hz, max(0.0, ph.peak_hz * speed_factor)),
                            accel_hz_per_s=ph.accel_hz_per_s,
                            decel_hz_per_s=ph.decel_hz_per_s,
                            hold_ms=ph.hold_ms,
                        )
                        for ph in p.phases
                    ),
                    start_delay_ms=p.start_delay_ms,
                    trigger_motor=p.trigger_motor,
                    trigger_steps=p.trigger_steps,
                )
                for p in params
            ]

        if warmup_id == "domino_ripple":
            params = _retune_domino_ripple(params, steps_per_rev=steps_per_rev)

        params = _remap_motor_profiles(
            params,
            connected_motors=connected_motors,
            motor_order=normalized_order,
        )

        result.append(params)

    return result


def _phase_target_steps(phase: WarmupPhase) -> int:
    if phase.peak_hz <= 0.0:
        return 0
    accel_s = (phase.peak_hz / phase.accel_hz_per_s) if phase.accel_hz_per_s > 0.0 else 0.0
    decel_s = (phase.peak_hz / phase.decel_hz_per_s) if phase.decel_hz_per_s > 0.0 else 0.0
    hold_s = max(0.0, float(phase.hold_ms) / 1000.0)
    step_rate = phase.peak_hz * _PULSE_ENGINE_MICROSTEP_RATIO
    total_steps = step_rate * (hold_s + (0.5 * accel_s) + (0.5 * decel_s))
    return max(1, int(round(total_steps)))


def _align_steps_to_nearest_full_revolution(
    target_steps: int,
    *,
    steps_per_rev: int,
) -> int:
    if target_steps <= 0 or steps_per_rev <= 0:
        return target_steps
    revolutions = max(1, int(round(float(target_steps) / float(steps_per_rev))))
    return revolutions * steps_per_rev


def _phase_total_steps(phase: WarmupPhase) -> float:
    if phase.peak_hz <= 0.0:
        return 0.0
    accel_s = (phase.peak_hz / phase.accel_hz_per_s) if phase.accel_hz_per_s > 0.0 else 0.0
    decel_s = (phase.peak_hz / phase.decel_hz_per_s) if phase.decel_hz_per_s > 0.0 else 0.0
    hold_s = max(0.0, float(phase.hold_ms) / 1000.0)
    step_rate = phase.peak_hz * _PULSE_ENGINE_MICROSTEP_RATIO
    return step_rate * (hold_s + (0.5 * accel_s) + (0.5 * decel_s))


def _time_to_steps_in_phase_ms(phase: WarmupPhase, target_steps: float) -> int | None:
    if target_steps <= 0.0:
        return 0
    if phase.peak_hz <= 0.0:
        return None

    peak_hz = phase.peak_hz
    accel_rate = max(0.0, phase.accel_hz_per_s)
    decel_rate = max(0.0, phase.decel_hz_per_s)
    peak_step_rate = peak_hz * _PULSE_ENGINE_MICROSTEP_RATIO
    accel_step_rate = accel_rate * _PULSE_ENGINE_MICROSTEP_RATIO
    decel_step_rate = decel_rate * _PULSE_ENGINE_MICROSTEP_RATIO

    accel_s = (peak_hz / accel_rate) if accel_rate > 0.0 else 0.0
    decel_s = (peak_hz / decel_rate) if decel_rate > 0.0 else 0.0
    hold_s = max(0.0, float(phase.hold_ms) / 1000.0)

    accel_steps = 0.5 * peak_step_rate * accel_s
    hold_steps = peak_step_rate * hold_s
    total_steps = accel_steps + hold_steps + (0.5 * peak_step_rate * decel_s)

    if target_steps > total_steps + 1e-6:
        return None

    if target_steps <= accel_steps + 1e-6:
        if accel_step_rate <= 0.0:
            return 0
        return max(0, int(round(math.sqrt((2.0 * target_steps) / accel_step_rate) * 1000.0)))

    if target_steps <= (accel_steps + hold_steps) + 1e-6:
        if peak_step_rate <= 0.0:
            return max(0, int(round(accel_s * 1000.0)))
        hold_offset_s = (target_steps - accel_steps) / peak_step_rate
        return max(0, int(round((accel_s + hold_offset_s) * 1000.0)))

    decel_offset_steps = max(0.0, target_steps - accel_steps - hold_steps)
    if decel_step_rate <= 0.0:
        return max(0, int(round((accel_s + hold_s) * 1000.0)))
    discriminant = max(0.0, (peak_step_rate * peak_step_rate) - (2.0 * decel_step_rate * decel_offset_steps))
    decel_offset_s = (peak_step_rate - math.sqrt(discriminant)) / decel_step_rate
    return max(0, int(round((accel_s + hold_s + decel_offset_s) * 1000.0)))


def _motor_motion_time_to_steps_ms(params: WarmupMotorParams, target_steps: int) -> int:
    if target_steps <= 0:
        return 0

    elapsed_ms = 0
    remaining_steps = float(target_steps)
    for phase in params.phases:
        phase_steps = _phase_total_steps(phase)
        if phase_steps > 0.0 and remaining_steps <= phase_steps + 1e-6:
            offset_ms = _time_to_steps_in_phase_ms(phase, remaining_steps)
            if offset_ms is not None:
                return elapsed_ms + offset_ms
        remaining_steps -= phase_steps
        elapsed_ms += _phase_total_ms(phase)
    return elapsed_ms


def _resolve_warmup_start_times(
    params: list[WarmupMotorParams],
    *,
    connected_motors: int,
) -> tuple[int, ...]:
    active_count = min(connected_motors, len(params))
    start_times: dict[int, int] = {}
    visiting: set[int] = set()

    def resolve(motor_idx: int) -> int:
        if motor_idx in start_times:
            return start_times[motor_idx]
        if motor_idx in visiting:
            raise ValueError(f"warmup trigger graph contains a cycle at motor {motor_idx}")
        visiting.add(motor_idx)
        motor = params[motor_idx]
        trigger_ready_ms = 0
        if motor.trigger_motor is not None:
            trigger_motor = motor.trigger_motor
            if trigger_motor < 0 or trigger_motor >= active_count:
                raise ValueError(f"warmup trigger motor out of range: {trigger_motor}")
            trigger_ready_ms = resolve(trigger_motor) + _motor_motion_time_to_steps_ms(
                params[trigger_motor],
                motor.trigger_steps,
            )
        start_ms = max(max(0, int(motor.start_delay_ms)), trigger_ready_ms)
        visiting.remove(motor_idx)
        start_times[motor_idx] = start_ms
        return start_ms

    return tuple(resolve(motor_idx) for motor_idx in range(active_count))


def _step_motion_phase_direction(
    *,
    warmup_id: WarmupId,
    motor_idx: int,
    phase_idx: int,
    phase_count: int,
    active_spin_index: int,
    active_spin_count: int,
) -> int:
    if warmup_id in {"domino_ripple", "center_splash", "pendulum_sync"}:
        if active_spin_count >= 2 and active_spin_index == (active_spin_count - 1):
            return -1
        return 1
    if warmup_id in {
        "slot_machine_lock_in", "turbine_spool_up", "chord_bloom",
        "pentatonic_cascade", "whole_tone_shimmer", "blues_stagger",
        "zigzag_march",
        "harmonic_series", "chromatic_converge", "minor_cascade",
    }:
        return -1 if (motor_idx % 2) else 1
    if warmup_id in {"wave_cascade", "gradient_tilt", "scatter_bloom"}:
        return 1
    if warmup_id == "mirror_sweep":
        return -1 if motor_idx >= 3 else 1
    return 1


def _to_step_motion_params(
    params: list[WarmupMotorParams],
    *,
    steps_per_rev: int,
    warmup_id: WarmupId,
) -> list[StepMotionMotorParams]:
    out: list[StepMotionMotorParams] = []
    for motor_idx, p in enumerate(params):
        phases: list[StepMotionPhase] = []
        active_phase_indexes: list[int] = []
        total_active_steps = 0
        active_spin_count = sum(1 for ph in p.phases if ph.peak_hz > 0.0)
        active_spin_index = 0
        for phase_idx, ph in enumerate(p.phases):
            target_steps = _phase_target_steps(ph)
            if ph.peak_hz > 0.0:
                if warmup_id in {"domino_ripple", "center_splash"}:
                    # These routines are authored as exact one-revolution spins
                    # in alternating directions. Snap each active phase to the
                    # nearest full revolution so they land back at home without
                    # relying on a cleanup snap after the routine finishes.
                    target_steps = _align_steps_to_nearest_full_revolution(
                        target_steps,
                        steps_per_rev=steps_per_rev,
                    )
                active_phase_indexes.append(len(phases))
                total_active_steps += target_steps
                direction = _step_motion_phase_direction(
                    warmup_id=warmup_id,
                    motor_idx=motor_idx,
                    phase_idx=phase_idx,
                    phase_count=len(p.phases),
                    active_spin_index=active_spin_index,
                    active_spin_count=active_spin_count,
                )
                active_spin_index += 1
            else:
                direction = 1
            # For active (spinning) phases, hold_ms is a timing-domain
            # concept from the WarmupPhase profile.  In step-motion mode
            # target_steps is the sole authority for distance; carrying a
            # hold_ms value can create a conflicting minimum-hold
            # constraint that causes the firmware to overshoot or
            # undershoot the step target.
            #
            # For idle phases (peak_hz == 0), hold_ms is the only timing
            # mechanism -- it controls how long the motor stays silent
            # (e.g. the gap between a forward and return cascade).
            phase_hold_ms = ph.hold_ms if ph.peak_hz <= 0.0 else 0
            phases.append(
                StepMotionPhase(
                    target_steps=target_steps,
                    peak_hz=ph.peak_hz,
                    accel_hz_per_s=ph.accel_hz_per_s,
                    decel_hz_per_s=ph.decel_hz_per_s,
                    hold_ms=phase_hold_ms,
                    direction=direction,
                )
            )
        if steps_per_rev > 0 and active_phase_indexes:
            remainder = total_active_steps % steps_per_rev
            if remainder != 0:
                last_idx = active_phase_indexes[-1]
                last = phases[last_idx]
                # Keep warmup motion shape close to the original timing by
                # snapping to the *nearest* full-revolution total, instead of
                # always rounding up (which can add a visible extra tail spin).
                up_delta = steps_per_rev - remainder
                down_delta = -remainder
                align_delta = up_delta
                if (last.target_steps + down_delta) >= 1 and abs(down_delta) <= abs(up_delta):
                    align_delta = down_delta
                phases[last_idx] = StepMotionPhase(
                    target_steps=last.target_steps + align_delta,
                    peak_hz=last.peak_hz,
                    accel_hz_per_s=last.accel_hz_per_s,
                    decel_hz_per_s=last.decel_hz_per_s,
                    hold_ms=last.hold_ms,
                    direction=last.direction,
                )
        out.append(
            StepMotionMotorParams(
                phases=tuple(phases),
                start_delay_ms=p.start_delay_ms,
                trigger_motor=p.trigger_motor,
                trigger_steps=p.trigger_steps,
            )
        )
    return out


def build_warmup_step_motion_params(
    sequence: Sequence[str],
    *,
    connected_motors: int,
    steps_per_rev: int,
    motor_order: Sequence[int] | None = None,
    speed_multipliers: Mapping[str, float] | None = None,
    max_accel_hz_per_s: float = 500.0,
) -> list[list[StepMotionMotorParams]]:
    """Build step-targeted warmup motion profiles from warmup routines."""
    normalized_order = _normalize_motor_order(
        connected_motors=connected_motors,
        motor_order=motor_order,
    )
    routines = build_warmup_params(
        sequence,
        connected_motors=connected_motors,
        steps_per_rev=steps_per_rev,
        motor_order=None,
        speed_multipliers=speed_multipliers,
        max_accel_hz_per_s=max_accel_hz_per_s,
    )
    return [
        _remap_step_motion_profiles(
            _to_step_motion_params(
                _retune_for_step_motion(
                    params,
                    steps_per_rev=steps_per_rev,
                    warmup_id=cast(WarmupId, raw_id),
                ),
                steps_per_rev=steps_per_rev,
                warmup_id=cast(WarmupId, raw_id),
            ),
            connected_motors=connected_motors,
            motor_order=normalized_order,
        )
        for raw_id, params in zip(sequence, routines)
    ]

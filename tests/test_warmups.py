from __future__ import annotations

import pytest

from music2.warmups import (
    WARMUP_IDS,
    _retune_for_step_motion,
    build_warmup_params,
    build_warmup_step_motion_params,
)
from music2.protocol import (
    MAX_PAYLOAD,
    StepMotionMotorParams,
    StepMotionPhase,
    WarmupMotorParams,
    WarmupPhase,
    decode_step_motion_payload,
    decode_warmup_payload,
    encode_step_motion_payload,
    encode_warmup_payload,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _max_peak_hz(params: list[WarmupMotorParams]) -> float:
    return max(
        ph.peak_hz
        for p in params
        for ph in p.phases
    )


def _active_params(params: list[WarmupMotorParams]) -> list[WarmupMotorParams]:
    """Return only motors that have at least one non-zero phase."""
    return [p for p in params if any(ph.peak_hz > 0 for ph in p.phases)]


def _approx_phase_steps(phase: WarmupPhase) -> int:
    if phase.peak_hz <= 0.0:
        return 0
    accel_s = (phase.peak_hz / phase.accel_hz_per_s) if phase.accel_hz_per_s > 0.0 else 0.0
    decel_s = (phase.peak_hz / phase.decel_hz_per_s) if phase.decel_hz_per_s > 0.0 else 0.0
    hold_s = max(0.0, float(phase.hold_ms) / 1000.0)
    return max(1, int(round(phase.peak_hz * 16.0 * (hold_s + (0.5 * accel_s) + (0.5 * decel_s)))))


def _phase_total_ms(phase: WarmupPhase) -> int:
    accel_ms = int(round((phase.peak_hz / phase.accel_hz_per_s) * 1000.0)) if phase.accel_hz_per_s > 0.0 else 0
    decel_ms = int(round((phase.peak_hz / phase.decel_hz_per_s) * 1000.0)) if phase.decel_hz_per_s > 0.0 else 0
    return accel_ms + phase.hold_ms + decel_ms


def _step_phase_total_ms(phase: StepMotionPhase) -> int:
    if phase.peak_hz <= 0.0:
        return phase.hold_ms
    accel_ms = int(round((phase.peak_hz / phase.accel_hz_per_s) * 1000.0)) if phase.accel_hz_per_s > 0.0 else 0
    decel_ms = int(round((phase.peak_hz / phase.decel_hz_per_s) * 1000.0)) if phase.decel_hz_per_s > 0.0 else 0
    step_rate = phase.peak_hz * 16.0
    if step_rate <= 0.0:
        return accel_ms + decel_ms
    accel_s = (phase.peak_hz / phase.accel_hz_per_s) if phase.accel_hz_per_s > 0.0 else 0.0
    decel_s = (phase.peak_hz / phase.decel_hz_per_s) if phase.decel_hz_per_s > 0.0 else 0.0
    ramp_steps = step_rate * (0.5 * accel_s + 0.5 * decel_s)
    hold_steps = max(0.0, phase.target_steps - ramp_steps)
    hold_ms = int(round((hold_steps / step_rate) * 1000.0))
    return accel_ms + hold_ms + decel_ms


# ---------------------------------------------------------------------------
# Basic structure tests
# ---------------------------------------------------------------------------

def test_each_warmup_produces_params_for_all_motors() -> None:
    for warmup_id in WARMUP_IDS:
        routines = build_warmup_params([warmup_id], connected_motors=6, steps_per_rev=800)
        assert len(routines) == 1, f"{warmup_id}: expected 1 routine"
        params = routines[0]
        assert len(params) == 6, f"{warmup_id}: expected 6 motor params, got {len(params)}"
        for i, p in enumerate(params):
            assert isinstance(p, WarmupMotorParams), f"{warmup_id} motor {i}: not WarmupMotorParams"
            assert len(p.phases) >= 1, f"{warmup_id} motor {i}: no phases"
            assert len(p.phases) <= 4, f"{warmup_id} motor {i}: too many phases"
            for j, ph in enumerate(p.phases):
                assert isinstance(ph, WarmupPhase), f"{warmup_id} motor {i} phase {j}: not WarmupPhase"
                assert ph.peak_hz >= 0.0
                if ph.peak_hz > 0:
                    assert ph.decel_hz_per_s > 0, (
                        f"{warmup_id} motor {i} phase {j}: peak_hz={ph.peak_hz} but decel=0"
                    )
                assert ph.hold_ms >= 0


def test_each_warmup_stays_within_safe_max_rpm() -> None:
    # 300 RPM at 800 steps/rev, MICROSTEP_RATIO=16: hz = (300/60)*(800/16) = 250 Hz.
    safe_max_hz = 251.0  # small margin for rounding
    for warmup_id in WARMUP_IDS:
        routines = build_warmup_params([warmup_id], connected_motors=6, steps_per_rev=800)
        for params in routines:
            for p in params:
                for ph in p.phases:
                    assert ph.peak_hz <= safe_max_hz, (
                        f"{warmup_id}: peak_hz={ph.peak_hz:.1f} exceeds safe max {safe_max_hz}"
                    )


def test_build_warmup_params_preserves_requested_order() -> None:
    routines = build_warmup_params(
        ["slot_machine_lock_in", "phase_alignment"],
        connected_motors=6,
        steps_per_rev=800,
    )
    assert len(routines) == 2
    assert len(routines[0]) == 6
    assert len(routines[1]) == 6


def test_step_motion_preserves_requested_order_for_all_warmups() -> None:
    order = (4, 2, 1, 3, 0, 5)
    for warmup_id in WARMUP_IDS:
        base = build_warmup_step_motion_params(
            [warmup_id],
            connected_motors=6,
            steps_per_rev=800,
        )[0]
        remapped = build_warmup_step_motion_params(
            [warmup_id],
            connected_motors=6,
            steps_per_rev=800,
            motor_order=order,
        )[0]
        for logical_idx, physical_idx in enumerate(order):
            expected = base[logical_idx]
            actual = remapped[physical_idx]
            assert actual.phases == expected.phases, (
                f"{warmup_id}: physical motor {physical_idx} should inherit logical motor {logical_idx} phases"
            )
            assert actual.start_delay_ms == expected.start_delay_ms
            if expected.trigger_motor is None:
                assert actual.trigger_motor is None
            else:
                assert actual.trigger_motor == order[expected.trigger_motor], (
                    f"{warmup_id}: trigger remap mismatch for logical motor {logical_idx}"
                )
            assert actual.trigger_steps == expected.trigger_steps


# ---------------------------------------------------------------------------
# Scaling tests
# ---------------------------------------------------------------------------

def test_warmup_peak_hz_scales_with_steps_per_rev() -> None:
    low = build_warmup_params(["turbine_spool_up"], connected_motors=6, steps_per_rev=800)
    high = build_warmup_params(["turbine_spool_up"], connected_motors=6, steps_per_rev=1600)
    low_peak = _max_peak_hz(low[0])
    high_peak = _max_peak_hz(high[0])
    assert high_peak > low_peak
    assert (high_peak / low_peak) > 1.8


def test_warmup_peak_hz_scales_with_speed_multiplier() -> None:
    base = build_warmup_params(["domino_ripple"], connected_motors=6, steps_per_rev=800)
    fast = build_warmup_params(
        ["domino_ripple"], connected_motors=6, steps_per_rev=800,
        speed_multipliers={"domino_ripple": 1.5},
    )
    base_peak = _max_peak_hz(base[0])
    fast_peak = _max_peak_hz(fast[0])
    assert fast_peak > base_peak
    assert (fast_peak / base_peak) > 1.3


def test_domino_step_motion_peak_hz_scales_with_speed_multiplier() -> None:
    base = build_warmup_step_motion_params(["domino_ripple"], connected_motors=6, steps_per_rev=800)
    fast = build_warmup_step_motion_params(
        ["domino_ripple"],
        connected_motors=6,
        steps_per_rev=800,
        speed_multipliers={"domino_ripple": 1.5},
    )
    base_peak = max(ph.peak_hz for p in base[0] for ph in p.phases)
    fast_peak = max(ph.peak_hz for p in fast[0] for ph in p.phases)
    assert fast_peak > base_peak
    assert (fast_peak / base_peak) > 1.3


# ---------------------------------------------------------------------------
# Protocol round-trip
# ---------------------------------------------------------------------------

def test_warmup_params_round_trip_through_protocol() -> None:
    for warmup_id in WARMUP_IDS:
        routines = build_warmup_params([warmup_id], connected_motors=6, steps_per_rev=800)
        params = routines[0]
        encoded = encode_warmup_payload(params)
        decoded = decode_warmup_payload(encoded)
        assert len(decoded) == len(params), f"{warmup_id}: motor count mismatch after round-trip"
        for i, (orig, dec) in enumerate(zip(params, decoded)):
            assert len(dec.phases) == len(orig.phases), (
                f"{warmup_id} motor {i}: phase count mismatch"
            )
            assert dec.start_delay_ms == orig.start_delay_ms
            assert dec.trigger_motor == orig.trigger_motor
            assert dec.trigger_steps == orig.trigger_steps
            for j, (oph, dph) in enumerate(zip(orig.phases, dec.phases)):
                assert abs(dph.peak_hz - oph.peak_hz) < 0.15, (
                    f"{warmup_id} motor {i} phase {j}: peak_hz round-trip error "
                    f"{oph.peak_hz} -> {dph.peak_hz}"
                )
                assert dph.hold_ms == oph.hold_ms


def test_step_motion_params_round_trip_through_protocol() -> None:
    for warmup_id in WARMUP_IDS:
        routines = build_warmup_step_motion_params([warmup_id], connected_motors=6, steps_per_rev=800)
        params = routines[0]
        for i, p in enumerate(params):
            assert isinstance(p, StepMotionMotorParams), f"{warmup_id} motor {i}: not StepMotionMotorParams"
            for j, ph in enumerate(p.phases):
                assert isinstance(ph, StepMotionPhase), f"{warmup_id} motor {i} phase {j}: not StepMotionPhase"
                if ph.peak_hz > 0.0:
                    assert ph.target_steps > 0
        encoded = encode_step_motion_payload(params)
        decoded = decode_step_motion_payload(encoded)
        assert len(decoded) == len(params)
        for orig, dec in zip(params, decoded):
            assert dec.start_delay_ms == orig.start_delay_ms
            assert dec.trigger_motor == orig.trigger_motor
            assert dec.trigger_steps == orig.trigger_steps
            assert len(dec.phases) == len(orig.phases)
            for orig_phase, dec_phase in zip(orig.phases, dec.phases):
                assert dec_phase.direction == orig_phase.direction


def test_step_motion_conversion_preserves_structure_and_triggers() -> None:
    for warmup_id in WARMUP_IDS:
        base = build_warmup_params([warmup_id], connected_motors=6, steps_per_rev=800)[0]
        step = build_warmup_step_motion_params([warmup_id], connected_motors=6, steps_per_rev=800)[0]
        assert len(base) == len(step)
        for b, s in zip(base, step):
            assert b.start_delay_ms == s.start_delay_ms
            assert b.trigger_motor == s.trigger_motor
            assert b.trigger_steps == s.trigger_steps
            assert len(b.phases) == len(s.phases)
            for bph, sph in zip(b.phases, s.phases):
                # Active (spinning) phases use hold_ms=0; target_steps
                # is the sole authority for distance.  Idle phases
                # (peak_hz==0) preserve hold_ms as a gap timer.
                if bph.peak_hz <= 0.0:
                    assert sph.hold_ms == bph.hold_ms
                else:
                    assert sph.hold_ms == 0
                if bph.peak_hz <= 0.0:
                    assert sph.target_steps == 0
                else:
                    assert sph.target_steps > 0


def test_step_motion_payload_sizes_fit_protocol_limit_for_all_warmups() -> None:
    for warmup_id in WARMUP_IDS:
        params = build_warmup_step_motion_params([warmup_id], connected_motors=8, steps_per_rev=1600)[0]
        payload = encode_step_motion_payload(params)
        assert len(payload) <= MAX_PAYLOAD


def test_step_motion_alignment_uses_nearest_full_revolution() -> None:
    steps_per_rev = 800
    for warmup_id in WARMUP_IDS:
        base = build_warmup_params([warmup_id], connected_motors=6, steps_per_rev=steps_per_rev)[0]
        exact_source = _retune_for_step_motion(base, steps_per_rev=steps_per_rev, warmup_id=warmup_id)
        step = build_warmup_step_motion_params([warmup_id], connected_motors=6, steps_per_rev=steps_per_rev)[0]
        for b, s in zip(exact_source, step):
            raw_total = sum(_approx_phase_steps(ph) for ph in b.phases if ph.peak_hz > 0.0)
            if raw_total == 0:
                continue
            step_total = sum(ph.target_steps for ph in s.phases if ph.peak_hz > 0.0)
            assert step_total % steps_per_rev == 0
            assert abs(step_total - raw_total) <= (steps_per_rev // 2), (
                f"{warmup_id}: step alignment drift too large ({raw_total} -> {step_total})"
            )


def test_all_warmups_finish_within_three_seconds() -> None:
    for warmup_id in WARMUP_IDS:
        routines = build_warmup_params([warmup_id], connected_motors=6, steps_per_rev=800)
        totals = [
            p.start_delay_ms + sum(_phase_total_ms(ph) for ph in p.phases)
            for p in routines[0]
            if any(ph.peak_hz > 0.0 for ph in p.phases)
        ]
        assert totals, f"{warmup_id}: expected active motor timings"
        assert max(totals) <= 3000, f"{warmup_id}: exceeded 3s warmup budget {totals}"


def test_step_motion_warmups_finish_within_three_seconds() -> None:
    for warmup_id in WARMUP_IDS:
        routines = build_warmup_step_motion_params([warmup_id], connected_motors=6, steps_per_rev=800)
        totals = [
            p.start_delay_ms + sum(_step_phase_total_ms(ph) for ph in p.phases)
            for p in routines[0]
            if any(ph.peak_hz > 0.0 for ph in p.phases)
        ]
        assert totals, f"{warmup_id}: expected active motor timings in step-motion mode"
        assert max(totals) <= 3000, f"{warmup_id}: exceeded 3s step-motion budget {totals}"


def test_step_motion_warmups_land_on_home_orientation() -> None:
    steps_per_rev = 800
    for warmup_id in WARMUP_IDS:
        routines = build_warmup_step_motion_params([warmup_id], connected_motors=6, steps_per_rev=steps_per_rev)
        for motor_idx, params in enumerate(routines[0]):
            active = [ph for ph in params.phases if ph.peak_hz > 0.0]
            if not active:
                continue
            signed_total = sum(ph.direction * ph.target_steps for ph in active)
            assert signed_total % steps_per_rev == 0, (
                f"{warmup_id} motor {motor_idx}: expected home orientation, got signed steps {signed_total}"
            )


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

def test_invalid_warmup_id_raises() -> None:
    with pytest.raises(ValueError, match="invalid warmup routine"):
        build_warmup_params(["not_a_real_warmup"], connected_motors=6, steps_per_rev=800)


def test_invalid_speed_multiplier_raises() -> None:
    with pytest.raises(ValueError):
        build_warmup_params(
            ["domino_ripple"], connected_motors=6, steps_per_rev=800,
            speed_multipliers={"domino_ripple": -0.5},
        )


# ---------------------------------------------------------------------------
# Per-routine behavioural assertions
# ---------------------------------------------------------------------------

def test_slot_machine_has_staggered_stop_times() -> None:
    """Motors should have different hold_ms so they stop sequentially."""
    routines = build_warmup_params(["slot_machine_lock_in"], connected_motors=6, steps_per_rev=800)
    active = _active_params(routines[0])
    hold_times = [p.phases[0].hold_ms for p in active]
    assert len(set(hold_times)) > 1, "slot_machine: all motors have same hold_ms"
    # Motor 0 (left) should lock in first → shortest hold.
    assert hold_times[0] < hold_times[-1], "slot_machine: motor 0 should have shorter hold than motor 5"


def test_slot_machine_respects_custom_motor_order() -> None:
    order = (4, 2, 1, 3, 0, 5)
    routines = build_warmup_params(
        ["slot_machine_lock_in"],
        connected_motors=6,
        steps_per_rev=800,
        motor_order=order,
    )
    params = routines[0]
    hold_times_in_wave_order = [params[motor].phases[0].hold_ms for motor in order]
    assert hold_times_in_wave_order == sorted(hold_times_in_wave_order)


def test_domino_ripple_uses_position_triggers() -> None:
    """Each motor after the first should have a position trigger on the previous motor."""
    routines = build_warmup_params(["domino_ripple"], connected_motors=6, steps_per_rev=800)
    params = routines[0]
    active = _active_params(params)
    trigger_steps = int(round(800.0 / 3.0))
    # Motor 0: no trigger.
    assert active[0].trigger_motor is None, "domino motor 0 should have no trigger"
    # Motors 1–5: trigger on previous motor at ~steps_per_rev/3.
    for i in range(1, len(active)):
        assert active[i].trigger_motor == (i - 1), (
            f"domino motor {i}: expected trigger_motor={i-1}, got {active[i].trigger_motor}"
        )
        assert active[i].trigger_steps == trigger_steps, (
            f"domino motor {i}: expected trigger_steps={trigger_steps}, got {active[i].trigger_steps}"
        )


def test_domino_ripple_remaps_triggers_for_custom_motor_order() -> None:
    order = (4, 2, 1, 3, 0, 5)
    routines = build_warmup_params(
        ["domino_ripple"],
        connected_motors=6,
        steps_per_rev=800,
        motor_order=order,
    )
    params = routines[0]
    trigger_steps = int(round(800.0 / 3.0))
    assert params[order[0]].trigger_motor is None
    for logical_idx in range(1, len(order)):
        phys = order[logical_idx]
        prev_phys = order[logical_idx - 1]
        assert params[phys].trigger_motor == prev_phys
        assert params[phys].trigger_steps == trigger_steps


def test_domino_ripple_turnaround_pivot_spins_once_and_reverse_starts_immediately() -> None:
    routines = build_warmup_params(
        ["domino_ripple"],
        connected_motors=6,
        steps_per_rev=800,
    )
    active = _active_params(routines[0])
    assert len(active) == 6

    pivot_idx = len(active) - 1
    pivot_spin_phases = [ph for ph in active[pivot_idx].phases if ph.peak_hz > 0.0]
    assert len(pivot_spin_phases) == 1, "domino pivot motor should spin only once at turnaround"

    # The motor just left of pivot should return immediately (0 ms gap).
    assert active[pivot_idx - 1].phases[1].hold_ms == 0, (
        "domino: expected immediate turnaround with zero gap after pivot"
    )

    # Remaining motors move leftward with linear multiples of a base return gap.
    base_gap_ms = active[pivot_idx - 2].phases[1].hold_ms
    assert base_gap_ms > 0, "domino: expected positive base return gap"
    for i in range(pivot_idx):
        distance = (pivot_idx - 1) - i
        expected_gap_ms = distance * base_gap_ms
        assert active[i].phases[1].hold_ms == expected_gap_ms, (
            f"domino motor {i}: expected return gap {expected_gap_ms} ms, "
            f"got {active[i].phases[1].hold_ms} ms"
        )


def test_domino_ripple_step_motion_targets_expected_revolutions_with_single_pivot() -> None:
    steps_per_rev = 800
    routines = build_warmup_step_motion_params(
        ["domino_ripple"],
        connected_motors=6,
        steps_per_rev=steps_per_rev,
    )
    active = [p for p in routines[0] if any(ph.peak_hz > 0.0 for ph in p.phases)]
    assert len(active) == 6
    pivot_idx = len(active) - 1
    for i, params in enumerate(active):
        spin_phases = [ph for ph in params.phases if ph.peak_hz > 0.0]
        expected_spin_count = 1 if i == pivot_idx else 2
        assert len(spin_phases) == expected_spin_count, (
            f"domino motor {i}: expected {expected_spin_count} spin phase(s), got {len(spin_phases)}"
        )
        if i != pivot_idx:
            assert spin_phases[-1].direction == -1, f"domino motor {i}: expected return phase to reverse"
        total_steps = sum(ph.target_steps for ph in spin_phases)
        expected_steps = steps_per_rev if i == pivot_idx else (2 * steps_per_rev)
        assert total_steps == expected_steps, (
            f"domino motor {i}: expected {expected_steps} total steps, got {total_steps}"
        )


def test_domino_ripple_keeps_expected_revolutions_when_sped_up() -> None:
    steps_per_rev = 800
    routines = build_warmup_step_motion_params(
        ["domino_ripple"],
        connected_motors=6,
        steps_per_rev=steps_per_rev,
        speed_multipliers={"domino_ripple": 1.6},
    )
    active = [p for p in routines[0] if any(ph.peak_hz > 0.0 for ph in p.phases)]
    assert len(active) == 6
    pivot_idx = len(active) - 1
    for i, params in enumerate(active):
        spin_phases = [ph for ph in params.phases if ph.peak_hz > 0.0]
        expected_spin_count = 1 if i == pivot_idx else 2
        assert len(spin_phases) == expected_spin_count, (
            f"domino motor {i}: expected {expected_spin_count} spin phase(s) at 1.6x speed"
        )
        total_steps = sum(ph.target_steps for ph in spin_phases)
        expected_steps = steps_per_rev if i == pivot_idx else (2 * steps_per_rev)
        assert total_steps == expected_steps, (
            f"domino motor {i}: expected {expected_steps} steps at 1.6x speed, got {total_steps}"
        )


def test_domino_ripple_high_multiplier_respects_accel_cap_and_expected_revolutions() -> None:
    steps_per_rev = 800
    accel_cap = 180.0
    multiplier = 2.5
    routines = build_warmup_params(
        ["domino_ripple"],
        connected_motors=6,
        steps_per_rev=steps_per_rev,
        speed_multipliers={"domino_ripple": multiplier},
        max_accel_hz_per_s=accel_cap,
    )
    active = _active_params(routines[0])
    # The domino ripple applies an internal 5x accel multiplier for snappier cascades.
    effective_accel = accel_cap * 5.0
    peak_cap = ((effective_accel * steps_per_rev) / 16.0) ** 0.5
    pivot_idx = len(active) - 1
    for i, params in enumerate(active):
        spin_phases = [ph for ph in params.phases if ph.peak_hz > 0.0]
        expected_spin_count = 1 if i == pivot_idx else 2
        assert len(spin_phases) == expected_spin_count, (
            f"domino motor {i}: expected {expected_spin_count} spin phase(s)"
        )
        for ph in spin_phases:
            assert ph.accel_hz_per_s == pytest.approx(effective_accel)
            assert ph.decel_hz_per_s == pytest.approx(effective_accel)
            assert ph.peak_hz <= (peak_cap + 1e-6), (
                f"domino motor {i}: peak_hz={ph.peak_hz:.6f} should be <= cap {peak_cap:.6f}"
            )

    step_routines = build_warmup_step_motion_params(
        ["domino_ripple"],
        connected_motors=6,
        steps_per_rev=steps_per_rev,
        speed_multipliers={"domino_ripple": multiplier},
        max_accel_hz_per_s=accel_cap,
    )
    active_step = [p for p in step_routines[0] if any(ph.peak_hz > 0.0 for ph in p.phases)]
    pivot_idx = len(active_step) - 1
    for i, params in enumerate(active_step):
        spin_phases = [ph for ph in params.phases if ph.peak_hz > 0.0]
        expected_spin_count = 1 if i == pivot_idx else 2
        assert len(spin_phases) == expected_spin_count, (
            f"domino motor {i}: expected {expected_spin_count} spin phase(s) under accel cap"
        )
        total_steps = sum(ph.target_steps for ph in spin_phases)
        expected_steps = steps_per_rev if i == pivot_idx else (2 * steps_per_rev)
        assert total_steps == expected_steps, (
            f"domino motor {i}: expected {expected_steps} steps under accel cap, got {total_steps}"
        )


def test_turbine_all_motors_identical_and_no_hold() -> None:
    """Turbine: all motors should be identical and hold_ms should be 0 (pure accel + decel)."""
    routines = build_warmup_params(["turbine_spool_up"], connected_motors=6, steps_per_rev=800)
    active = _active_params(routines[0])
    assert len(active) == 6
    ref = active[0].phases[0]
    for i, p in enumerate(active):
        assert len(p.phases) == 1, f"turbine motor {i}: expected 1 phase"
        ph = p.phases[0]
        assert ph.peak_hz == ref.peak_hz, f"turbine motor {i}: peak_hz differs"
        assert ph.hold_ms == 0, f"turbine motor {i}: hold_ms should be 0"


def test_center_splash_has_three_phases() -> None:
    """Center splash motors should each have 3 phases (outward, gap, inward)."""
    routines = build_warmup_params(["center_splash"], connected_motors=6, steps_per_rev=800)
    active = _active_params(routines[0])
    for i, p in enumerate(active):
        assert len(p.phases) == 3, f"center_splash motor {i}: expected 3 phases, got {len(p.phases)}"
        # Phase 0 and phase 2 are the spin phases (non-zero peak).
        assert p.phases[0].peak_hz > 0, f"center_splash motor {i} phase 0: should be spin"
        assert p.phases[1].peak_hz == 0, f"center_splash motor {i} phase 1: should be idle gap"
        assert p.phases[2].peak_hz > 0, f"center_splash motor {i} phase 2: should be spin"


def test_center_splash_stagger_timing() -> None:
    """Center pair should start first (start_delay=0); outer pair starts last."""
    routines = build_warmup_params(["center_splash"], connected_motors=6, steps_per_rev=800)
    params = routines[0]
    center_delay = params[2].start_delay_ms   # motor 2 (center)
    outer_delay  = params[0].start_delay_ms   # motor 0 (outer)
    assert center_delay < outer_delay, (
        f"center_splash: center motor delay ({center_delay}) should be < outer ({outer_delay})"
    )


def test_phase_alignment_has_two_phases() -> None:
    """Phase alignment motors should each have 2 phases (chaos + convergence)."""
    routines = build_warmup_params(["phase_alignment"], connected_motors=6, steps_per_rev=800)
    active = _active_params(routines[0])
    for i, p in enumerate(active):
        assert len(p.phases) == 2, f"phase_alignment motor {i}: expected 2 phases"


def test_phase_alignment_gradient_speeds() -> None:
    """Phase alignment: motor 0 should have lower phase-0 peak than motor 5."""
    routines = build_warmup_params(["phase_alignment"], connected_motors=6, steps_per_rev=800)
    params = routines[0]
    ph0_motor0 = params[0].phases[0].peak_hz
    ph0_motor5 = params[5].phases[0].peak_hz
    assert ph0_motor0 < ph0_motor5, (
        f"phase_alignment: motor 0 phase 0 ({ph0_motor0:.1f} Hz) should be < "
        f"motor 5 phase 0 ({ph0_motor5:.1f} Hz)"
    )


def test_phase_alignment_convergence_speeds_equal() -> None:
    """Phase alignment phase-1 (convergence) should be the same RPM for all motors."""
    routines = build_warmup_params(["phase_alignment"], connected_motors=6, steps_per_rev=800)
    active = _active_params(routines[0])
    conv_speeds = [p.phases[1].peak_hz for p in active]
    assert len(set(conv_speeds)) == 1, (
        f"phase_alignment: convergence speeds differ: {conv_speeds}"
    )


def test_chord_bloom_builds_staggered_six_voice_chord_in_under_four_seconds() -> None:
    routines = build_warmup_params(["chord_bloom"], connected_motors=6, steps_per_rev=800)
    active = _active_params(routines[0])
    assert len(active) == 6
    assert all(len(p.phases) == 1 for p in active)

    start_delays = [p.start_delay_ms for p in active]
    assert start_delays == [0, 300, 600, 900, 1200, 1500]

    peaks = [p.phases[0].peak_hz for p in active]
    assert peaks == sorted(peaks), f"chord_bloom: expected ascending chord voices, got {peaks}"
    assert peaks == pytest.approx([58.27, 87.31, 116.54, 146.83, 174.61, 220.00], abs=0.01)

    hold_ms = [p.phases[0].hold_ms for p in active]
    assert hold_ms[0] > hold_ms[-1], "chord_bloom: early voices should sustain longer than late voices"

    end_times = [p.start_delay_ms + _phase_total_ms(p.phases[0]) for p in active]
    assert min(end_times) >= 1600, f"chord_bloom: ended too early {end_times}"
    assert max(end_times) <= 2700, f"chord_bloom: exceeded 2.5s target {end_times}"


def test_chord_bloom_step_motion_preserves_staggered_entry_and_single_phase() -> None:
    routines = build_warmup_step_motion_params(["chord_bloom"], connected_motors=6, steps_per_rev=800)
    active = [p for p in routines[0] if any(ph.peak_hz > 0.0 for ph in p.phases)]
    assert len(active) == 6
    assert [p.start_delay_ms for p in active] == [0, 300, 600, 900, 1200, 1500]
    for i, params in enumerate(active):
        assert len(params.phases) == 1, f"chord_bloom motor {i}: expected single active phase"
        phase = params.phases[0]
        assert phase.peak_hz > 0.0
        assert phase.direction == (-1 if (i % 2) else 1)
        assert phase.target_steps % 800 == 0, (
            f"chord_bloom motor {i}: expected full-revolution target, got {phase.target_steps}"
        )


def test_chord_bloom_step_motion_uses_exact_motion_safe_ceiling_and_gentler_ramps() -> None:
    base = build_warmup_params(["chord_bloom"], connected_motors=6, steps_per_rev=800)[0]
    step = build_warmup_step_motion_params(["chord_bloom"], connected_motors=6, steps_per_rev=800)[0]

    base_active = _active_params(base)
    step_active = [p for p in step if any(ph.peak_hz > 0.0 for ph in p.phases)]
    assert len(base_active) == len(step_active) == 6

    base_peaks = [p.phases[0].peak_hz for p in base_active]
    step_peaks = [p.phases[0].peak_hz for p in step_active]
    assert max(base_peaks) == pytest.approx(220.0, abs=0.01)
    assert max(step_peaks) == pytest.approx(180.0, abs=0.01)

    scale = max(step_peaks) / max(base_peaks)
    for base_peak, step_peak in zip(base_peaks, step_peaks):
        assert step_peak == pytest.approx(base_peak * scale, abs=0.02)

    top_phase = step_active[-1].phases[0]
    assert top_phase.accel_hz_per_s == pytest.approx(400.0, abs=0.1)
    assert top_phase.decel_hz_per_s == pytest.approx(180.0 / 0.35, abs=0.2)
    assert top_phase.accel_hz_per_s < base_active[-1].phases[0].accel_hz_per_s
    assert top_phase.decel_hz_per_s < base_active[-1].phases[0].decel_hz_per_s

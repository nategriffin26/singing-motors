from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
import math

from .instrument_profile import FrequencyBand, InstrumentProfile, InstrumentMotorProfile
from .models import (
    CompileOptions,
    CompileReport,
    NoteEvent,
    OverflowMode,
    PlaybackEventGroup,
    PlaybackMotorChange,
    Segment,
)
from .playback_program import playback_plan_from_compile_report

_PLANNER_BEAM_WIDTH = 12
_ROLE_WEIGHT = {
    "melody": 1.0,
    "bass": 0.8,
    "inner": 0.35,
}
_MELODY_TRACK_KEYWORDS = ("melody", "lead", "vocal", "voice", "solo")


class AllocationError(Exception):
    pass


@dataclass(frozen=True)
class _AllocationResult:
    assignments: list[int]
    effective_end_s: list[float]
    stolen_note_count: int
    dropped_note_count: int


@dataclass
class _PlannerState:
    score: float
    active_note_to_motor: dict[int, int]
    free_motors: set[int]
    motor_usage: list[int]
    recent_pitch_motor: dict[int, tuple[int, float]]
    last_started_note_idx_by_motor: list[int | None]
    last_release_s_by_motor: list[float]
    stolen_note_count: int
    dropped_note_count: int
    parent: _PlannerState | None = None
    assigned_note_idx: int | None = None
    assigned_motor_idx: int | None = None
    assigned_end_s: float | None = None
    truncated_note_idx: int | None = None
    truncated_end_s: float | None = None


def _find_preferred_melody_track(notes: list[NoteEvent]) -> int | None:
    counts: dict[int, int] = {}
    for note in notes:
        label = (note.source_track_name or "").strip().lower()
        if not label or not any(keyword in label for keyword in _MELODY_TRACK_KEYWORDS):
            continue
        counts[note.source_track] = counts.get(note.source_track, 0) + 1
    if not counts:
        return None
    return max(counts.items(), key=lambda item: (item[1], -item[0]))[0]


def _classify_note_roles(
    notes: list[NoteEvent],
    *,
    prefer_explicit_melody_track: bool = False,
) -> list[str]:
    roles = ["inner"] * len(notes)
    preferred_melody_track = _find_preferred_melody_track(notes) if prefer_explicit_melody_track else None
    boundary_to_start: dict[float, list[int]] = {}
    boundary_to_end: dict[float, list[int]] = {}
    for idx, note in enumerate(notes):
        boundary_to_start.setdefault(note.start_s, []).append(idx)
        boundary_to_end.setdefault(note.end_s, []).append(idx)

    active: set[int] = set()
    for boundary in sorted(set(boundary_to_start) | set(boundary_to_end)):
        for note_idx in boundary_to_end.get(boundary, []):
            active.discard(note_idx)

        starting = boundary_to_start.get(boundary, [])
        if starting:
            pitch_pool = [notes[idx].transposed_note for idx in active]
            pitch_pool.extend(notes[idx].transposed_note for idx in starting)
            highest = max(pitch_pool)
            lowest = min(pitch_pool)
            for note_idx in starting:
                pitch = notes[note_idx].transposed_note
                if pitch == highest:
                    roles[note_idx] = "melody"
                elif pitch == lowest:
                    roles[note_idx] = "bass"
                else:
                    roles[note_idx] = "inner"

        for note_idx in starting:
            active.add(note_idx)

    if preferred_melody_track is not None:
        for idx, note in enumerate(notes):
            if note.source_track == preferred_melody_track:
                roles[idx] = "melody"

    return roles


def _classify_active_note_role(
    note_idx: int,
    active_note_indices: set[int],
    notes: list[NoteEvent],
    *,
    preferred_melody_track: int | None,
) -> str:
    note = notes[note_idx]
    if preferred_melody_track is not None and note.source_track == preferred_melody_track:
        return "melody"

    comparable = active_note_indices
    if preferred_melody_track is not None:
        comparable = {idx for idx in active_note_indices if notes[idx].source_track != preferred_melody_track}
        if not comparable:
            return "inner"

    highest = max(notes[idx].transposed_note for idx in comparable)
    lowest = min(notes[idx].transposed_note for idx in comparable)
    if note.transposed_note == highest:
        return "melody"
    if note.transposed_note == lowest:
        return "bass"
    return "inner"


def _select_active_melody_note(
    active_note_indices: set[int],
    notes: list[NoteEvent],
    *,
    preferred_melody_track: int | None,
) -> int | None:
    if not active_note_indices:
        return None
    if preferred_melody_track is not None:
        candidates = [idx for idx in active_note_indices if notes[idx].source_track == preferred_melody_track]
        if not candidates:
            return None
    else:
        candidates = list(active_note_indices)
    return max(
        candidates,
        key=lambda idx: (
            notes[idx].transposed_note,
            notes[idx].velocity,
            notes[idx].end_s - notes[idx].start_s,
            -notes[idx].start_s,
            -idx,
        ),
    )


def _apply_melody_doubling(
    *,
    targets: list[float],
    active_note_by_motor: list[int],
    active_notes: set[int],
    assignments: list[int],
    notes: list[NoteEvent],
    connected_motors: int,
    preferred_melody_track: int | None,
) -> None:
    if connected_motors < 2:
        return

    melody_note_idx = _select_active_melody_note(
        active_notes,
        notes,
        preferred_melody_track=preferred_melody_track,
    )
    if melody_note_idx is None:
        return

    primary_motor = assignments[melody_note_idx]
    if primary_motor < 0 or primary_motor >= connected_motors:
        return

    active_assigned = {idx for idx in active_note_by_motor if idx >= 0}
    candidate_motors = [motor_idx for motor_idx in range(connected_motors) if motor_idx != primary_motor]
    if not candidate_motors:
        return

    def _mirror_rank(motor_idx: int) -> tuple[int, int, int, int]:
        current_note_idx = active_note_by_motor[motor_idx]
        if current_note_idx < 0:
            return (0, 0, 0, motor_idx)
        role = _classify_active_note_role(
            current_note_idx,
            active_assigned,
            notes,
            preferred_melody_track=preferred_melody_track,
        )
        role_rank = {"inner": 1, "bass": 2, "melody": 3}[role]
        current_note = notes[current_note_idx]
        return (role_rank, current_note.velocity, current_note.transposed_note, motor_idx)

    mirror_motor = min(candidate_motors, key=_mirror_rank)
    targets[mirror_motor] = notes[melody_note_idx].frequency_hz
    active_note_by_motor[mirror_motor] = melody_note_idx


def _note_priority(note: NoteEvent, role: str) -> float:
    velocity_factor = 0.65 + (max(0, min(127, note.velocity)) / 127.0) * 0.60
    duration_factor = min(1.35, 0.80 + max(0.0, note.end_s - note.start_s) * 1.6)
    return _ROLE_WEIGHT[role] * velocity_factor * duration_factor


def _in_band(freq_hz: float, band: FrequencyBand) -> bool:
    return band.start_hz <= freq_hz <= band.end_hz


def _motor_profile_for_idx(
    instrument_profile: InstrumentProfile | None,
    motor_idx: int,
) -> InstrumentMotorProfile | None:
    if instrument_profile is None or motor_idx >= instrument_profile.motor_count:
        return None
    return instrument_profile.ordered_motors[motor_idx]


def _motor_assignment_delta(
    *,
    note_idx: int,
    motor_idx: int,
    timestamp: float,
    notes: list[NoteEvent],
    sticky_gap_s: float,
    state: _PlannerState,
    base_assignment_scores: list[list[float]],
) -> float:
    note = notes[note_idx]
    score = base_assignment_scores[note_idx][motor_idx]

    recent = state.recent_pitch_motor.get(note.transposed_note)
    if recent is not None:
        recent_motor, released_at = recent
        if recent_motor == motor_idx and (timestamp - released_at) <= sticky_gap_s:
            score += 18.0

    last_started_note_idx = state.last_started_note_idx_by_motor[motor_idx]
    if last_started_note_idx is not None:
        previous = notes[last_started_note_idx]
        pitch_distance = abs(previous.transposed_note - note.transposed_note)
        score -= min(24.0, pitch_distance * 0.55)
        if math.isclose(previous.frequency_hz, note.frequency_hz, rel_tol=1e-9, abs_tol=1e-9):
            score += 10.0

    score -= state.motor_usage[motor_idx] * 0.35

    return score


def _base_assignment_score(
    *,
    note: NoteEvent,
    priority: float,
    motor_idx: int,
    instrument_profile: InstrumentProfile | None,
) -> float:
    score = priority * 100.0
    motor_profile = _motor_profile_for_idx(instrument_profile, motor_idx)
    if motor_profile is None:
        return score

    if note.frequency_hz < motor_profile.resolved_min_hz or note.frequency_hz > motor_profile.resolved_max_hz:
        edge_distance = min(
            abs(note.frequency_hz - motor_profile.resolved_min_hz),
            abs(note.frequency_hz - motor_profile.resolved_max_hz),
        )
        score -= 220.0 + min(80.0, edge_distance * 0.5)

    if (
        note.frequency_hz < motor_profile.resolved_preferred_min_hz
        or note.frequency_hz > motor_profile.resolved_preferred_max_hz
    ):
        score -= 12.0 * motor_profile.weight_sustain_quality

    score -= sum(
        14.0 * band.severity * motor_profile.weight_sustain_quality
        for band in motor_profile.resonance_bands
        if _in_band(note.frequency_hz, band)
    )
    score -= sum(
        28.0 * band.severity * motor_profile.weight_attack_cleanliness
        for band in motor_profile.avoid_bands
        if _in_band(note.frequency_hz, band)
    )

    return score


def _apply_release_gap_penalty(
    *,
    score: float,
    note_idx: int,
    motor_idx: int,
    timestamp: float,
    notes: list[NoteEvent],
    state: _PlannerState,
    instrument_profile: InstrumentProfile | None,
) -> float:
    last_started_note_idx = state.last_started_note_idx_by_motor[motor_idx]
    if last_started_note_idx is None:
        return score

    motor_profile = _motor_profile_for_idx(instrument_profile, motor_idx)
    if motor_profile is None:
        return score

    release_gap_s = max(0.0, timestamp - state.last_release_s_by_motor[motor_idx])
    required_gap_s = (motor_profile.resolved_safe_reverse_min_gap_ms + motor_profile.safe_reverse_margin_ms) / 1000.0
    if release_gap_s >= required_gap_s:
        return score

    previous = notes[last_started_note_idx]
    if math.isclose(previous.frequency_hz, notes[note_idx].frequency_hz, rel_tol=1e-9, abs_tol=1e-9):
        return score
    return score - (16.0 * motor_profile.weight_attack_cleanliness)


def _truncation_penalty(
    *,
    note_idx: int,
    timestamp: float,
    notes: list[NoteEvent],
    priorities: list[float],
) -> float:
    note = notes[note_idx]
    total_duration = max(1e-9, note.end_s - note.start_s)
    remaining_duration = max(0.0, note.end_s - timestamp)
    remaining_ratio = remaining_duration / total_duration
    played_ratio = max(0.0, min(1.0, (timestamp - note.start_s) / total_duration))
    abruptness_penalty = 1.15 if played_ratio < 0.2 else 0.85
    return priorities[note_idx] * (55.0 + remaining_ratio * 75.0) * abruptness_penalty


def _clone_planner_state(state: _PlannerState) -> _PlannerState:
    return _PlannerState(
        score=state.score,
        active_note_to_motor=dict(state.active_note_to_motor),
        free_motors=set(state.free_motors),
        motor_usage=list(state.motor_usage),
        recent_pitch_motor=dict(state.recent_pitch_motor),
        last_started_note_idx_by_motor=list(state.last_started_note_idx_by_motor),
        last_release_s_by_motor=list(state.last_release_s_by_motor),
        stolen_note_count=state.stolen_note_count,
        dropped_note_count=state.dropped_note_count,
        parent=state,
    )


def _materialize_planner_state(state: _PlannerState, notes: list[NoteEvent]) -> tuple[list[int], list[float]]:
    assignments = [-1] * len(notes)
    effective_end_s = [note.end_s for note in notes]

    decision_chain: list[_PlannerState] = []
    cursor: _PlannerState | None = state
    while cursor is not None:
        decision_chain.append(cursor)
        cursor = cursor.parent

    for decision in reversed(decision_chain):
        if decision.assigned_note_idx is not None and decision.assigned_motor_idx is not None:
            assignments[decision.assigned_note_idx] = decision.assigned_motor_idx
        if decision.assigned_note_idx is not None and decision.assigned_end_s is not None:
            effective_end_s[decision.assigned_note_idx] = decision.assigned_end_s
        if decision.truncated_note_idx is not None and decision.truncated_end_s is not None:
            effective_end_s[decision.truncated_note_idx] = decision.truncated_end_s

    return assignments, effective_end_s


def _planner_sort_key(state: _PlannerState) -> tuple[float, float, float]:
    return (
        state.score,
        -float(state.dropped_note_count),
        -float(state.stolen_note_count),
    )


def allocate_notes_cost_based(
    notes: list[NoteEvent],
    connected_motors: int,
    sticky_gap_s: float,
    overflow_mode: OverflowMode,
    *,
    instrument_profile: InstrumentProfile | None = None,
    prefer_explicit_melody_track: bool = False,
    beam_width: int = _PLANNER_BEAM_WIDTH,
    progress_callback: Callable[[int, int], None] | None = None,
) -> _AllocationResult:
    if connected_motors <= 0:
        raise ValueError("connected_motors must be positive")
    if overflow_mode not in {"steal_quietest", "drop_newest", "strict"}:
        raise ValueError(f"invalid overflow_mode: {overflow_mode}")
    if beam_width < 1:
        raise ValueError("beam_width must be >= 1")

    roles = _classify_note_roles(
        notes,
        prefer_explicit_melody_track=prefer_explicit_melody_track,
    )
    priorities = [_note_priority(note, roles[idx]) for idx, note in enumerate(notes)]
    base_assignment_scores = [
        [
            _base_assignment_score(
                note=note,
                priority=priorities[note_idx],
                motor_idx=motor_idx,
                instrument_profile=instrument_profile,
            )
            for motor_idx in range(connected_motors)
        ]
        for note_idx, note in enumerate(notes)
    ]
    initial_state = _PlannerState(
        score=0.0,
        active_note_to_motor={},
        free_motors=set(range(connected_motors)),
        motor_usage=[0 for _ in range(connected_motors)],
        recent_pitch_motor={},
        last_started_note_idx_by_motor=[None for _ in range(connected_motors)],
        last_release_s_by_motor=[-1_000_000.0 for _ in range(connected_motors)],
        stolen_note_count=0,
        dropped_note_count=0,
    )

    events: list[tuple[float, int, int]] = []
    for note_idx, note in enumerate(notes):
        events.append((note.start_s, 1, note_idx))
        events.append((note.end_s, 0, note_idx))
    events.sort(key=lambda item: (item[0], item[1], item[2]))

    beam = [initial_state]
    _total_events = len(events)
    _progress_step = max(1, _total_events // 100)
    for _ev_idx, (timestamp, event_type, note_idx) in enumerate(events):
        if progress_callback is not None and _ev_idx % _progress_step == 0:
            progress_callback(_ev_idx, _total_events)
        if event_type == 0:
            for state in beam:
                motor = state.active_note_to_motor.pop(note_idx, None)
                if motor is None:
                    continue
                state.free_motors.add(motor)
                state.recent_pitch_motor[notes[note_idx].transposed_note] = (motor, timestamp)
                state.last_release_s_by_motor[motor] = timestamp
            continue

        next_states: list[_PlannerState] = []
        for state in beam:
            if state.free_motors:
                for motor_idx in sorted(state.free_motors):
                    candidate = _clone_planner_state(state)
                    candidate.assigned_note_idx = note_idx
                    candidate.assigned_motor_idx = motor_idx
                    candidate.active_note_to_motor[note_idx] = motor_idx
                    candidate.free_motors.remove(motor_idx)
                    candidate.motor_usage[motor_idx] += 1
                    candidate.last_started_note_idx_by_motor[motor_idx] = note_idx
                    candidate.score += _motor_assignment_delta(
                        note_idx=note_idx,
                        motor_idx=motor_idx,
                        timestamp=timestamp,
                        notes=notes,
                        sticky_gap_s=sticky_gap_s,
                        state=state,
                        base_assignment_scores=base_assignment_scores,
                    )
                    candidate.score = _apply_release_gap_penalty(
                        score=candidate.score,
                        note_idx=note_idx,
                        motor_idx=motor_idx,
                        timestamp=timestamp,
                        notes=notes,
                        state=state,
                        instrument_profile=instrument_profile,
                    )
                    next_states.append(candidate)
                continue

            if overflow_mode == "strict":
                continue

            if overflow_mode == "drop_newest":
                candidate = _clone_planner_state(state)
                candidate.assigned_note_idx = note_idx
                candidate.assigned_motor_idx = -1
                candidate.assigned_end_s = notes[note_idx].start_s
                candidate.dropped_note_count += 1
                candidate.score -= priorities[note_idx] * 100.0
                next_states.append(candidate)
                continue

            dropped = _clone_planner_state(state)
            dropped.assigned_note_idx = note_idx
            dropped.assigned_motor_idx = -1
            dropped.assigned_end_s = notes[note_idx].start_s
            dropped.dropped_note_count += 1
            dropped.score -= priorities[note_idx] * 100.0
            next_states.append(dropped)

            for victim_idx, victim_motor in sorted(state.active_note_to_motor.items()):
                candidate = _clone_planner_state(state)
                candidate.active_note_to_motor.pop(victim_idx, None)
                candidate.truncated_note_idx = victim_idx
                candidate.truncated_end_s = max(notes[victim_idx].start_s, timestamp)
                candidate.recent_pitch_motor[notes[victim_idx].transposed_note] = (victim_motor, timestamp)
                candidate.last_release_s_by_motor[victim_motor] = timestamp
                candidate.stolen_note_count += 1

                candidate.assigned_note_idx = note_idx
                candidate.assigned_motor_idx = victim_motor
                candidate.active_note_to_motor[note_idx] = victim_motor
                candidate.motor_usage[victim_motor] += 1
                candidate.last_started_note_idx_by_motor[victim_motor] = note_idx
                candidate.score += _motor_assignment_delta(
                    note_idx=note_idx,
                    motor_idx=victim_motor,
                    timestamp=timestamp,
                    notes=notes,
                    sticky_gap_s=sticky_gap_s,
                    state=state,
                    base_assignment_scores=base_assignment_scores,
                )
                candidate.score = _apply_release_gap_penalty(
                    score=candidate.score,
                    note_idx=note_idx,
                    motor_idx=victim_motor,
                    timestamp=timestamp,
                    notes=notes,
                    state=state,
                    instrument_profile=instrument_profile,
                )
                candidate.score -= _truncation_penalty(
                    note_idx=victim_idx,
                    timestamp=timestamp,
                    notes=notes,
                    priorities=priorities,
                )
                next_states.append(candidate)

        if not next_states:
            raise AllocationError("polyphony exceeds connected motors during allocation")

        next_states.sort(key=_planner_sort_key, reverse=True)
        beam = next_states[:beam_width]

    if progress_callback is not None:
        progress_callback(_total_events, _total_events)
    best = max(beam, key=_planner_sort_key)
    assignments, effective_end_s = _materialize_planner_state(best, notes)
    return _AllocationResult(
        assignments=assignments,
        effective_end_s=effective_end_s,
        stolen_note_count=best.stolen_note_count,
        dropped_note_count=best.dropped_note_count,
    )


def _select_steal_candidate(
    *,
    note_idx: int,
    notes: list[NoteEvent],
    active_note_to_motor: dict[int, int],
) -> int:
    incoming_note = notes[note_idx]
    return min(
        active_note_to_motor,
        key=lambda active_idx: (
            notes[active_idx].velocity,
            -abs(notes[active_idx].transposed_note - incoming_note.transposed_note),
            notes[active_idx].start_s,
            active_note_to_motor[active_idx],
        ),
    )


def allocate_notes_sticky(
    notes: list[NoteEvent],
    connected_motors: int,
    sticky_gap_s: float,
    overflow_mode: OverflowMode,
) -> _AllocationResult:
    if connected_motors <= 0:
        raise ValueError("connected_motors must be positive")
    if overflow_mode not in {"steal_quietest", "drop_newest", "strict"}:
        raise ValueError(f"invalid overflow_mode: {overflow_mode}")

    assignments = [-1] * len(notes)
    effective_end_s = [note.end_s for note in notes]
    stolen_note_count = 0
    dropped_note_count = 0

    events: list[tuple[float, int, int]] = []
    for note_idx, note in enumerate(notes):
        events.append((note.start_s, 1, note_idx))
        events.append((note.end_s, 0, note_idx))

    # End events (0) execute before start events (1) at same timestamp.
    events.sort(key=lambda item: (item[0], item[1], item[2]))

    free_motors = set(range(connected_motors))
    active_note_to_motor: dict[int, int] = {}
    motor_usage = [0 for _ in range(connected_motors)]
    recent_pitch_motor: dict[int, tuple[int, float]] = {}

    for timestamp, event_type, note_idx in events:
        if event_type == 0:
            if note_idx in active_note_to_motor:
                motor = active_note_to_motor.pop(note_idx)
                free_motors.add(motor)
                recent_pitch_motor[notes[note_idx].transposed_note] = (motor, timestamp)
            continue

        if not free_motors:
            if overflow_mode == "strict":
                raise AllocationError("polyphony exceeds connected motors during allocation")

            if overflow_mode == "drop_newest":
                dropped_note_count += 1
                effective_end_s[note_idx] = notes[note_idx].start_s
                continue

            if not active_note_to_motor:
                dropped_note_count += 1
                effective_end_s[note_idx] = notes[note_idx].start_s
                continue

            victim_idx = _select_steal_candidate(
                note_idx=note_idx,
                notes=notes,
                active_note_to_motor=active_note_to_motor,
            )
            victim_motor = active_note_to_motor.pop(victim_idx)
            recent_pitch_motor[notes[victim_idx].transposed_note] = (victim_motor, timestamp)
            effective_end_s[victim_idx] = max(notes[victim_idx].start_s, min(effective_end_s[victim_idx], timestamp))
            stolen_note_count += 1
            free_motors.add(victim_motor)

        note = notes[note_idx]
        preferred_motor: int | None = None
        pitch_key = note.transposed_note
        if pitch_key in recent_pitch_motor:
            candidate_motor, released_at = recent_pitch_motor[pitch_key]
            if candidate_motor in free_motors and (timestamp - released_at) <= sticky_gap_s:
                preferred_motor = candidate_motor

        if preferred_motor is None:
            preferred_motor = min(free_motors, key=lambda motor: (motor_usage[motor], motor))

        assignments[note_idx] = preferred_motor
        active_note_to_motor[note_idx] = preferred_motor
        free_motors.remove(preferred_motor)
        motor_usage[preferred_motor] += 1

    return _AllocationResult(
        assignments=assignments,
        effective_end_s=effective_end_s,
        stolen_note_count=stolen_note_count,
        dropped_note_count=dropped_note_count,
    )


def assign_notes_sticky(
    notes: list[NoteEvent],
    connected_motors: int,
    sticky_gap_s: float,
    overflow_mode: OverflowMode = "steal_quietest",
) -> list[int]:
    allocation = allocate_notes_sticky(
        notes=notes,
        connected_motors=connected_motors,
        sticky_gap_s=sticky_gap_s,
        overflow_mode=overflow_mode,
    )
    return allocation.assignments


def _duplicate_idle(freqs: list[float], cursor: int) -> tuple[int, int]:
    active = [freq for freq in freqs if freq > 0.0]
    if not active:
        return cursor, 0

    duplicates = 0
    active_count = len(active)
    for idx, value in enumerate(freqs):
        if value > 0.0:
            continue
        freqs[idx] = active[cursor % active_count]
        cursor += 1
        duplicates += 1

    return cursor, duplicates


def _estimate_direction_flip_transition_us(
    previous_freq_hz: float,
    target_freq_hz: float,
    *,
    playback_run_accel_hz_per_s: float,
    playback_launch_start_hz: float,
    playback_launch_accel_hz_per_s: float,
    playback_launch_crossover_hz: float,
) -> int:
    """Mirror the firmware's stop-and-restart shape closely enough to flag
    physically tight note windows without suppressing them."""
    previous_hz = max(0.0, previous_freq_hz)
    target_hz = max(0.0, target_freq_hz)
    if target_hz <= 0.0:
        return 0

    decel_us = int(round((previous_hz / playback_launch_accel_hz_per_s) * 1_000_000.0))
    if target_hz <= playback_launch_start_hz:
        accel_us = 0
    elif target_hz <= playback_launch_crossover_hz:
        accel_us = int(
            round(
                ((target_hz - playback_launch_start_hz) / playback_launch_accel_hz_per_s)
                * 1_000_000.0
            )
        )
    else:
        launch_us = int(
            round(
                (
                    (playback_launch_crossover_hz - playback_launch_start_hz)
                    / playback_launch_accel_hz_per_s
                )
                * 1_000_000.0
            )
        )
        run_us = int(
            round(
                ((target_hz - playback_launch_crossover_hz) / playback_run_accel_hz_per_s)
                * 1_000_000.0
            )
        )
        accel_us = launch_us + run_us
    return max(0, decel_us + accel_us)


def _render_targets_for_event_groups(
    *,
    active_notes: set[int],
    assignments: list[int],
    notes: list[NoteEvent],
    connected_motors: int,
    idle_mode: str,
    duplicate_cursor: int,
    melody_doubling_enabled: bool,
    preferred_melody_track: int | None,
) -> tuple[list[float], list[int], int, int]:
    targets = [0.0 for _ in range(connected_motors)]
    active_note_by_motor = [-1 for _ in range(connected_motors)]
    for note_idx in active_notes:
        motor_idx = assignments[note_idx]
        if motor_idx >= 0:
            targets[motor_idx] = notes[note_idx].frequency_hz
            active_note_by_motor[motor_idx] = note_idx

    next_duplicate_cursor = duplicate_cursor
    duplicate_count = 0
    if idle_mode == "duplicate":
        next_duplicate_cursor, duplicate_count = _duplicate_idle(targets, duplicate_cursor)
    if melody_doubling_enabled:
        _apply_melody_doubling(
            targets=targets,
            active_note_by_motor=active_note_by_motor,
            active_notes=active_notes,
            assignments=assignments,
            notes=notes,
            connected_motors=connected_motors,
            preferred_melody_track=preferred_melody_track,
        )
    return targets, active_note_by_motor, next_duplicate_cursor, duplicate_count


def _compile_playback_timeline(
    *,
    notes: list[NoteEvent],
    assignments: list[int],
    effective_end_s: list[float],
    connected_motors: int,
    idle_mode: str,
    flip_direction_on_note_change: bool,
    suppress_tight_direction_flips: bool,
    direction_flip_safety_margin_ms: float,
    direction_flip_cooldown_ms: float,
    playback_run_accel_hz_per_s: float,
    playback_launch_start_hz: float,
    playback_launch_accel_hz_per_s: float,
    playback_launch_crossover_hz: float,
    melody_doubling_enabled: bool,
    preferred_melody_track: int | None,
) -> tuple[list[PlaybackEventGroup], list[Segment], int, int, int, int, int, int]:
    if not notes:
        return [], [], 0, 0, 0, 0, 0, 0

    boundaries = sorted(
        {0.0}
        | {note.start_s for note in notes}
        | {note.end_s for note in notes}
        | {effective_end for effective_end in effective_end_s}
    )
    boundary_to_start: dict[float, list[int]] = {}
    boundary_to_end: dict[float, list[int]] = {}
    for idx, note in enumerate(notes):
        if assignments[idx] < 0:
            continue
        effective_end = max(note.start_s, effective_end_s[idx])
        if effective_end <= note.start_s:
            continue
        boundary_to_start.setdefault(note.start_s, []).append(idx)
        boundary_to_end.setdefault(effective_end, []).append(idx)

    active_notes: set[int] = set()
    has_started_note_for_motor = [False for _ in range(connected_motors)]
    last_started_freq_hz_by_motor: list[float | None] = [None for _ in range(connected_motors)]
    last_event_start_us = 0
    event_groups: list[PlaybackEventGroup] = []
    segments: list[Segment] = []
    direction_flip_requested_count = 0
    direction_flip_applied_count = 0
    direction_flip_suppressed_count = 0
    direction_flip_cooldown_suppressed_count = 0
    motor_change_count = 0
    tight_boundary_warning_count = 0
    safety_margin_us = max(0, int(round(direction_flip_safety_margin_ms * 1000.0)))
    cooldown_us = max(0, int(round(direction_flip_cooldown_ms * 1000.0)))
    duplicate_cursor = 0
    current_targets = [0.0 for _ in range(connected_motors)]
    current_actual_note_by_motor = [-1 for _ in range(connected_motors)]
    last_applied_flip_boundary_us_by_motor: list[int | None] = [None for _ in range(connected_motors)]

    for boundary_idx, current_time in enumerate(boundaries):
        current_boundary_us = int(round(current_time * 1_000_000.0))
        starting_notes = boundary_to_start.get(current_time, [])
        ending_notes = boundary_to_end.get(current_time, [])

        for note_idx in ending_notes:
            active_notes.discard(note_idx)
        for note_idx in starting_notes:
            active_notes.add(note_idx)

        next_targets, next_actual_note_by_motor, next_duplicate_cursor, _duplicate_count = _render_targets_for_event_groups(
            active_notes=active_notes,
            assignments=assignments,
            notes=notes,
            connected_motors=connected_motors,
            idle_mode=idle_mode,
            duplicate_cursor=duplicate_cursor,
            melody_doubling_enabled=melody_doubling_enabled,
            preferred_melody_track=preferred_melody_track,
        )

        changes: list[PlaybackMotorChange] = []
        flip_mask = 0
        for motor_idx in range(connected_motors):
            before_note_idx = current_actual_note_by_motor[motor_idx]
            after_note_idx = next_actual_note_by_motor[motor_idx]
            before_target_hz = current_targets[motor_idx]
            after_target_hz = next_targets[motor_idx]
            actual_note_changed = before_note_idx != after_note_idx
            target_changed = before_target_hz != after_target_hz

            if not actual_note_changed and not target_changed:
                continue

            flip_before_restart = False
            if actual_note_changed and after_note_idx >= 0:
                after_note_freq_hz = notes[after_note_idx].frequency_hz
                last_started_freq_hz = last_started_freq_hz_by_motor[motor_idx]
                note_pitch_changed = (
                    last_started_freq_hz is None
                    or not math.isclose(
                        last_started_freq_hz,
                        after_note_freq_hz,
                        rel_tol=1e-9,
                        abs_tol=1e-9,
                    )
                )
                if (
                    flip_direction_on_note_change
                    and has_started_note_for_motor[motor_idx]
                    and note_pitch_changed
                ):
                    direction_flip_requested_count += 1
                    last_applied_flip_us = last_applied_flip_boundary_us_by_motor[motor_idx]
                    within_cooldown = (
                        last_applied_flip_us is not None
                        and (current_boundary_us - last_applied_flip_us) < cooldown_us
                    )
                    if within_cooldown:
                        direction_flip_suppressed_count += 1
                        direction_flip_cooldown_suppressed_count += 1
                    else:
                        available_us = max(
                            0,
                            int(round((effective_end_s[after_note_idx] - current_time) * 1_000_000.0)),
                        )
                        required_us = _estimate_direction_flip_transition_us(
                            notes[before_note_idx].frequency_hz if before_note_idx >= 0 else 0.0,
                            notes[after_note_idx].frequency_hz,
                            playback_run_accel_hz_per_s=playback_run_accel_hz_per_s,
                            playback_launch_start_hz=playback_launch_start_hz,
                            playback_launch_accel_hz_per_s=playback_launch_accel_hz_per_s,
                            playback_launch_crossover_hz=playback_launch_crossover_hz,
                        )
                        boundary_is_tight = available_us < (required_us + safety_margin_us)
                        if boundary_is_tight:
                            tight_boundary_warning_count += 1
                        if suppress_tight_direction_flips and boundary_is_tight:
                            direction_flip_suppressed_count += 1
                        else:
                            flip_before_restart = True
                            direction_flip_applied_count += 1
                            flip_mask |= 1 << motor_idx
                            last_applied_flip_boundary_us_by_motor[motor_idx] = current_boundary_us
                has_started_note_for_motor[motor_idx] = True
                last_started_freq_hz_by_motor[motor_idx] = after_note_freq_hz

            changes.append(
                PlaybackMotorChange(
                    motor_idx=motor_idx,
                    target_hz=after_target_hz,
                    flip_before_restart=flip_before_restart,
                )
            )

        if changes:
            event_groups.append(
                PlaybackEventGroup(
                    delta_us=current_boundary_us - last_event_start_us,
                    changes=tuple(changes),
                )
            )
            last_event_start_us = current_boundary_us
            motor_change_count += len(changes)

        current_targets = next_targets
        current_actual_note_by_motor = next_actual_note_by_motor
        duplicate_cursor = next_duplicate_cursor

        if boundary_idx + 1 >= len(boundaries):
            continue

        next_boundary_us = int(round(boundaries[boundary_idx + 1] * 1_000_000.0))
        duration_us = next_boundary_us - current_boundary_us
        if duration_us <= 0:
            continue
        segments.append(
            Segment(
                duration_us=duration_us,
                motor_freq_hz=tuple(current_targets),
                direction_flip_mask=flip_mask,
            )
        )

    return (
        event_groups,
        segments,
        direction_flip_requested_count,
        direction_flip_applied_count,
        direction_flip_suppressed_count,
        direction_flip_cooldown_suppressed_count,
        motor_change_count,
        tight_boundary_warning_count,
    )


def compile_segments(
    notes: list[NoteEvent],
    options: CompileOptions,
    *,
    instrument_profile: InstrumentProfile | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
) -> CompileReport:
    if not notes:
        empty_report = CompileReport(
            segments=[],
            assignments=[],
            duplicated_slots=0,
            event_groups=[],
            connected_motors=options.connected_motors,
            overflow_mode=options.overflow_mode,
            effective_end_s=[],
            stolen_note_count=0,
            dropped_note_count=0,
            truncated_note_count=0,
            zero_length_note_count=0,
            adjacent_segments_merged=0,
            short_segments_absorbed=0,
            silence_gaps_bridged=0,
            direction_flip_requested_count=0,
            direction_flip_applied_count=0,
            direction_flip_suppressed_count=0,
            direction_flip_cooldown_suppressed_count=0,
            motor_change_count=0,
            tight_boundary_warning_count=0,
            load_limited_segment_count=0,
            load_limited_note_count=0,
        )
        return replace(empty_report, playback_plan=playback_plan_from_compile_report(empty_report))

    allocation = allocate_notes_cost_based(
        notes=notes,
        connected_motors=options.connected_motors,
        sticky_gap_s=options.sticky_gap_s,
        overflow_mode=options.overflow_mode,
        instrument_profile=instrument_profile,
        prefer_explicit_melody_track=options.melody_doubling_enabled,
        progress_callback=progress_callback,
    )
    assignments = allocation.assignments
    effective_end_s = allocation.effective_end_s
    preferred_melody_track = _find_preferred_melody_track(notes) if options.melody_doubling_enabled else None

    (
        event_groups,
        segments,
        direction_flip_requested_count,
        direction_flip_applied_count,
        direction_flip_suppressed_count,
        direction_flip_cooldown_suppressed_count,
        motor_change_count,
        tight_boundary_warning_count,
    ) = _compile_playback_timeline(
        notes=notes,
        assignments=assignments,
        effective_end_s=effective_end_s,
        connected_motors=options.connected_motors,
        idle_mode=options.idle_mode,
        flip_direction_on_note_change=options.flip_direction_on_note_change,
        suppress_tight_direction_flips=options.suppress_tight_direction_flips,
        direction_flip_safety_margin_ms=options.direction_flip_safety_margin_ms,
        direction_flip_cooldown_ms=options.direction_flip_cooldown_ms,
        playback_run_accel_hz_per_s=options.playback_run_accel_hz_per_s,
        playback_launch_start_hz=options.playback_launch_start_hz,
        playback_launch_accel_hz_per_s=options.playback_launch_accel_hz_per_s,
        playback_launch_crossover_hz=options.playback_launch_crossover_hz,
        melody_doubling_enabled=options.melody_doubling_enabled,
        preferred_melody_track=preferred_melody_track,
    )
    truncated_note_count = 0
    zero_length_note_count = 0
    for idx, note in enumerate(notes):
        if assignments[idx] < 0:
            continue
        effective_end = max(note.start_s, effective_end_s[idx])
        if effective_end < note.end_s:
            truncated_note_count += 1
            if effective_end <= note.start_s:
                zero_length_note_count += 1

    duplicated_slots = 0
    if options.idle_mode == "duplicate":
        active_notes = set()
        duplicate_cursor = 0
        boundaries = sorted(
            {0.0}
            | {note.start_s for note in notes}
            | {max(note.start_s, effective_end_s[idx]) for idx, note in enumerate(notes) if assignments[idx] >= 0}
        )
        boundary_to_start: dict[float, list[int]] = {}
        boundary_to_end: dict[float, list[int]] = {}
        for idx, note in enumerate(notes):
            if assignments[idx] < 0:
                continue
            effective_end = max(note.start_s, effective_end_s[idx])
            if effective_end <= note.start_s:
                continue
            boundary_to_start.setdefault(note.start_s, []).append(idx)
            boundary_to_end.setdefault(effective_end, []).append(idx)
        for current_time in boundaries:
            for note_idx in boundary_to_end.get(current_time, []):
                active_notes.discard(note_idx)
            for note_idx in boundary_to_start.get(current_time, []):
                active_notes.add(note_idx)
            _, _, duplicate_cursor, duplicate_count = _render_targets_for_event_groups(
                active_notes=active_notes,
                assignments=assignments,
                notes=notes,
                connected_motors=options.connected_motors,
                idle_mode=options.idle_mode,
                duplicate_cursor=duplicate_cursor,
                melody_doubling_enabled=options.melody_doubling_enabled,
                preferred_melody_track=preferred_melody_track,
            )
            duplicated_slots += duplicate_count

    compiled = CompileReport(
        segments=segments,
        assignments=assignments,
        duplicated_slots=duplicated_slots,
        event_groups=event_groups,
        connected_motors=options.connected_motors,
        overflow_mode=options.overflow_mode,
        effective_end_s=effective_end_s,
        stolen_note_count=allocation.stolen_note_count,
        dropped_note_count=allocation.dropped_note_count,
        truncated_note_count=truncated_note_count,
        zero_length_note_count=zero_length_note_count,
        adjacent_segments_merged=0,
        short_segments_absorbed=0,
        silence_gaps_bridged=0,
        direction_flip_requested_count=direction_flip_requested_count,
        direction_flip_applied_count=direction_flip_applied_count,
        direction_flip_suppressed_count=direction_flip_suppressed_count,
        direction_flip_cooldown_suppressed_count=direction_flip_cooldown_suppressed_count,
        motor_change_count=motor_change_count,
        tight_boundary_warning_count=tight_boundary_warning_count,
        load_limited_segment_count=0,
        load_limited_note_count=0,
    )
    return replace(compiled, playback_plan=playback_plan_from_compile_report(compiled))

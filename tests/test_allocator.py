from __future__ import annotations

import pytest

from music2.compiler import AllocationError, allocate_notes_sticky, assign_notes_sticky, compile_segments
from music2.instrument_profile import InstrumentMotorProfile, InstrumentProfile
from music2.models import CompileOptions, NoteEvent, PlaybackEventGroup, PlaybackMotorChange


def _note(
    start_s: float,
    end_s: float,
    source_note: int,
    transposed_note: int,
    frequency_hz: float,
    *,
    velocity: int = 100,
    source_track: int = 0,
    source_track_name: str | None = None,
) -> NoteEvent:
    return NoteEvent(
        start_s=start_s,
        end_s=end_s,
        source_note=source_note,
        transposed_note=transposed_note,
        frequency_hz=frequency_hz,
        velocity=velocity,
        channel=0,
        source_track=source_track,
        source_track_name=source_track_name,
    )


def test_sticky_assignment_reuses_recent_motor() -> None:
    notes = [
        _note(0.00, 0.10, 60, 60, 261.6),
        _note(0.12, 0.22, 60, 60, 261.6),
    ]
    assignments = assign_notes_sticky(notes, connected_motors=2, sticky_gap_s=0.05)
    assert assignments[0] == assignments[1]


def test_assignment_steals_quietest_when_polyphony_exceeds_motors() -> None:
    notes = [
        _note(0.00, 0.30, 60, 60, 261.6, velocity=30),
        _note(0.00, 0.30, 64, 64, 329.6, velocity=110),
        _note(0.10, 0.30, 67, 67, 392.0, velocity=90),
    ]
    compiled = compile_segments(
        notes,
        CompileOptions(
            connected_motors=2,
            idle_mode="idle",
            sticky_gap_s=0.05,
        ),
    )
    assert compiled.stolen_note_count == 1
    assert compiled.dropped_note_count == 0
    assert compiled.truncated_note_count == 1
    assert compiled.zero_length_note_count == 0
    assert compiled.effective_end_s[0] == pytest.approx(0.10, abs=1e-6)
    assert compiled.assignments[2] == compiled.assignments[0]


def test_assignment_strict_mode_raises_when_polyphony_exceeds_motors() -> None:
    notes = [
        _note(0.00, 0.30, 60, 60, 261.6),
        _note(0.00, 0.30, 62, 62, 293.7),
        _note(0.00, 0.30, 64, 64, 329.6),
    ]
    with pytest.raises(AllocationError):
        assign_notes_sticky(notes, connected_motors=2, sticky_gap_s=0.05, overflow_mode="strict")


def test_steal_tiebreak_prefers_farthest_pitch() -> None:
    notes = [
        _note(0.00, 0.30, 60, 60, 261.6, velocity=80),
        _note(0.00, 0.30, 70, 70, 466.2, velocity=80),
        _note(0.10, 0.30, 62, 62, 293.7, velocity=80),
    ]
    allocation = allocate_notes_sticky(
        notes,
        connected_motors=2,
        sticky_gap_s=0.05,
        overflow_mode="steal_quietest",
    )
    assert allocation.effective_end_s[1] == pytest.approx(0.10, abs=1e-6)


def test_steal_tiebreak_prefers_oldest_note_when_velocity_and_pitch_distance_match() -> None:
    notes = [
        _note(0.00, 0.30, 60, 60, 261.6, velocity=80),
        _note(0.05, 0.30, 64, 64, 329.6, velocity=80),
        _note(0.10, 0.30, 62, 62, 293.7, velocity=80),
    ]
    allocation = allocate_notes_sticky(
        notes,
        connected_motors=2,
        sticky_gap_s=0.05,
        overflow_mode="steal_quietest",
    )
    assert allocation.effective_end_s[0] == pytest.approx(0.10, abs=1e-6)


def test_steal_tiebreak_prefers_lower_motor_index_when_other_signals_match() -> None:
    notes = [
        _note(0.00, 0.30, 60, 60, 261.6, velocity=80),
        _note(0.00, 0.30, 64, 64, 329.6, velocity=80),
        _note(0.10, 0.30, 62, 62, 293.7, velocity=80),
    ]
    allocation = allocate_notes_sticky(
        notes,
        connected_motors=2,
        sticky_gap_s=0.05,
        overflow_mode="steal_quietest",
    )
    assert allocation.effective_end_s[0] == pytest.approx(0.10, abs=1e-6)


def test_cost_based_compile_drops_inner_note_to_preserve_more_important_voices() -> None:
    notes = [
        _note(0.00, 0.30, 48, 48, 130.8, velocity=110),
        _note(0.00, 0.30, 60, 60, 261.6, velocity=115),
        _note(0.00, 0.30, 72, 72, 523.3, velocity=70),
    ]
    compiled = compile_segments(
        notes,
        CompileOptions(
            connected_motors=2,
            idle_mode="idle",
            sticky_gap_s=0.05,
        ),
    )
    assert compiled.assignments[0] >= 0
    assert compiled.assignments[2] >= 0
    assert compiled.effective_end_s[1] == pytest.approx(notes[1].start_s, abs=1e-9)
    assert compiled.zero_length_note_count == 1
    assert compiled.stolen_note_count == 1


def test_melody_doubling_prefers_explicit_lead_track() -> None:
    notes = [
        _note(0.00, 0.30, 48, 48, 130.8, velocity=90, source_track=1, source_track_name="Bass"),
        _note(0.00, 0.30, 60, 60, 261.6, velocity=100, source_track=2, source_track_name="Lead Melody"),
    ]
    compiled = compile_segments(
        notes,
        CompileOptions(
            connected_motors=3,
            idle_mode="idle",
            sticky_gap_s=0.05,
            melody_doubling_enabled=True,
        ),
    )

    active_freqs = {freq for freq in compiled.segments[0].motor_freq_hz if freq > 0.0}
    assert compiled.segments[0].motor_freq_hz.count(261.6) == 2
    assert 130.8 in active_freqs


def test_melody_doubling_falls_back_to_top_voice_when_no_lead_track_exists() -> None:
    notes = [
        _note(0.00, 0.30, 48, 48, 130.8, velocity=90),
        _note(0.00, 0.30, 72, 72, 523.3, velocity=100),
    ]
    compiled = compile_segments(
        notes,
        CompileOptions(
            connected_motors=2,
            idle_mode="idle",
            sticky_gap_s=0.05,
            melody_doubling_enabled=True,
        ),
    )

    assert compiled.segments[0].motor_freq_hz == (523.3, 523.3)


def test_cost_based_compile_respects_instrument_preferred_bands() -> None:
    profile = InstrumentProfile(
        name="split_range",
        profile_version=1,
        motor_count=2,
        motors=(
            InstrumentMotorProfile(
                motor_idx=0,
                label="low",
                min_hz=15.0,
                max_hz=800.0,
                preferred_min_hz=60.0,
                preferred_max_hz=220.0,
            ),
            InstrumentMotorProfile(
                motor_idx=1,
                label="high",
                min_hz=15.0,
                max_hz=800.0,
                preferred_min_hz=400.0,
                preferred_max_hz=760.0,
            ),
        ),
    )
    notes = [
        _note(0.00, 0.20, 84, 84, 1046.5 / 2.0, velocity=90),
        _note(0.00, 0.20, 48, 48, 130.8, velocity=90),
    ]
    compiled = compile_segments(
        notes,
        CompileOptions(
            connected_motors=2,
            idle_mode="idle",
            sticky_gap_s=0.05,
        ),
        instrument_profile=profile,
    )
    assert compiled.assignments == [1, 0]


def test_drop_newest_mode_marks_dropped_notes() -> None:
    notes = [
        _note(0.00, 0.40, 60, 60, 261.6),
        _note(0.10, 0.30, 64, 64, 329.6),
    ]
    compiled = compile_segments(
        notes,
        CompileOptions(
            connected_motors=1,
            idle_mode="idle",
            overflow_mode="drop_newest",
            sticky_gap_s=0.05,
        ),
    )
    assert compiled.stolen_note_count == 0
    assert compiled.dropped_note_count == 1
    assert compiled.assignments[1] == -1
    assert compiled.effective_end_s[1] == pytest.approx(notes[1].start_s, abs=1e-9)
    assert sum(seg.duration_us for seg in compiled.segments) == 400_000


def test_compile_duplicate_idle_mode_fills_all_motors() -> None:
    notes = [_note(0.00, 0.20, 69, 69, 440.0)]
    compiled = compile_segments(
        notes,
        CompileOptions(
            connected_motors=4,
            idle_mode="duplicate",
            sticky_gap_s=0.05,
        ),
    )
    assert len(compiled.segments) == 1
    freqs = compiled.segments[0].motor_freq_hz
    assert len(freqs) == 4
    assert min(freqs) > 0.0


def test_compile_idle_mode_leaves_idle_zero() -> None:
    notes = [_note(0.00, 0.20, 69, 69, 440.0)]
    compiled = compile_segments(
        notes,
        CompileOptions(
            connected_motors=4,
            idle_mode="idle",
            sticky_gap_s=0.05,
        ),
    )
    freqs = compiled.segments[0].motor_freq_hz
    active_count = sum(1 for freq in freqs if freq > 0.0)
    assert active_count == 1


def test_compile_total_duration_matches_note_span() -> None:
    """Total compiled duration should exactly match the note span in microseconds."""
    notes = [
        _note(0.0, 0.1000001, 60, 60, 261.6),
        _note(0.1000001, 0.2000002, 62, 62, 293.7),
        _note(0.2000002, 0.3000003, 64, 64, 329.6),
    ]
    compiled = compile_segments(
        notes,
        CompileOptions(
            connected_motors=3,
            idle_mode="idle",
            sticky_gap_s=0.05,
        ),
    )
    total_us = sum(seg.duration_us for seg in compiled.segments)
    expected_us = round(0.3000003 * 1_000_000)
    assert total_us == expected_us


def test_compile_report_includes_playback_event_groups() -> None:
    notes = [
        _note(0.0, 0.1, 60, 60, 261.6),
        _note(0.1, 0.2, 62, 62, 293.7),
    ]
    compiled = compile_segments(
        notes,
        CompileOptions(
            connected_motors=1,
            idle_mode="idle",
            sticky_gap_s=0.05,
        ),
    )
    assert compiled.event_groups == [
        PlaybackEventGroup(
            delta_us=0,
            changes=(PlaybackMotorChange(motor_idx=0, target_hz=261.6, flip_before_restart=False),),
        ),
        PlaybackEventGroup(
            delta_us=100_000,
            changes=(PlaybackMotorChange(motor_idx=0, target_hz=293.7, flip_before_restart=False),),
        ),
        PlaybackEventGroup(
            delta_us=100_000,
            changes=(PlaybackMotorChange(motor_idx=0, target_hz=0.0, flip_before_restart=False),),
        ),
    ]
    assert compiled.motor_change_count == 3


def test_event_groups_preserve_same_pitch_reattack_boundary_without_flip() -> None:
    notes = [
        _note(0.0, 0.1, 69, 69, 440.0),
        _note(0.1, 0.2, 69, 69, 440.0),
    ]
    compiled = compile_segments(
        notes,
        CompileOptions(
            connected_motors=1,
            idle_mode="idle",
            sticky_gap_s=0.05,
            flip_direction_on_note_change=True,
        ),
    )
    assert compiled.event_groups[1] == PlaybackEventGroup(
        delta_us=100_000,
        changes=(PlaybackMotorChange(motor_idx=0, target_hz=440.0, flip_before_restart=False),),
    )
    assert compiled.direction_flip_requested_count == 0
    assert compiled.tight_boundary_warning_count == 0


def test_event_groups_ignore_legacy_load_guard_limits() -> None:
    notes = [
        _note(0.0, 0.2, 60, 60, 261.6),
        _note(0.0, 0.2, 64, 64, 329.6),
    ]
    compiled = compile_segments(
        notes,
        CompileOptions(
            connected_motors=2,
            idle_mode="idle",
            sticky_gap_s=0.05,
        ),
    )
    assert compiled.event_groups[0] == PlaybackEventGroup(
        delta_us=0,
        changes=(
            PlaybackMotorChange(motor_idx=0, target_hz=261.6, flip_before_restart=False),
            PlaybackMotorChange(motor_idx=1, target_hz=329.6, flip_before_restart=False),
        ),
    )


def test_shadow_segments_preserve_tiny_idle_boundaries() -> None:
    notes = [
        _note(0.000000, 0.100000, 60, 60, 261.6),
        _note(0.100001, 0.200000, 60, 60, 261.6),
    ]
    compiled = compile_segments(
        notes,
        CompileOptions(
            connected_motors=1,
            idle_mode="idle",
            sticky_gap_s=0.05,
        ),
    )
    assert sum(seg.duration_us for seg in compiled.segments) == 200_000
    assert len(compiled.segments) == 3
    assert compiled.short_segments_absorbed == 0
    assert compiled.adjacent_segments_merged == 0


def test_same_pitch_gap_preserves_explicit_stop_segment() -> None:
    notes = [
        _note(0.000, 0.499, 69, 69, 440.0),
        _note(0.500, 1.000, 69, 69, 440.0),
    ]
    compiled = compile_segments(
        notes,
        CompileOptions(
            connected_motors=1,
            idle_mode="idle",
            sticky_gap_s=0.05,
        ),
    )
    assert compiled.silence_gaps_bridged == 0
    assert len(compiled.segments) == 3
    assert compiled.segments[1].motor_freq_hz[0] == 0.0
    assert sum(seg.duration_us for seg in compiled.segments) == 1_000_000


def test_long_gap_preserves_idle_segment() -> None:
    notes = [
        _note(0.000, 0.400, 69, 69, 440.0),
        _note(0.500, 1.000, 69, 69, 440.0),
    ]
    compiled = compile_segments(
        notes,
        CompileOptions(
            connected_motors=1,
            idle_mode="idle",
            sticky_gap_s=0.15,
        ),
    )
    assert compiled.silence_gaps_bridged == 0
    # Should have at least the silence segment preserved.
    assert any(seg.motor_freq_hz[0] == 0.0 for seg in compiled.segments)


def test_reattack_bridge_only_bridges_matching_frequencies() -> None:
    """A silence gap between DIFFERENT pitches on the same motor should
    NOT be bridged."""
    notes = [
        _note(0.000, 0.499, 60, 60, 261.6),
        _note(0.500, 1.000, 64, 64, 329.6),
    ]
    compiled = compile_segments(
        notes,
        CompileOptions(
            connected_motors=1,
            idle_mode="idle",
            sticky_gap_s=0.05,
        ),
    )
    assert compiled.silence_gaps_bridged == 0


def test_triple_identical_notes_keep_explicit_gap_boundaries() -> None:
    notes = [
        _note(0.000, 0.499, 69, 69, 440.0),
        _note(0.500, 0.999, 69, 69, 440.0),
        _note(1.000, 1.500, 69, 69, 440.0),
    ]
    compiled = compile_segments(
        notes,
        CompileOptions(
            connected_motors=1,
            idle_mode="idle",
            sticky_gap_s=0.05,
        ),
    )
    assert compiled.silence_gaps_bridged == 0
    assert len(compiled.segments) == 5
    assert [seg.motor_freq_hz[0] for seg in compiled.segments] == [440.0, 0.0, 440.0, 0.0, 440.0]


def test_reattack_bridge_disabled_when_zero() -> None:
    """No live-playback reattack bridging should collapse explicit stops."""
    notes = [
        _note(0.000, 0.499, 69, 69, 440.0),
        _note(0.500, 1.000, 69, 69, 440.0),
    ]
    compiled = compile_segments(
        notes,
        CompileOptions(
            connected_motors=1,
            idle_mode="idle",
            sticky_gap_s=0.05,
        ),
    )
    assert compiled.silence_gaps_bridged == 0


def test_flip_direction_ignores_same_pitch_restart_without_bridging() -> None:
    notes = [
        _note(0.000, 0.499, 69, 69, 440.0),
        _note(0.500, 1.000, 69, 69, 440.0),
    ]
    compiled = compile_segments(
        notes,
        CompileOptions(
            connected_motors=1,
            idle_mode="idle",
            sticky_gap_s=0.05,
            flip_direction_on_note_change=True,
        ),
    )

    assert compiled.silence_gaps_bridged == 0
    assert len(compiled.segments) == 3
    assert compiled.segments[2].direction_flip_mask == 0
    assert compiled.direction_flip_requested_count == 0
    assert compiled.direction_flip_applied_count == 0
    assert compiled.direction_flip_suppressed_count == 0


def test_flip_direction_marks_pitch_change_on_same_motor() -> None:
    notes = [
        _note(0.000, 0.500, 60, 60, 261.6),
        _note(0.500, 1.000, 64, 64, 329.6),
    ]
    compiled = compile_segments(
        notes,
        CompileOptions(
            connected_motors=1,
            idle_mode="idle",
            sticky_gap_s=0.05,
            flip_direction_on_note_change=True,
        ),
    )

    assert len(compiled.segments) == 2
    assert compiled.segments[0].direction_flip_mask == 0
    assert compiled.segments[1].direction_flip_mask == 0x01


def test_flip_direction_ignores_duplicate_idle_slots() -> None:
    notes = [_note(0.00, 0.10, 69, 69, 440.0)]
    compiled = compile_segments(
        notes,
        CompileOptions(
            connected_motors=3,
            idle_mode="duplicate",
            sticky_gap_s=0.05,
            flip_direction_on_note_change=True,
        ),
    )

    assert len(compiled.segments) == 1
    assert compiled.segments[0].direction_flip_mask == 0


def test_flip_direction_preserves_same_frequency_boundary_without_flip() -> None:
    notes = [
        _note(0.000, 0.300, 69, 69, 440.0),
        _note(0.300, 0.600, 69, 69, 440.0),
    ]
    compiled = compile_segments(
        notes,
        CompileOptions(
            connected_motors=1,
            idle_mode="idle",
            sticky_gap_s=0.05,
            flip_direction_on_note_change=True,
        ),
    )

    assert len(compiled.segments) == 2
    assert compiled.segments[1].direction_flip_mask == 0


def test_flip_direction_suppresses_tight_window_by_default() -> None:
    notes = [
        _note(0.000, 0.100, 69, 69, 440.0),
        _note(0.100, 0.110, 76, 76, 659.3),
    ]
    compiled = compile_segments(
        notes,
        CompileOptions(
            connected_motors=1,
            idle_mode="idle",
            sticky_gap_s=0.05,
            flip_direction_on_note_change=True,
        ),
    )

    assert len(compiled.segments) == 2
    assert compiled.direction_flip_requested_count == 1
    assert compiled.direction_flip_applied_count == 0
    assert compiled.direction_flip_suppressed_count == 1
    assert compiled.tight_boundary_warning_count == 1
    assert compiled.segments[1].direction_flip_mask == 0


def test_flip_direction_uses_launch_accel_for_stop_estimation() -> None:
    notes = [
        _note(0.000, 0.100, 69, 69, 440.0),
        _note(0.100, 0.250, 76, 76, 659.3),
    ]
    compiled = compile_segments(
        notes,
        CompileOptions(
            connected_motors=1,
            idle_mode="idle",
            sticky_gap_s=0.05,
            flip_direction_on_note_change=True,
            direction_flip_safety_margin_ms=0.0,
            direction_flip_cooldown_ms=0.0,
        ),
    )

    assert compiled.direction_flip_requested_count == 1
    assert compiled.direction_flip_applied_count == 0
    assert compiled.direction_flip_suppressed_count == 1
    assert compiled.direction_flip_cooldown_suppressed_count == 0
    assert compiled.tight_boundary_warning_count == 1


def test_flip_direction_allows_boundary_when_launch_decel_matches_run_accel() -> None:
    notes = [
        _note(0.000, 0.100, 69, 69, 440.0),
        _note(0.100, 0.250, 76, 76, 659.3),
    ]
    compiled = compile_segments(
        notes,
        CompileOptions(
            connected_motors=1,
            idle_mode="idle",
            sticky_gap_s=0.05,
            flip_direction_on_note_change=True,
            direction_flip_safety_margin_ms=0.0,
            direction_flip_cooldown_ms=0.0,
            playback_launch_accel_hz_per_s=8000.0,
        ),
    )

    assert compiled.direction_flip_requested_count == 1
    assert compiled.direction_flip_applied_count == 1
    assert compiled.direction_flip_suppressed_count == 0
    assert compiled.direction_flip_cooldown_suppressed_count == 0
    assert compiled.tight_boundary_warning_count == 0
    assert compiled.segments[1].direction_flip_mask == 0x01


def test_flip_direction_can_preserve_tight_window_when_suppression_disabled() -> None:
    notes = [
        _note(0.000, 0.100, 69, 69, 440.0),
        _note(0.100, 0.110, 76, 76, 659.3),
    ]
    compiled = compile_segments(
        notes,
        CompileOptions(
            connected_motors=1,
            idle_mode="idle",
            sticky_gap_s=0.05,
            flip_direction_on_note_change=True,
            suppress_tight_direction_flips=False,
        ),
    )

    assert len(compiled.segments) == 2
    assert compiled.direction_flip_requested_count == 1
    assert compiled.direction_flip_applied_count == 1
    assert compiled.direction_flip_suppressed_count == 0
    assert compiled.tight_boundary_warning_count == 1
    assert compiled.segments[1].direction_flip_mask == 0x01


def test_flip_direction_cooldown_suppresses_back_to_back_reversals_on_same_motor() -> None:
    notes = [
        _note(0.000, 0.180, 57, 57, 220.0),
        _note(0.180, 0.360, 64, 64, 329.6),
        _note(0.360, 0.540, 59, 59, 246.9),
    ]
    compiled = compile_segments(
        notes,
        CompileOptions(
            connected_motors=1,
            idle_mode="idle",
            sticky_gap_s=0.05,
            flip_direction_on_note_change=True,
            direction_flip_safety_margin_ms=0.0,
            direction_flip_cooldown_ms=200.0,
        ),
    )

    assert compiled.direction_flip_requested_count == 2
    assert compiled.direction_flip_applied_count == 1
    assert compiled.direction_flip_suppressed_count == 1
    assert compiled.direction_flip_cooldown_suppressed_count == 1
    assert compiled.tight_boundary_warning_count == 0
    assert compiled.segments[1].direction_flip_mask == 0x01
    assert compiled.segments[2].direction_flip_mask == 0


def test_flip_direction_cooldown_is_per_motor_not_global() -> None:
    profile = InstrumentProfile(
        name="split_range",
        profile_version=1,
        motor_count=2,
        motors=(
            InstrumentMotorProfile(
                motor_idx=0,
                label="low",
                min_hz=15.0,
                max_hz=800.0,
                preferred_min_hz=80.0,
                preferred_max_hz=220.0,
            ),
            InstrumentMotorProfile(
                motor_idx=1,
                label="high",
                min_hz=15.0,
                max_hz=800.0,
                preferred_min_hz=330.0,
                preferred_max_hz=760.0,
            ),
        ),
    )
    notes = [
        _note(0.000, 0.180, 45, 45, 110.0),
        _note(0.000, 0.300, 69, 69, 440.0),
        _note(0.180, 0.480, 50, 50, 146.8),
        _note(0.300, 0.600, 73, 73, 554.4),
    ]
    compiled = compile_segments(
        notes,
        CompileOptions(
            connected_motors=2,
            idle_mode="idle",
            sticky_gap_s=0.05,
            flip_direction_on_note_change=True,
            direction_flip_safety_margin_ms=0.0,
            direction_flip_cooldown_ms=200.0,
        ),
        instrument_profile=profile,
    )

    assert compiled.direction_flip_requested_count == 2
    assert compiled.direction_flip_applied_count == 2
    assert compiled.direction_flip_suppressed_count == 0
    assert compiled.direction_flip_cooldown_suppressed_count == 0


def test_flip_direction_safety_margin_can_suppress_otherwise_safe_boundary() -> None:
    notes = [
        _note(0.000, 0.100, 69, 69, 440.0),
        _note(0.100, 0.301, 76, 76, 659.3),
    ]
    compiled = compile_segments(
        notes,
        CompileOptions(
            connected_motors=1,
            idle_mode="idle",
            sticky_gap_s=0.05,
            flip_direction_on_note_change=True,
            direction_flip_safety_margin_ms=100.0,
        ),
    )

    assert compiled.direction_flip_requested_count == 1
    assert compiled.direction_flip_applied_count == 0
    assert compiled.direction_flip_suppressed_count == 1
    assert compiled.direction_flip_cooldown_suppressed_count == 0
    assert compiled.tight_boundary_warning_count == 1


def test_event_groups_preserve_dense_polyphony_without_load_guard() -> None:
    notes = [
        _note(0.000, 0.500, 60, 60, 261.6, velocity=100),
        _note(0.000, 0.500, 64, 64, 329.6, velocity=95),
        _note(0.000, 0.500, 67, 67, 392.0, velocity=90),
        _note(0.000, 0.500, 71, 71, 493.9, velocity=20),
    ]
    compiled = compile_segments(
        notes,
        CompileOptions(
            connected_motors=4,
            idle_mode="idle",
            sticky_gap_s=0.05,
        ),
    )

    assert len(compiled.segments) == 1
    assert sum(1 for freq in compiled.segments[0].motor_freq_hz if freq > 0.0) == 4
    assert compiled.load_limited_segment_count == 0
    assert compiled.load_limited_note_count == 0


def test_event_groups_ignore_aggregate_step_rate_limit() -> None:
    notes = [
        _note(0.000, 0.500, 60, 60, 300.0, velocity=100),
        _note(0.000, 0.500, 64, 64, 250.0, velocity=95),
        _note(0.000, 0.500, 67, 67, 200.0, velocity=90),
    ]
    compiled = compile_segments(
        notes,
        CompileOptions(
            connected_motors=3,
            idle_mode="idle",
            sticky_gap_s=0.05,
        ),
    )

    active_freqs = [freq for freq in compiled.segments[0].motor_freq_hz if freq > 0.0]
    assert len(active_freqs) == 3
    assert 300.0 in active_freqs
    assert 250.0 in active_freqs
    assert 200.0 in active_freqs
    assert compiled.load_limited_segment_count == 0
    assert compiled.load_limited_note_count == 0


def test_same_pitch_gap_preserves_per_motor_independence() -> None:
    """One motor can keep an explicit idle gap while another changes normally."""
    notes = [
        # Motor 0 plays 440 Hz across a 1 ms gap.
        _note(0.000, 0.499, 69, 69, 440.0),
        _note(0.500, 1.000, 69, 69, 440.0),
        # Motor 1 plays 330 Hz only during the first half
        _note(0.000, 0.499, 64, 64, 329.6),
    ]
    compiled = compile_segments(
        notes,
        CompileOptions(
            connected_motors=2,
            idle_mode="idle",
            sticky_gap_s=0.05,
        ),
    )
    assert compiled.silence_gaps_bridged == 0
    assert compiled.segments[0].motor_freq_hz == pytest.approx((440.0, 329.6))
    assert compiled.segments[1].motor_freq_hz == pytest.approx((0.0, 0.0))
    assert compiled.segments[2].motor_freq_hz == pytest.approx((440.0, 0.0))


def test_segment_floor_keeps_tiny_high_pulse_segments() -> None:
    notes = [
        _note(0.000000, 0.100000, 60, 60, 261.6),
        _note(0.100000, 0.100020, 95, 95, 2000.0),
        _note(0.100020, 0.200000, 60, 60, 261.6),
    ]
    compiled = compile_segments(
        notes,
        CompileOptions(
            connected_motors=1,
            idle_mode="idle",
            sticky_gap_s=0.05,
        ),
    )
    assert sum(seg.duration_us for seg in compiled.segments) == 200_000
    assert any(
        seg.duration_us == 20 and seg.motor_freq_hz[0] > 1500.0
        for seg in compiled.segments
    )

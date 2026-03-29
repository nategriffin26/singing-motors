from __future__ import annotations

from music2.arrangement_report import build_arrangement_report
from music2.compiler import compile_segments
from music2.instrument_profile import FrequencyBand, InstrumentMotorProfile, InstrumentProfile
from music2.models import CompileOptions, MidiAnalysisReport, NoteEvent


def test_arrangement_report_tracks_role_loss_and_comfort_hits() -> None:
    notes = [
        NoteEvent(0.0, 1.0, 48, 48, 130.81, 90, 0),
        NoteEvent(0.0, 1.0, 60, 60, 261.63, 40, 0),
        NoteEvent(0.0, 1.0, 72, 72, 523.25, 100, 0),
    ]
    analysis = MidiAnalysisReport(
        notes=notes,
        duration_s=1.0,
        note_count=len(notes),
        max_polyphony=3,
        transpose_semitones=0,
        clamped_note_count=0,
        min_source_note=48,
        max_source_note=72,
    )
    compiled = compile_segments(
        notes,
        CompileOptions(
            connected_motors=2,
            idle_mode="idle",
            overflow_mode="steal_quietest",
            sticky_gap_s=0.05,
        ),
    )
    profile = InstrumentProfile(
        name="test_duo",
        profile_version=1,
        motor_count=2,
        motors=(
            InstrumentMotorProfile(motor_idx=0, label="bass", min_hz=30.0, max_hz=600.0),
            InstrumentMotorProfile(
                motor_idx=1,
                label="lead",
                min_hz=30.0,
                max_hz=700.0,
                preferred_max_hz=400.0,
                resonance_bands=(FrequencyBand(start_hz=500.0, end_hz=540.0, severity=0.8),),
            ),
        ),
    )

    report = build_arrangement_report(
        analysis=analysis,
        compiled=compiled,
        instrument_profile=profile,
    )

    assert report.considered_note_count == 3
    assert report.melody_note_count == 1
    assert report.preserved_melody_note_count == 1
    assert report.inner_note_count == 1
    assert report.dropped_inner_note_count == 1
    assert report.bass_note_count == 1
    assert report.preserved_bass_note_count == 1
    assert report.motor_preferred_band_violation_count == 1
    assert report.motor_resonance_band_hit_count == 1
    assert report.motor_comfort_violation_count == 1
    assert report.weighted_musical_loss == 0.35


def test_arrangement_report_counts_octave_retargeted_notes() -> None:
    notes = [
        NoteEvent(0.0, 0.5, 108, 108, 1046.5, 90, 0),
    ]
    analysis = MidiAnalysisReport(
        notes=notes,
        duration_s=0.5,
        note_count=1,
        max_polyphony=1,
        transpose_semitones=0,
        clamped_note_count=1,
        min_source_note=108,
        max_source_note=108,
    )
    compiled = compile_segments(
        notes,
        CompileOptions(
            connected_motors=1,
            idle_mode="idle",
            overflow_mode="steal_quietest",
            sticky_gap_s=0.05,
        ),
    )
    profile = InstrumentProfile(
        name="solo",
        profile_version=1,
        motor_count=1,
        motors=(InstrumentMotorProfile(motor_idx=0, label="solo", min_hz=30.0, max_hz=1500.0),),
    )

    report = build_arrangement_report(
        analysis=analysis,
        compiled=compiled,
        instrument_profile=profile,
    )

    assert report.octave_retargeted_note_count == 1

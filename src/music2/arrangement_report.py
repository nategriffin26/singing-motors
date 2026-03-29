from __future__ import annotations

import math

from .instrument_profile import FrequencyBand, InstrumentProfile
from .midi import midi_note_to_freq
from .models import ArrangementReport, CompileReport, MidiAnalysisReport, NoteEvent

_ROLE_WEIGHT = {
    "melody": 1.0,
    "bass": 0.8,
    "inner": 0.35,
}


def _classify_roles(notes: list[NoteEvent]) -> list[str]:
    roles = ["inner"] * len(notes)
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

    return roles


def _in_band(freq_hz: float, band: FrequencyBand) -> bool:
    return band.start_hz <= freq_hz <= band.end_hz


def build_arrangement_report(
    *,
    analysis: MidiAnalysisReport,
    compiled: CompileReport,
    instrument_profile: InstrumentProfile,
) -> ArrangementReport:
    notes = analysis.notes
    if not notes:
        return ArrangementReport()

    roles = _classify_roles(notes)
    preferred_band_violation_count = 0
    resonance_band_hit_count = 0
    avoid_band_hit_count = 0
    motor_comfort_violation_count = 0

    melody_note_count = 0
    preserved_melody_note_count = 0
    dropped_melody_note_count = 0
    bass_note_count = 0
    preserved_bass_note_count = 0
    dropped_bass_note_count = 0
    inner_note_count = 0
    dropped_inner_note_count = 0

    preserved_note_count = 0
    dropped_note_count = 0
    truncated_note_count = 0
    weighted_musical_loss = 0.0

    ordered_motors = instrument_profile.ordered_motors

    for idx, note in enumerate(notes):
        role = roles[idx]
        weight = _ROLE_WEIGHT[role]
        if role == "melody":
            melody_note_count += 1
        elif role == "bass":
            bass_note_count += 1
        else:
            inner_note_count += 1

        assignment = compiled.assignments[idx] if idx < len(compiled.assignments) else -1
        effective_end = (
            max(note.start_s, compiled.effective_end_s[idx])
            if idx < len(compiled.effective_end_s)
            else note.start_s
        )
        played_duration_s = max(0.0, effective_end - note.start_s)
        original_duration_s = max(1e-9, note.end_s - note.start_s)
        preserved = assignment >= 0 and played_duration_s > 0.0

        if preserved:
            preserved_note_count += 1
            if role == "melody":
                preserved_melody_note_count += 1
            elif role == "bass":
                preserved_bass_note_count += 1
            if effective_end < note.end_s:
                truncated_note_count += 1
                weighted_musical_loss += weight * max(0.0, 1.0 - (played_duration_s / original_duration_s))
        else:
            dropped_note_count += 1
            weighted_musical_loss += weight
            if role == "melody":
                dropped_melody_note_count += 1
            elif role == "bass":
                dropped_bass_note_count += 1
            else:
                dropped_inner_note_count += 1
            continue

        if assignment >= len(ordered_motors):
            continue
        motor = ordered_motors[assignment]
        violation = False
        if note.frequency_hz < motor.resolved_preferred_min_hz or note.frequency_hz > motor.resolved_preferred_max_hz:
            preferred_band_violation_count += 1
            violation = True
        if any(_in_band(note.frequency_hz, band) for band in motor.resonance_bands):
            resonance_band_hit_count += 1
            violation = True
        if any(_in_band(note.frequency_hz, band) for band in motor.avoid_bands + motor.stall_prone_bands):
            avoid_band_hit_count += 1
            violation = True
        if violation:
            motor_comfort_violation_count += 1

    octave_retargeted_note_count = 0
    for note in notes:
        raw_freq = midi_note_to_freq(note.transposed_note)
        if not math.isclose(raw_freq, note.frequency_hz, rel_tol=1e-9, abs_tol=1e-9):
            octave_retargeted_note_count += 1

    return ArrangementReport(
        considered_note_count=len(notes),
        preserved_note_count=preserved_note_count,
        dropped_note_count=dropped_note_count,
        truncated_note_count=truncated_note_count,
        melody_note_count=melody_note_count,
        preserved_melody_note_count=preserved_melody_note_count,
        dropped_melody_note_count=dropped_melody_note_count,
        bass_note_count=bass_note_count,
        preserved_bass_note_count=preserved_bass_note_count,
        dropped_bass_note_count=dropped_bass_note_count,
        inner_note_count=inner_note_count,
        dropped_inner_note_count=dropped_inner_note_count,
        octave_retargeted_note_count=octave_retargeted_note_count,
        coalesced_transition_count=(
            compiled.adjacent_segments_merged + compiled.short_segments_absorbed + compiled.silence_gaps_bridged
        ),
        requested_reversal_count=compiled.direction_flip_requested_count,
        applied_reversal_count=compiled.direction_flip_applied_count,
        avoided_reversal_count=compiled.direction_flip_suppressed_count,
        tight_reversal_window_count=compiled.tight_boundary_warning_count,
        motor_preferred_band_violation_count=preferred_band_violation_count,
        motor_resonance_band_hit_count=resonance_band_hit_count,
        motor_avoid_band_hit_count=avoid_band_hit_count,
        motor_comfort_violation_count=motor_comfort_violation_count,
        weighted_musical_loss=round(weighted_musical_loss, 4),
    )

from __future__ import annotations

from music2.transcribe.polyphony import compute_max_polyphony, enforce_polyphony_cap
from music2.transcribe.types import CandidateNote


def _note(start: float, end: float, midi_note: int, *, confidence: float = 0.8, velocity: int = 90) -> CandidateNote:
    return CandidateNote(
        start_s=start,
        end_s=end,
        midi_note=midi_note,
        confidence=confidence,
        velocity=velocity,
        source="test",
    )


def test_enforce_polyphony_cap_limits_to_six() -> None:
    notes = [_note(0.0, 1.0, 60 + idx, confidence=0.9 - idx * 0.02) for idx in range(8)]
    capped, stats = enforce_polyphony_cap(notes, cap=6)
    assert compute_max_polyphony(capped) <= 6
    assert stats.max_polyphony_before == 8
    assert stats.max_polyphony_after <= 6
    assert len(capped) == 6


def test_enforce_polyphony_prefers_continuity_for_persistent_note() -> None:
    notes = [
        _note(0.0, 0.9, 60, confidence=0.7),
        _note(0.0, 0.4, 62, confidence=0.7),
        _note(0.0, 0.4, 64, confidence=0.7),
        _note(0.0, 0.4, 65, confidence=0.7),
        _note(0.0, 0.4, 67, confidence=0.7),
        _note(0.0, 0.4, 69, confidence=0.7),
        _note(0.4, 0.9, 71, confidence=0.7),
    ]
    capped, _stats = enforce_polyphony_cap(notes, cap=6)
    sustained = [note for note in capped if note.midi_note == 60]
    assert len(sustained) == 1
    assert sustained[0].start_s == 0.0
    assert sustained[0].end_s == 0.9


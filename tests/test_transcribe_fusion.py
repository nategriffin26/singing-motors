from __future__ import annotations

from music2.transcribe.fusion import fuse_candidates
from music2.transcribe.types import CandidateNote


def _note(
    start: float,
    end: float,
    midi_note: int,
    *,
    source: str,
    confidence: float = 0.8,
    velocity: int = 90,
) -> CandidateNote:
    return CandidateNote(
        start_s=start,
        end_s=end,
        midi_note=midi_note,
        source=source,
        confidence=confidence,
        velocity=velocity,
    )


def test_fuse_candidates_merges_overlapping_same_pitch() -> None:
    music = [_note(0.00, 0.40, 60, source="music", confidence=0.7)]
    speech = [_note(0.02, 0.42, 60, source="speech", confidence=0.9)]
    fused = fuse_candidates(music, speech, tolerance_s=0.03)
    assert len(fused) == 1
    assert fused[0].start_s == 0.00
    assert fused[0].end_s == 0.42
    assert fused[0].midi_note == 60
    assert fused[0].confidence == 0.9
    assert fused[0].source == "music+speech"


def test_fuse_candidates_keeps_distinct_far_apart_notes() -> None:
    music = [_note(0.00, 0.10, 60, source="music")]
    speech = [_note(0.30, 0.40, 60, source="speech")]
    fused = fuse_candidates(music, speech, tolerance_s=0.02)
    assert len(fused) == 2
    assert [note.start_s for note in fused] == [0.00, 0.30]


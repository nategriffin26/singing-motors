from __future__ import annotations

import sys
import types
from pathlib import Path

from music2.transcribe.postprocess import (
    apply_speech_postprocessing,
    compress_velocity,
    correct_octave_errors,
    filter_low_confidence,
    filter_short_notes,
    quantize_onsets_to_beats,
)
from music2.transcribe.types import CandidateNote, ConversionConfig


def _note(
    start: float,
    end: float,
    midi_note: int,
    *,
    velocity: int = 96,
    confidence: float = 0.8,
) -> CandidateNote:
    return CandidateNote(
        start_s=start,
        end_s=end,
        midi_note=midi_note,
        velocity=velocity,
        confidence=confidence,
        source="test",
    )


def test_filter_short_notes_drops_notes_below_threshold() -> None:
    notes = [_note(0.0, 0.03, 60), _note(0.1, 0.2, 62)]
    filtered = filter_short_notes(notes, min_duration_s=0.05)
    assert [note.midi_note for note in filtered] == [62]


def test_filter_low_confidence_drops_unreliable_notes() -> None:
    notes = [_note(0.0, 0.2, 60, confidence=0.2), _note(0.2, 0.4, 62, confidence=0.7)]
    filtered = filter_low_confidence(notes, min_confidence=0.3)
    assert [note.midi_note for note in filtered] == [62]


def test_quantize_onsets_to_beats_snaps_when_close(monkeypatch) -> None:
    fake_librosa = types.SimpleNamespace(
        load=lambda _path, sr=None, mono=True: ([0.0], 22050),
        beat=types.SimpleNamespace(beat_track=lambda y, sr: (120.0, [10, 20, 30])),
        frames_to_time=lambda frames, sr: [frame / 100.0 for frame in frames],
    )
    monkeypatch.setitem(sys.modules, "librosa", fake_librosa)

    notes = [_note(0.102, 0.202, 60), _note(0.28, 0.38, 62)]
    quantized = quantize_onsets_to_beats(notes, audio_path=Path("dummy.wav"))
    assert quantized[0].start_s == 0.1
    assert round(quantized[0].duration_s, 4) == 0.1
    assert quantized[1].start_s == 0.3


def test_compress_velocity_sqrt_curve() -> None:
    notes = [_note(0.0, 0.2, 60, velocity=16), _note(0.2, 0.4, 62, velocity=127)]
    compressed = compress_velocity(notes)
    assert compressed[0].velocity > 64
    assert compressed[1].velocity == 127


def test_correct_octave_errors_fixes_middle_outlier() -> None:
    notes = [
        _note(0.00, 0.10, 60),
        _note(0.12, 0.20, 72),
        _note(0.22, 0.30, 60),
    ]
    corrected = correct_octave_errors(notes)
    assert [note.midi_note for note in corrected] == [60, 60, 60]


def test_apply_speech_postprocessing_enforces_min_80ms() -> None:
    notes = [
        _note(0.00, 0.06, 60, confidence=0.9),
        _note(0.10, 0.19, 62, confidence=0.9),
    ]
    config = ConversionConfig(mode="speech", min_note_duration_s=0.05, min_confidence=0.3)
    processed = apply_speech_postprocessing(notes, config=config)
    assert [note.midi_note for note in processed] == [62]

from __future__ import annotations

from music2.transcribe.speech import segment_speech_pitch_track


def test_segment_speech_pitch_track_splits_on_large_pitch_jump() -> None:
    times = [idx * 0.01 for idx in range(30)]
    freqs = [220.0] * 12 + [330.0] * 12 + [0.0] * 6
    conf = [0.9] * 24 + [0.0] * 6

    notes = segment_speech_pitch_track(
        times_s=times,
        freq_hz=freqs,
        confidence=conf,
        min_confidence=0.3,
        max_pitch_jump_semitones=1.5,
        source="speech_test",
    )
    assert len(notes) == 2
    assert notes[0].midi_note == 57  # A3 ~ 220 Hz
    assert notes[1].midi_note == 64  # E4 ~ 330 Hz
    assert len(notes[0].bends) > 0
    assert len(notes[1].bends) > 0


def test_segment_speech_pitch_track_ignores_low_confidence_frames() -> None:
    times = [idx * 0.01 for idx in range(8)]
    freqs = [220.0] * 8
    conf = [0.1] * 8
    notes = segment_speech_pitch_track(
        times_s=times,
        freq_hz=freqs,
        confidence=conf,
        min_confidence=0.35,
    )
    assert notes == []


def test_segment_speech_pitch_track_hysteresis_uses_sustain_threshold() -> None:
    times = [idx * 0.01 for idx in range(12)]
    freqs = [220.0] * 8 + [0.0] * 4
    conf = [0.36, 0.24, 0.22, 0.21, 0.19, 0.37, 0.23, 0.22, 0.0, 0.0, 0.0, 0.0]
    notes = segment_speech_pitch_track(
        times_s=times,
        freq_hz=freqs,
        confidence=conf,
        min_confidence=0.35,
        sustain_confidence=0.20,
        min_note_duration_s=0.01,
    )
    assert len(notes) == 2
    assert notes[0].start_s == 0.0
    assert notes[0].end_s <= notes[1].start_s


def test_segment_speech_pitch_track_drops_short_note_by_default() -> None:
    times = [idx * 0.01 for idx in range(6)]
    freqs = [220.0] * 6
    conf = [0.9] * 6
    notes = segment_speech_pitch_track(
        times_s=times,
        freq_hz=freqs,
        confidence=conf,
    )
    assert notes == []

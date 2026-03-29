from __future__ import annotations

from pathlib import Path

from mido import Message, MetaMessage, MidiFile, MidiTrack

from music2.midi import analyze_midi, midi_note_to_freq


def _write_mid(path: Path) -> Path:
    mid = MidiFile(ticks_per_beat=480)
    track = MidiTrack()
    mid.tracks.append(track)
    track.append(MetaMessage("set_tempo", tempo=500000, time=0))
    track.append(Message("note_on", note=40, velocity=100, time=0))
    track.append(Message("note_off", note=40, velocity=0, time=480))
    track.append(Message("note_on", note=100, velocity=100, time=0))
    track.append(Message("note_off", note=100, velocity=0, time=480))
    mid.save(path)
    return path


def test_analysis_soft_clamp_count_and_frequencies(tmp_path: Path) -> None:
    midi_path = _write_mid(tmp_path / "clamp.mid")
    analysis, _ = analyze_midi(
        midi_path,
        min_freq_hz=200.0,
        max_freq_hz=500.0,
        transpose_override=0,
        auto_transpose=False,
    )
    assert analysis.note_count == 2
    assert analysis.clamped_note_count == 2
    # Note 40 (~82.4 Hz) folds up: 82.4 -> 164.8 -> 329.6 Hz (in range)
    # Note 100 (~2637 Hz) folds down: 2637 -> 1318.5 -> 659.3 -> 329.6 Hz (in range)
    for note in analysis.notes:
        assert 200.0 <= note.frequency_hz <= 500.0


def test_fold_frequency_brings_low_note_into_range(tmp_path: Path) -> None:
    """A note at 30 Hz with min=50 Hz should octave-up to 60 Hz, not clamp to 50."""
    mid = MidiFile(ticks_per_beat=480)
    track = MidiTrack()
    mid.tracks.append(track)
    track.append(MetaMessage("set_tempo", tempo=500000, time=0))
    # MIDI note 23 = ~30.87 Hz (B0)
    track.append(Message("note_on", note=23, velocity=100, time=0))
    track.append(Message("note_off", note=23, velocity=0, time=480))
    midi_path = tmp_path / "fold.mid"
    mid.save(midi_path)

    analysis, _ = analyze_midi(
        midi_path,
        min_freq_hz=50.0,
        max_freq_hz=2000.0,
        transpose_override=0,
        auto_transpose=False,
    )
    freq = analysis.notes[0].frequency_hz
    # Should be ~61.74 Hz (B1), not clamped to 50.0
    assert 61.0 < freq < 62.5


def test_fold_frequency_brings_high_note_into_range(tmp_path: Path) -> None:
    """A note at ~4186 Hz with max=2000 Hz should octave-down to ~1046 Hz."""
    mid = MidiFile(ticks_per_beat=480)
    track = MidiTrack()
    mid.tracks.append(track)
    track.append(MetaMessage("set_tempo", tempo=500000, time=0))
    # MIDI note 108 = ~4186 Hz (C8)
    track.append(Message("note_on", note=108, velocity=100, time=0))
    track.append(Message("note_off", note=108, velocity=0, time=480))
    midi_path = tmp_path / "fold_high.mid"
    mid.save(midi_path)

    analysis, _ = analyze_midi(
        midi_path,
        min_freq_hz=50.0,
        max_freq_hz=2000.0,
        transpose_override=0,
        auto_transpose=False,
    )
    freq = analysis.notes[0].frequency_hz
    # 4186 / 2 = 2093 (still too high) / 2 = 1046.5 Hz (C6) — in range
    assert 1040.0 < freq < 1053.0


def test_auto_transpose_reduces_clamps(tmp_path: Path) -> None:
    midi_path = _write_mid(tmp_path / "auto.mid")
    manual, _ = analyze_midi(
        midi_path,
        min_freq_hz=midi_note_to_freq(55),
        max_freq_hz=midi_note_to_freq(80),
        transpose_override=0,
        auto_transpose=False,
    )
    auto, _ = analyze_midi(
        midi_path,
        min_freq_hz=midi_note_to_freq(55),
        max_freq_hz=midi_note_to_freq(80),
        transpose_override=None,
        auto_transpose=True,
    )

    assert auto.clamped_note_count <= manual.clamped_note_count

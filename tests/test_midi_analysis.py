from __future__ import annotations

import math
from pathlib import Path

import pytest
from mido import Message, MetaMessage, MidiFile, MidiTrack

from music2.midi import analyze_midi, freq_to_note_name


def _write_mid(path: Path, track: MidiTrack, ticks_per_beat: int = 480) -> Path:
    mid = MidiFile(ticks_per_beat=ticks_per_beat)
    mid.tracks.append(track)
    mid.save(path)
    return path


def test_analyze_midi_handles_tempo_map(tmp_path: Path) -> None:
    track = MidiTrack()
    track.append(MetaMessage("set_tempo", tempo=500000, time=0))
    track.append(Message("note_on", note=60, velocity=100, time=0))
    track.append(Message("note_off", note=60, velocity=0, time=480))
    track.append(MetaMessage("set_tempo", tempo=1000000, time=0))
    track.append(Message("note_on", note=62, velocity=100, time=0))
    track.append(Message("note_off", note=62, velocity=0, time=480))

    midi_path = _write_mid(tmp_path / "tempo.mid", track)
    analysis, _ = analyze_midi(
        midi_path,
        min_freq_hz=20.0,
        max_freq_hz=5000.0,
        transpose_override=0,
        auto_transpose=False,
    )

    assert analysis.note_count == 2
    assert analysis.duration_s == 1.5
    assert analysis.max_polyphony == 1


def test_analyze_midi_polyphony(tmp_path: Path) -> None:
    track = MidiTrack()
    track.append(MetaMessage("set_tempo", tempo=500000, time=0))
    track.append(Message("note_on", note=60, velocity=100, time=0))
    track.append(Message("note_on", note=64, velocity=100, time=0))
    track.append(Message("note_off", note=60, velocity=0, time=480))
    track.append(Message("note_off", note=64, velocity=0, time=0))

    midi_path = _write_mid(tmp_path / "poly.mid", track)
    analysis, _ = analyze_midi(
        midi_path,
        min_freq_hz=20.0,
        max_freq_hz=5000.0,
        transpose_override=0,
        auto_transpose=False,
    )

    assert analysis.max_polyphony == 2
    assert analysis.duration_s == 0.5


def test_analyze_midi_drops_channel_10_drums(tmp_path: Path) -> None:
    track = MidiTrack()
    track.append(MetaMessage("set_tempo", tempo=500000, time=0))
    track.append(Message("note_on", channel=9, note=36, velocity=100, time=0))
    track.append(Message("note_off", channel=9, note=36, velocity=0, time=480))
    track.append(Message("note_on", channel=0, note=60, velocity=100, time=0))
    track.append(Message("note_off", channel=0, note=60, velocity=0, time=480))

    midi_path = _write_mid(tmp_path / "drums.mid", track)
    analysis, _ = analyze_midi(
        midi_path,
        min_freq_hz=20.0,
        max_freq_hz=5000.0,
        transpose_override=0,
        auto_transpose=False,
    )

    assert analysis.note_count == 1
    assert [note.channel for note in analysis.notes] == [0]
    assert [note.source_note for note in analysis.notes] == [60]


def test_analyze_midi_drops_blacklisted_percussion_programs(tmp_path: Path) -> None:
    track = MidiTrack()
    track.append(MetaMessage("set_tempo", tempo=500000, time=0))
    track.append(Message("program_change", channel=1, program=11, time=0))
    track.append(Message("note_on", channel=1, note=72, velocity=100, time=0))
    track.append(Message("note_off", channel=1, note=72, velocity=0, time=480))
    track.append(Message("program_change", channel=0, program=40, time=0))
    track.append(Message("note_on", channel=0, note=60, velocity=100, time=0))
    track.append(Message("note_off", channel=0, note=60, velocity=0, time=480))

    midi_path = _write_mid(tmp_path / "percussion_program.mid", track)
    analysis, _ = analyze_midi(
        midi_path,
        min_freq_hz=20.0,
        max_freq_hz=5000.0,
        transpose_override=0,
        auto_transpose=False,
    )

    assert analysis.note_count == 1
    assert [note.channel for note in analysis.notes] == [0]
    assert [note.source_note for note in analysis.notes] == [60]


@pytest.mark.parametrize(
    "freq_hz, expected",
    [
        (440.0, "A4 "),
        (261.63, "C4 "),
        (329.63, "E4 "),
        (466.16, "A#4"),
        (130.81, "C3 "),
        (783.99, "G5 "),
        (0.0, "-- "),
        (0.001, "-- "),
    ],
)
def test_freq_to_note_name(freq_hz: float, expected: str) -> None:
    assert freq_to_note_name(freq_hz) == expected


def test_analyze_midi_returns_tempo_map(tmp_path: Path) -> None:
    track = MidiTrack()
    track.append(MetaMessage("set_tempo", tempo=500000, time=0))
    track.append(Message("note_on", note=60, velocity=100, time=0))
    track.append(Message("note_off", note=60, velocity=0, time=480))

    midi_path = _write_mid(tmp_path / "tempo_map.mid", track, ticks_per_beat=480)
    analysis, tempo_map = analyze_midi(
        midi_path, min_freq_hz=20.0, max_freq_hz=5000.0,
        transpose_override=0, auto_transpose=False,
    )

    assert analysis.note_count == 1
    assert tempo_map.ticks_per_beat == 480
    assert len(tempo_map.points) >= 1
    assert tempo_map.points[0].tempo == 500000


def test_analyze_midi_tempo_map_captures_changes(tmp_path: Path) -> None:
    track = MidiTrack()
    track.append(MetaMessage("set_tempo", tempo=500000, time=0))
    track.append(Message("note_on", note=60, velocity=100, time=0))
    track.append(Message("note_off", note=60, velocity=0, time=480))
    track.append(MetaMessage("set_tempo", tempo=750000, time=0))
    track.append(Message("note_on", note=62, velocity=100, time=0))
    track.append(Message("note_off", note=62, velocity=0, time=480))

    midi_path = _write_mid(tmp_path / "tempo_changes.mid", track, ticks_per_beat=480)
    _, tempo_map = analyze_midi(
        midi_path, min_freq_hz=20.0, max_freq_hz=5000.0,
        transpose_override=0, auto_transpose=False,
    )

    assert len(tempo_map.points) == 2
    assert tempo_map.points[0].tempo == 500000
    assert tempo_map.points[1].tempo == 750000


def test_analyze_midi_strip_leading_silence_toggle(tmp_path: Path) -> None:
    track = MidiTrack()
    track.append(MetaMessage("set_tempo", tempo=500000, time=0))
    track.append(Message("note_on", note=60, velocity=100, time=960))
    track.append(Message("note_off", note=60, velocity=0, time=480))

    midi_path = _write_mid(tmp_path / "leading_silence.mid", track, ticks_per_beat=480)

    stripped, _ = analyze_midi(
        midi_path,
        min_freq_hz=20.0,
        max_freq_hz=5000.0,
        transpose_override=0,
        auto_transpose=False,
        strip_leading_silence=True,
    )
    preserved, _ = analyze_midi(
        midi_path,
        min_freq_hz=20.0,
        max_freq_hz=5000.0,
        transpose_override=0,
        auto_transpose=False,
        strip_leading_silence=False,
    )

    assert stripped.note_count == preserved.note_count == 1
    assert stripped.notes[0].start_s == pytest.approx(0.0)
    assert stripped.notes[0].end_s == pytest.approx(0.5)
    assert stripped.duration_s == pytest.approx(0.5)
    assert preserved.notes[0].start_s == pytest.approx(1.0)
    assert preserved.notes[0].end_s == pytest.approx(1.5)
    assert preserved.duration_s == pytest.approx(1.5)


def test_freq_to_note_name_returns_fixed_width() -> None:
    for note in range(24, 96):
        freq = 440.0 * (2.0 ** ((note - 69) / 12.0))
        name = freq_to_note_name(freq)
        assert len(name) in (2, 3), f"note {note} freq {freq} gave {name!r} len={len(name)}"

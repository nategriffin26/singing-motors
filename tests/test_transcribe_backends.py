from __future__ import annotations

import sys
import types
from pathlib import Path

from mido import Message, MetaMessage, MidiFile, MidiTrack

from music2.transcribe import backends
from music2.transcribe.types import CandidateNote


def test_transcribe_with_piano_transcription_uses_cached_midi(
    monkeypatch,
    tmp_path: Path,
) -> None:
    audio_path = tmp_path / "input.wav"
    audio_path.write_bytes(b"RIFFfake")
    cache_dir = tmp_path / "cache"
    cached_midi = cache_dir / "piano_transcription" / "cachekey.mid"
    cached_midi.parent.mkdir(parents=True, exist_ok=True)
    cached_midi.write_bytes(b"MThd")

    fake_torch = types.SimpleNamespace(
        cuda=types.SimpleNamespace(is_available=lambda: False),
        backends=types.SimpleNamespace(mps=types.SimpleNamespace(is_available=lambda: False)),
    )

    class _UnexpectedTranscriptor:
        def __init__(self, device: str) -> None:
            raise AssertionError(f"piano transcriptor should not run when cache exists (device={device})")

    fake_pti = types.SimpleNamespace(PianoTranscription=_UnexpectedTranscriptor, sample_rate=16000)
    fake_librosa = types.SimpleNamespace(load=lambda _path, sr, mono: ([0.0], sr))
    fake_numpy = types.SimpleNamespace()

    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setitem(sys.modules, "piano_transcription_inference", fake_pti)
    monkeypatch.setitem(sys.modules, "librosa", fake_librosa)
    monkeypatch.setitem(sys.modules, "numpy", fake_numpy)
    monkeypatch.setattr(backends, "_stable_key", lambda _path: "cachekey")

    expected = [
        CandidateNote(
            start_s=0.0,
            end_s=0.2,
            midi_note=60,
            velocity=96,
            confidence=0.8,
            source="piano_transcription",
        )
    ]

    def _fake_read(midi_path: Path, *, source: str) -> list[CandidateNote]:
        assert midi_path == cached_midi
        assert source == "piano_transcription"
        return expected

    monkeypatch.setattr(backends, "read_midi_as_candidate_notes", _fake_read)

    notes, warnings = backends.transcribe_with_piano_transcription(audio_path, cache_dir=cache_dir)
    assert warnings == []
    assert notes == expected


def test_read_midi_as_candidate_notes_drops_drum_channel(tmp_path: Path) -> None:
    midi_path = tmp_path / "drums.mid"
    mid = MidiFile()
    track = MidiTrack()
    mid.tracks.append(track)
    track.append(MetaMessage("set_tempo", tempo=500000, time=0))
    track.append(Message("note_on", channel=9, note=36, velocity=100, time=0))
    track.append(Message("note_off", channel=9, note=36, velocity=0, time=480))
    track.append(Message("note_on", channel=0, note=60, velocity=100, time=0))
    track.append(Message("note_off", channel=0, note=60, velocity=0, time=480))
    mid.save(midi_path)

    notes = backends.read_midi_as_candidate_notes(midi_path, source="test")

    assert len(notes) == 1
    assert notes[0].midi_note == 60


def test_read_midi_as_candidate_notes_drops_blacklisted_programs(tmp_path: Path) -> None:
    midi_path = tmp_path / "percussion_program.mid"
    mid = MidiFile()
    track = MidiTrack()
    mid.tracks.append(track)
    track.append(MetaMessage("set_tempo", tempo=500000, time=0))
    track.append(Message("program_change", channel=1, program=112, time=0))
    track.append(Message("note_on", channel=1, note=50, velocity=100, time=0))
    track.append(Message("note_off", channel=1, note=50, velocity=0, time=480))
    track.append(Message("program_change", channel=0, program=40, time=0))
    track.append(Message("note_on", channel=0, note=60, velocity=100, time=0))
    track.append(Message("note_off", channel=0, note=60, velocity=0, time=480))
    mid.save(midi_path)

    notes = backends.read_midi_as_candidate_notes(midi_path, source="test")

    assert len(notes) == 1
    assert notes[0].midi_note == 60


def test_transcribe_with_basic_pitch_skips_non_playable_instruments(monkeypatch, tmp_path: Path) -> None:
    audio_path = tmp_path / "input.wav"
    audio_path.write_bytes(b"RIFFfake")

    class _Note:
        def __init__(self, pitch: int) -> None:
            self.start = 0.0
            self.end = 0.2
            self.pitch = pitch
            self.velocity = 96

    playable_instrument = types.SimpleNamespace(program=40, is_drum=False, notes=[_Note(60)])
    drum_instrument = types.SimpleNamespace(program=0, is_drum=True, notes=[_Note(36)])
    percussive_instrument = types.SimpleNamespace(program=112, is_drum=False, notes=[_Note(72)])
    fake_midi = types.SimpleNamespace(instruments=[drum_instrument, percussive_instrument, playable_instrument])
    fake_inference = types.SimpleNamespace(predict=lambda _path: (None, fake_midi, []))

    monkeypatch.setitem(sys.modules, "basic_pitch.inference", fake_inference)

    notes, warnings = backends.transcribe_with_basic_pitch(audio_path)

    assert warnings == []
    assert [note.midi_note for note in notes] == [60]

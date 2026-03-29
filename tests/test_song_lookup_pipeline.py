from __future__ import annotations

from pathlib import Path

import mido

from music2.config import HostConfig
from music2.midi import analyze_midi
from music2.song_lookup.pipeline import find_song
from music2.song_lookup.sources.local_corpus import LocalCorpusAdapter
from music2.song_lookup.sources.manual_url import ManualUrlAdapter
from music2.song_lookup.types import SongQuery


def _write_midi(path: Path, note_groups: list[tuple[int, list[int], int]], *, ticks_per_beat: int = 480) -> Path:
    mid = mido.MidiFile(ticks_per_beat=ticks_per_beat)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.append(mido.MetaMessage("set_tempo", tempo=500000, time=0))

    last_tick = 0
    events: list[tuple[int, int, mido.Message]] = []
    for start_tick, notes, duration in note_groups:
        for note in notes:
            events.append((start_tick, 1, mido.Message("note_on", note=note, velocity=90, time=0, channel=0)))
            events.append((start_tick + duration, 0, mido.Message("note_off", note=note, velocity=0, time=0, channel=0)))
    events.sort(key=lambda item: (item[0], item[1]))
    for abs_tick, _, msg in events:
        delta = abs_tick - last_tick
        last_tick = abs_tick
        track.append(msg.copy(time=delta))
    track.append(mido.MetaMessage("end_of_track", time=0))
    mid.save(path)
    return path


def test_find_song_prefers_lower_loss_candidate(tmp_path: Path, monkeypatch) -> None:
    repo_root = tmp_path / "repo"
    midi_dir = repo_root / "assets" / "midi"
    midi_dir.mkdir(parents=True)

    strong = _write_midi(
        midi_dir / "Imperial March good.mid",
        [(0, [60], 240), (240, [62], 240), (480, [64], 240)],
    )
    noisy = _write_midi(
        midi_dir / "Imperial March dense.mid",
        [(0, [48, 52, 55, 60, 64, 67, 71], 480), (480, [50, 53, 57, 62, 65, 69, 72], 480)],
    )

    from music2.song_lookup import pipeline as lookup_pipeline

    monkeypatch.setattr(
        lookup_pipeline,
        "build_default_adapters",
        lambda **kwargs: [LocalCorpusAdapter(roots=(midi_dir,))],
    )

    result = find_song(
        SongQuery(title="Imperial March", artist="John Williams"),
        cfg=HostConfig(),
        repo_root=repo_root,
        out_dir=tmp_path / "out",
        download_best=True,
    )

    assert result.candidates
    assert result.recommended_index == 0
    best = result.candidates[0]
    assert best.artifact is not None
    assert best.artifact.local_path == strong
    assert best.analysis is not None
    assert best.analysis.exported_motor_safe_midi is not None
    assert (tmp_path / "out" / "01_imperial-march-good.mid").exists()
    exported_analysis, _ = analyze_midi(
        best.analysis.exported_motor_safe_midi,
        min_freq_hz=HostConfig().min_freq_hz,
        max_freq_hz=HostConfig().max_freq_hz,
        transpose_override=0,
        auto_transpose=False,
    )
    assert exported_analysis.max_polyphony <= 6
    assert noisy.exists()


def test_find_song_uses_manual_audio_fallback_when_provided(tmp_path: Path, monkeypatch) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    out_dir = tmp_path / "out"
    audio_path = tmp_path / "imperial.mp3"
    audio_path.write_bytes(b"fake-audio")
    generated_midi = _write_midi(tmp_path / "generated.mid", [(0, [60, 64, 67], 480)])

    from music2.song_lookup import pipeline as lookup_pipeline

    monkeypatch.setattr(
        lookup_pipeline,
        "build_default_adapters",
        lambda **kwargs: [ManualUrlAdapter(audio_paths=(str(audio_path),))],
    )

    class _FakeConversionResult:
        def __init__(self, midi_path: Path) -> None:
            self.motor_midi_path = midi_path

    monkeypatch.setattr(
        lookup_pipeline,
        "convert_mp3_to_dual_midi",
        lambda *args, **kwargs: _FakeConversionResult(generated_midi),
    )

    result = lookup_pipeline.find_song(
        SongQuery(title="Imperial March", audio_paths=(str(audio_path),)),
        cfg=HostConfig(),
        repo_root=repo_root,
        out_dir=out_dir,
    )

    assert result.candidates
    assert result.candidates[0].analysis is not None
    assert result.candidates[0].analysis.artifact_path == generated_midi

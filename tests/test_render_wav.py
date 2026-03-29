from __future__ import annotations

import json
from pathlib import Path
import wave

from mido import Message, MetaMessage, MidiFile, MidiTrack

from music2.config import HostConfig
from music2.models import CompileOptions, MidiAnalysisReport, NoteEvent
from music2.playback_modes import build_default_playback_program
from music2.render_wav import RenderWavOptions, render_midi_to_stepper_wav
from music2.compiler import compile_segments


def _write_test_midi(path: Path) -> Path:
    track = MidiTrack()
    track.append(MetaMessage("set_tempo", tempo=500000, time=0))
    track.append(Message("note_on", note=72, velocity=100, time=0))
    track.append(Message("note_off", note=72, velocity=0, time=960))
    mid = MidiFile(ticks_per_beat=480)
    mid.tracks.append(track)
    mid.save(path)
    return path


def _write_low_note_midi(path: Path) -> Path:
    track = MidiTrack()
    track.append(MetaMessage("set_tempo", tempo=500000, time=0))
    track.append(Message("note_on", note=24, velocity=100, time=0))
    track.append(Message("note_off", note=24, velocity=0, time=960))
    mid = MidiFile(ticks_per_beat=480)
    mid.tracks.append(track)
    mid.save(path)
    return path


def _write_reattack_midi(path: Path) -> Path:
    track = MidiTrack()
    track.append(MetaMessage("set_tempo", tempo=500000, time=0))
    track.append(Message("note_on", note=69, velocity=100, time=0))
    track.append(Message("note_off", note=69, velocity=0, time=480))
    track.append(Message("note_on", note=69, velocity=100, time=0))
    track.append(Message("note_off", note=69, velocity=0, time=480))
    mid = MidiFile(ticks_per_beat=480)
    mid.tracks.append(track)
    mid.save(path)
    return path


def _write_pitch_change_midi(path: Path) -> Path:
    track = MidiTrack()
    track.append(MetaMessage("set_tempo", tempo=500000, time=0))
    track.append(Message("note_on", note=69, velocity=100, time=0))
    track.append(Message("note_off", note=69, velocity=0, time=480))
    track.append(Message("note_on", note=72, velocity=100, time=0))
    track.append(Message("note_off", note=72, velocity=0, time=480))
    mid = MidiFile(ticks_per_beat=480)
    mid.tracks.append(track)
    mid.save(path)
    return path


def test_render_midi_to_stepper_wav_writes_wav_and_metadata(tmp_path: Path) -> None:
    midi_path = _write_test_midi(tmp_path / "input.mid")
    out_wav = tmp_path / "render.stepper.wav"
    cfg = HostConfig(
        auto_transpose=False,
        transpose_override=0,
        connected_motors=6,
        min_freq_hz=30.0,
        max_freq_hz=800.0,
    )
    options = RenderWavOptions(sample_rate=8_000, normalize=True)

    result = render_midi_to_stepper_wav(
        midi_path=midi_path,
        cfg=cfg,
        out_wav=out_wav,
        options=options,
    )

    assert result.wav_path.exists()
    assert result.metadata_path.exists()
    assert result.duration_s > 0.0
    assert result.segment_count > 0

    with wave.open(str(result.wav_path), "rb") as wav_file:
        assert wav_file.getnchannels() == 1
        assert wav_file.getsampwidth() == 2
        assert wav_file.getframerate() == 8_000
        assert wav_file.getnframes() > 0

    metadata = json.loads(result.metadata_path.read_text(encoding="utf-8"))
    assert metadata["audio"]["sample_rate"] == 8_000
    assert metadata["command_timeline"]["effective_interval_count"] == result.segment_count
    assert metadata["command_timeline"]["event_group_count"] == metadata["compile"]["event_group_count"]
    assert metadata["playback_program"]["mode_id"] == "full-song"
    assert metadata["playback_program"]["section_count"] == 1
    assert metadata["audio"]["model"] == "stepper_mic_v3_close"
    render_chain = metadata["audio"]["render_chain"]
    assert render_chain["firmware_emulate"] is True
    assert render_chain["run_accel_dhz_per_s"] == options.max_accel_dhz_per_s
    assert render_chain["launch_accel_dhz_per_s"] == options.launch_accel_dhz_per_s
    assert render_chain["launch_start_hz"] == options.launch_start_hz
    assert render_chain["launch_crossover_hz"] == options.launch_crossover_hz
    assert render_chain["room_mix"] == 0.0
    assert render_chain["output_saturation"] == "none"
    assert "room_taps_s" not in render_chain


def test_render_midi_to_stepper_wav_tracks_safety_clamps(tmp_path: Path) -> None:
    midi_path = _write_test_midi(tmp_path / "input.mid")
    out_wav = tmp_path / "clamped.stepper.wav"
    cfg = HostConfig(
        auto_transpose=False,
        transpose_override=0,
        connected_motors=6,
        min_freq_hz=30.0,
        max_freq_hz=800.0,
    )
    options = RenderWavOptions(
        sample_rate=8_000,
        normalize=False,
        safe_max_freq_hz=100.0,
        clamp_frequencies=True,
    )

    result = render_midi_to_stepper_wav(
        midi_path=midi_path,
        cfg=cfg,
        out_wav=out_wav,
        options=options,
    )
    metadata = json.loads(result.metadata_path.read_text(encoding="utf-8"))
    assert metadata["command_timeline"]["safety_clamp_count"] > 0
    assert metadata["command_timeline"]["max_effective_freq_hz"] <= 100.0


def test_render_midi_to_stepper_wav_default_does_not_fold_low_notes(tmp_path: Path) -> None:
    midi_path = _write_low_note_midi(tmp_path / "low.mid")
    out_wav = tmp_path / "low.stepper.wav"
    cfg = HostConfig(
        auto_transpose=False,
        transpose_override=0,
        connected_motors=6,
        min_freq_hz=80.0,
        max_freq_hz=500.0,
    )
    options = RenderWavOptions(sample_rate=8_000, normalize=False)

    result = render_midi_to_stepper_wav(
        midi_path=midi_path,
        cfg=cfg,
        out_wav=out_wav,
        options=options,
    )
    metadata = json.loads(result.metadata_path.read_text(encoding="utf-8"))
    # MIDI note 24 is ~32.7 Hz; if it were folded, we'd observe >= 80 Hz here.
    assert metadata["command_timeline"]["max_effective_freq_hz"] < 80.0
    assert metadata["analysis"]["clamped_note_count"] == 0


def test_render_midi_to_stepper_wav_has_deterministic_timeline_hash(tmp_path: Path) -> None:
    midi_path = _write_test_midi(tmp_path / "input.mid")
    cfg = HostConfig(
        auto_transpose=False,
        transpose_override=0,
        connected_motors=6,
        min_freq_hz=30.0,
        max_freq_hz=800.0,
    )
    options = RenderWavOptions(sample_rate=8_000, normalize=False)

    first = render_midi_to_stepper_wav(
        midi_path=midi_path,
        cfg=cfg,
        out_wav=tmp_path / "a.stepper.wav",
        options=options,
    )
    second = render_midi_to_stepper_wav(
        midi_path=midi_path,
        cfg=cfg,
        out_wav=tmp_path / "b.stepper.wav",
        options=options,
    )

    first_meta = json.loads(first.metadata_path.read_text(encoding="utf-8"))
    second_meta = json.loads(second.metadata_path.read_text(encoding="utf-8"))
    assert (
        first_meta["command_timeline"]["timeline_hash_sha256"]
        == second_meta["command_timeline"]["timeline_hash_sha256"]
    )


def test_render_midi_to_stepper_wav_tracks_direction_flip_boundaries(tmp_path: Path) -> None:
    midi_path = _write_reattack_midi(tmp_path / "reattack.mid")
    options = RenderWavOptions(sample_rate=8_000, normalize=False)

    without_flip = render_midi_to_stepper_wav(
        midi_path=midi_path,
        cfg=HostConfig(
            auto_transpose=False,
            transpose_override=0,
            connected_motors=1,
            min_freq_hz=30.0,
            max_freq_hz=800.0,
            flip_direction_on_note_change=False,
        ),
        out_wav=tmp_path / "no-flip.stepper.wav",
        options=options,
    )
    with_flip = render_midi_to_stepper_wav(
        midi_path=midi_path,
        cfg=HostConfig(
            auto_transpose=False,
            transpose_override=0,
            connected_motors=1,
            min_freq_hz=30.0,
            max_freq_hz=800.0,
            flip_direction_on_note_change=True,
        ),
        out_wav=tmp_path / "flip.stepper.wav",
        options=options,
    )

    without_flip_meta = json.loads(without_flip.metadata_path.read_text(encoding="utf-8"))
    with_flip_meta = json.loads(with_flip.metadata_path.read_text(encoding="utf-8"))
    assert without_flip_meta["command_timeline"]["direction_flip_change_count"] == 0
    assert with_flip_meta["command_timeline"]["direction_flip_change_count"] == 0
    assert (
        without_flip_meta["command_timeline"]["timeline_hash_sha256"]
        == with_flip_meta["command_timeline"]["timeline_hash_sha256"]
    )


def test_render_midi_to_stepper_wav_flips_only_for_pitch_changes(tmp_path: Path) -> None:
    midi_path = _write_pitch_change_midi(tmp_path / "pitch-change.mid")
    options = RenderWavOptions(sample_rate=8_000, normalize=False)

    without_flip = render_midi_to_stepper_wav(
        midi_path=midi_path,
        cfg=HostConfig(
            auto_transpose=False,
            transpose_override=0,
            connected_motors=1,
            min_freq_hz=30.0,
            max_freq_hz=800.0,
            flip_direction_on_note_change=False,
        ),
        out_wav=tmp_path / "pitch-no-flip.stepper.wav",
        options=options,
    )
    with_flip = render_midi_to_stepper_wav(
        midi_path=midi_path,
        cfg=HostConfig(
            auto_transpose=False,
            transpose_override=0,
            connected_motors=1,
            min_freq_hz=30.0,
            max_freq_hz=800.0,
            flip_direction_on_note_change=True,
        ),
        out_wav=tmp_path / "pitch-flip.stepper.wav",
        options=options,
    )

    without_flip_meta = json.loads(without_flip.metadata_path.read_text(encoding="utf-8"))
    with_flip_meta = json.loads(with_flip.metadata_path.read_text(encoding="utf-8"))
    assert without_flip_meta["command_timeline"]["direction_flip_change_count"] == 0
    assert with_flip_meta["command_timeline"]["direction_flip_change_count"] == 1
    assert (
        without_flip_meta["command_timeline"]["timeline_hash_sha256"]
        != with_flip_meta["command_timeline"]["timeline_hash_sha256"]
    )


def test_compile_segments_populates_playback_plan() -> None:
    notes = [
        NoteEvent(
            start_s=0.0,
            end_s=0.5,
            source_note=60,
            transposed_note=60,
            frequency_hz=261.63,
            velocity=100,
            channel=0,
        )
    ]
    compiled = compile_segments(
        notes,
        CompileOptions(
            connected_motors=1,
            idle_mode="idle",
            overflow_mode="steal_quietest",
        ),
    )

    assert compiled.playback_plan is not None
    assert compiled.playback_plan.event_group_count == len(compiled.event_groups)
    assert compiled.playback_plan.shadow_segment_count == len(compiled.segments)
    assert compiled.playback_plan.connected_motors == compiled.connected_motors


def test_default_playback_mode_builds_single_section_program() -> None:
    analysis = MidiAnalysisReport(
        notes=[
            NoteEvent(
                start_s=0.0,
                end_s=0.5,
                source_note=60,
                transposed_note=60,
                frequency_hz=261.63,
                velocity=100,
                channel=0,
            )
        ],
        duration_s=0.5,
        note_count=1,
        max_polyphony=1,
        transpose_semitones=0,
        clamped_note_count=0,
        min_source_note=60,
        max_source_note=60,
    )
    compiled = compile_segments(
        analysis.notes,
        CompileOptions(
            connected_motors=1,
            idle_mode="idle",
            overflow_mode="steal_quietest",
        ),
    )

    program = build_default_playback_program(analysis=analysis, compiled=compiled)

    assert len(program.sections) == 1
    assert program.playback_plan is compiled.playback_plan
    assert program.sections[0].playback_plan.event_group_count == len(compiled.event_groups)

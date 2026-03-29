from __future__ import annotations

import json
import random
from pathlib import Path

from .backends import (
    choose_python_runtime_warning,
    ensure_ffmpeg_available,
    list_backends_used,
    separate_stems,
    transcribe_speech_prosody,
    transcribe_with_basic_pitch,
    transcribe_with_piano_transcription,
    transcribe_with_mt3_command,
)
from .fusion import fuse_candidates
from .midi_writer import MidiWriteSettings, write_expressive_midi, write_motor_midi
from .polyphony import compute_max_polyphony, enforce_polyphony_cap
from .postprocess import apply_music_postprocessing, apply_speech_postprocessing
from .types import ConversionConfig, ConversionResult, ConversionStats


def _log(msg: str) -> None:
    print(f"[transcribe] {msg}", flush=True)


def convert_mp3_to_dual_midi(
    input_audio: str | Path,
    *,
    output_dir: str | Path,
    config: ConversionConfig,
    cache_dir: str | Path = ".cache/transcribe",
) -> ConversionResult:
    random.seed(config.seed)

    input_path = Path(input_audio).expanduser().resolve()
    if not input_path.exists():
        raise RuntimeError(f"input audio not found: {input_path}")
    if input_path.suffix.lower() not in {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg"}:
        raise RuntimeError(f"unsupported audio extension: {input_path.suffix}")

    _log(f"Input: {input_path.name}")
    ensure_ffmpeg_available()
    warnings = choose_python_runtime_warning()
    out_dir = Path(output_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_path = Path(cache_dir).expanduser().resolve()
    cache_path.mkdir(parents=True, exist_ok=True)

    notes_music_candidates = 0
    notes_speech_candidates = 0
    fused_after_postprocess = []
    used_piano_transcription = False
    used_mt3 = False
    used_torchcrepe = False
    used_pyin = False

    if config.mode == "music":
        _log("Separating stems with Demucs...")
        _, accompaniment_path, demucs_warnings = separate_stems(
            input_path,
            cache_dir=cache_path,
            quality=config.quality,
            use_demucs=config.use_demucs,
        )
        warnings.extend(demucs_warnings)
        if demucs_warnings:
            _log(f"  Demucs: skipped ({demucs_warnings[0]})")
        else:
            _log("  Demucs: done")

        _log("Transcribing with piano-transcription-inference (accompaniment)...")
        piano_notes, piano_warnings = transcribe_with_piano_transcription(
            accompaniment_path,
            device=config.device,
            cache_dir=cache_path,
        )
        warnings.extend(piano_warnings)
        used_piano_transcription = bool(piano_notes)
        _log(f"  Got {len(piano_notes)} notes")

        _log("Transcribing with basic-pitch (full mix)...")
        basic_notes, basic_warnings = transcribe_with_basic_pitch(input_path)
        warnings.extend(basic_warnings)
        _log(f"  Got {len(basic_notes)} notes")

        if config.mt3_command:
            _log("Running MT3 transcription...")
        mt3_notes, mt3_warnings = transcribe_with_mt3_command(
            input_audio=input_path,
            mt3_command=config.mt3_command,
            cache_dir=cache_path,
        )
        warnings.extend(mt3_warnings)
        used_mt3 = bool(mt3_notes)
        if config.mt3_command:
            _log(f"  Got {len(mt3_notes)} notes")

        _log("Fusing music candidates...")
        music_fused = fuse_candidates(piano_notes, basic_notes, tolerance_s=0.03)
        if mt3_notes:
            music_fused = fuse_candidates(music_fused, mt3_notes, tolerance_s=0.04)
        _log(f"  {len(music_fused)} fused music notes")
        notes_music_candidates = len(music_fused)

        _log("Applying music post-processing...")
        fused_after_postprocess = apply_music_postprocessing(music_fused, audio_path=input_path, config=config)
        _log(f"  {len(fused_after_postprocess)} notes after post-processing")
    else:
        _log("Transcribing speech prosody...")
        speech_notes, speech_warnings = transcribe_speech_prosody(
            input_path,
            device=config.device,
            config=config,
        )
        warnings.extend(speech_warnings)
        used_torchcrepe = any("speech_torchcrepe" in note.source for note in speech_notes)
        used_pyin = any("speech_pyin" in note.source for note in speech_notes)
        backend = "torchcrepe" if used_torchcrepe else "pyin" if used_pyin else "none"
        _log(f"  {len(speech_notes)} speech notes (via {backend})")
        notes_speech_candidates = len(speech_notes)

        _log("Applying speech post-processing...")
        fused_after_postprocess = apply_speech_postprocessing(speech_notes, config=config)
        _log(f"  {len(fused_after_postprocess)} notes after post-processing")

    if not fused_after_postprocess:
        raise RuntimeError(
            "transcription produced no notes. Install transcribe extras or provide --mt3-cmd. "
            "See docs/mp3_to_midi_best.md."
        )

    _log(f"Enforcing polyphony cap ({config.max_polyphony})...")
    capped, cap_stats = enforce_polyphony_cap(fused_after_postprocess, cap=config.max_polyphony)
    max_polyphony_out = compute_max_polyphony(capped)
    if max_polyphony_out > config.max_polyphony:
        raise RuntimeError("internal error: polyphony cap enforcement exceeded requested limit")
    _log(f"  {len(capped)} notes after cap (dropped {cap_stats.dropped_note_count})")

    _log("Writing MIDI files...")
    midi_settings = MidiWriteSettings(pitch_bend_range_semitones=config.pitch_bend_range_semitones)
    base_name = input_path.stem
    motor_path = out_dir / f"{base_name}.motor6.mid"
    expressive_path = out_dir / f"{base_name}.expressive6.mid"
    write_motor_midi(capped, output_path=motor_path, settings=midi_settings)
    write_expressive_midi(capped, output_path=expressive_path, settings=midi_settings, max_channels=config.max_polyphony)

    backends = list_backends_used(
        config,
        used_piano_transcription=used_piano_transcription,
        used_mt3=used_mt3,
        used_torchcrepe=used_torchcrepe,
        used_pyin=used_pyin,
    )
    unique_warnings = tuple(sorted(set(warnings)))
    report_path: Path | None = None
    stats = ConversionStats(
        input_path=str(input_path),
        motor_midi_path=str(motor_path),
        expressive_midi_path=str(expressive_path),
        report_path=None,
        max_polyphony_requested=config.max_polyphony,
        max_polyphony_output=max_polyphony_out,
        notes_music_candidates=notes_music_candidates,
        notes_speech_candidates=notes_speech_candidates,
        notes_fused_before_cap=len(fused_after_postprocess),
        notes_after_cap=len(capped),
        dropped_by_polyphony_cap=cap_stats.dropped_note_count,
        transcriber_backends=backends,
        warnings=unique_warnings,
    )

    if config.write_report:
        report_path = out_dir / f"{base_name}.report.json"
        payload = stats.to_json_dict()
        payload["report_generated_by"] = "scripts/mp3_to_midi_best.py"
        with report_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        stats = ConversionStats(**{**stats.to_json_dict(), "report_path": str(report_path)})

    _log("Done.")
    return ConversionResult(
        motor_midi_path=motor_path,
        expressive_midi_path=expressive_path,
        report_path=report_path,
        stats=stats,
    )

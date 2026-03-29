from __future__ import annotations

import hashlib
import os
import shlex
import subprocess
import sys
from pathlib import Path

from mido import MidiFile

from ..midi_filter import is_non_playable_gm_program, is_non_playable_midi_part
from .speech import segment_speech_pitch_track
from .types import CandidateNote, ConversionConfig


def _stable_key(path: Path) -> str:
    file_stat = path.stat()
    payload = f"{path.resolve()}::{file_stat.st_size}::{int(file_stat.st_mtime)}".encode("utf-8")
    return hashlib.sha1(payload).hexdigest()[:16]


def ensure_ffmpeg_available() -> None:
    proc = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError("ffmpeg is required but was not found in PATH")


def separate_stems(
    input_audio: Path,
    *,
    cache_dir: Path,
    quality: str,
    use_demucs: bool,
) -> tuple[Path, Path, list[str]]:
    warnings: list[str] = []
    if not use_demucs:
        warnings.append("Demucs disabled by configuration; using full mix for all branches.")
        return input_audio, input_audio, warnings

    model = {"ultra": "htdemucs_ft", "high": "htdemucs", "balanced": "mdx_q"}.get(quality, "htdemucs")
    out_root = cache_dir / "demucs"
    out_root.mkdir(parents=True, exist_ok=True)
    run_key = _stable_key(input_audio)
    run_dir = out_root / run_key
    vocals_path = run_dir / model / input_audio.stem / "vocals.wav"
    accompaniment_path = run_dir / model / input_audio.stem / "no_vocals.wav"
    if vocals_path.exists() and accompaniment_path.exists():
        return vocals_path, accompaniment_path, warnings

    cmd = [
        sys.executable,
        "-m",
        "demucs.separate",
        "--two-stems",
        "vocals",
        "-n",
        model,
        "-o",
        str(run_dir),
        str(input_audio),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0 or not vocals_path.exists() or not accompaniment_path.exists():
        warnings.append("Demucs separation unavailable or failed; using full mix fallback.")
        return input_audio, input_audio, warnings
    return vocals_path, accompaniment_path, warnings


def transcribe_with_basic_pitch(audio_path: Path) -> tuple[list[CandidateNote], list[str]]:
    warnings: list[str] = []
    try:
        from basic_pitch.inference import predict  # type: ignore[import-not-found]
    except ImportError as exc:
        warnings.append(f"basic-pitch unavailable ({exc}); music AMT backend skipped.")
        return [], warnings

    notes: list[CandidateNote] = []
    try:
        _, midi_data, note_events = predict(str(audio_path))
    except Exception as exc:
        warnings.append(f"basic-pitch failed: {exc}")
        return [], warnings

    if midi_data is not None and hasattr(midi_data, "instruments"):
        for instrument in getattr(midi_data, "instruments", []):
            instrument_program = getattr(instrument, "program", None)
            instrument_is_drum = bool(getattr(instrument, "is_drum", False))
            if is_non_playable_midi_part(program=instrument_program, is_drum=instrument_is_drum):
                continue
            for note in getattr(instrument, "notes", []):
                start_s = float(getattr(note, "start", 0.0))
                end_s = float(getattr(note, "end", start_s))
                pitch = int(getattr(note, "pitch", 60))
                velocity = int(getattr(note, "velocity", 96))
                if end_s <= start_s:
                    continue
                notes.append(
                    CandidateNote(
                        start_s=start_s,
                        end_s=end_s,
                        midi_note=pitch,
                        velocity=velocity,
                        confidence=min(1.0, max(0.0, velocity / 127.0)),
                        source="basic_pitch",
                    )
                )

    if not notes and isinstance(note_events, list):
        for event in note_events:
            if _event_is_non_playable(event):
                continue
            parsed = _parse_basic_pitch_event(event)
            if parsed is not None:
                notes.append(parsed)

    notes.sort(key=lambda note: (note.start_s, note.end_s, note.midi_note, -note.confidence))
    return notes, warnings


def _parse_basic_pitch_event(event: object) -> CandidateNote | None:
    if isinstance(event, dict):
        start_s = float(event.get("start_time_s", event.get("start", 0.0)))
        end_s = float(event.get("end_time_s", event.get("end", start_s)))
        midi_note = int(event.get("pitch_midi", event.get("pitch", 60)))
        confidence = float(event.get("confidence", 0.7))
        velocity = int(max(1, min(127, round(20 + confidence * 107))))
        if end_s <= start_s:
            return None
        return CandidateNote(
            start_s=start_s,
            end_s=end_s,
            midi_note=midi_note,
            velocity=velocity,
            confidence=max(0.0, min(1.0, confidence)),
            source="basic_pitch",
        )

    if isinstance(event, (tuple, list)) and len(event) >= 3:
        start_s = float(event[0])
        end_s = float(event[1])
        midi_note = int(event[2])
        confidence = float(event[3]) if len(event) >= 4 else 0.7
        velocity = int(max(1, min(127, round(20 + confidence * 107))))
        if end_s <= start_s:
            return None
        return CandidateNote(
            start_s=start_s,
            end_s=end_s,
            midi_note=midi_note,
            velocity=velocity,
            confidence=max(0.0, min(1.0, confidence)),
            source="basic_pitch",
        )
    return None


def transcribe_with_piano_transcription(
    audio_path: Path,
    *,
    device: str = "auto",
    cache_dir: Path | None = None,
) -> tuple[list[CandidateNote], list[str]]:
    warnings: list[str] = []
    try:
        import torch  # type: ignore[import-not-found]
        from piano_transcription_inference import PianoTranscription, sample_rate  # type: ignore[import-not-found]
    except ImportError as exc:
        warnings.append(f"piano-transcription-inference unavailable ({exc}); skipping piano backend.")
        return [], warnings

    try:
        import librosa  # type: ignore[import-not-found]
        import numpy as np  # type: ignore[import-not-found]
    except ImportError as exc:
        warnings.append(f"librosa/numpy unavailable ({exc}); skipping piano backend.")
        return [], warnings

    try:
        target_device = _resolve_torch_device(torch_module=torch, requested=device)

        # Cache the output MIDI to avoid re-running inference
        if cache_dir is not None:
            out_midi = cache_dir / "piano_transcription" / f"{_stable_key(audio_path)}.mid"
            out_midi.parent.mkdir(parents=True, exist_ok=True)
            if out_midi.exists():
                return read_midi_as_candidate_notes(out_midi, source="piano_transcription"), warnings
        else:
            import tempfile
            out_midi = Path(tempfile.mktemp(suffix=".mid"))

        audio, _ = librosa.load(str(audio_path), sr=sample_rate, mono=True)
        transcriptor = PianoTranscription(device=target_device)
        transcriptor.transcribe(audio, str(out_midi))
        notes = read_midi_as_candidate_notes(out_midi, source="piano_transcription")
        return notes, warnings
    except Exception as exc:
        warnings.append(f"piano-transcription-inference failed: {exc}")
        return [], warnings


def transcribe_with_mt3_command(
    *,
    input_audio: Path,
    mt3_command: str | None,
    cache_dir: Path,
) -> tuple[list[CandidateNote], list[str]]:
    warnings: list[str] = []
    if not mt3_command:
        return [], warnings

    output_midi = cache_dir / "mt3" / f"{_stable_key(input_audio)}.mid"
    output_midi.parent.mkdir(parents=True, exist_ok=True)
    formatted = mt3_command.format(input=shlex.quote(str(input_audio)), output=shlex.quote(str(output_midi)))
    proc = subprocess.run(formatted, shell=True, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        warnings.append("MT3 command failed; continuing without MT3 notes.")
        return [], warnings
    if not output_midi.exists():
        warnings.append("MT3 command completed without output MIDI; continuing without MT3 notes.")
        return [], warnings
    return read_midi_as_candidate_notes(output_midi, source="mt3"), warnings


def transcribe_speech_prosody(
    vocals_audio: Path,
    *,
    device: str,
    config: ConversionConfig,
) -> tuple[list[CandidateNote], list[str]]:
    warnings: list[str] = []
    torchcrepe_notes, torchcrepe_warnings = _speech_with_torchcrepe(vocals_audio, device=device, config=config)
    warnings.extend(torchcrepe_warnings)
    if torchcrepe_notes:
        return torchcrepe_notes, warnings

    pyin_notes, pyin_warnings = _speech_with_librosa_pyin(vocals_audio, config=config)
    warnings.extend(pyin_warnings)
    return pyin_notes, warnings


def _speech_with_torchcrepe(
    vocals_audio: Path,
    *,
    device: str,
    config: ConversionConfig,
) -> tuple[list[CandidateNote], list[str]]:
    warnings: list[str] = []
    try:
        import numpy as np  # type: ignore[import-not-found]
        import torch  # type: ignore[import-not-found]
        import torchcrepe  # type: ignore[import-not-found]
    except ImportError as exc:
        warnings.append(f"torchcrepe backend unavailable ({exc}).")
        return [], warnings

    try:
        audio, sr = torchcrepe.load.audio(str(vocals_audio))
        # torchcrepe expects mono [1, N]; mix down if stereo
        if audio.dim() == 2 and audio.shape[0] > 1:
            audio = audio.mean(dim=0, keepdim=True)
        elif audio.dim() == 1:
            audio = audio.unsqueeze(0)
        hop_length = max(1, int(sr / 100.0))
        model = "full"
        target_device = _resolve_torch_device(torch_module=torch, requested=device)
        pitch, periodicity = torchcrepe.predict(
            audio,
            sr,
            hop_length,
            50.0,
            1000.0,
            model,
            batch_size=1024,
            device=target_device,
            return_periodicity=True,
        )
        pitch_np = pitch.detach().cpu().numpy().reshape(-1)
        periodicity_np = periodicity.detach().cpu().numpy().reshape(-1)
        times = np.arange(len(pitch_np), dtype=float) * (hop_length / float(sr))
        notes = segment_speech_pitch_track(
            times_s=times.tolist(),
            freq_hz=pitch_np.tolist(),
            confidence=periodicity_np.tolist(),
            min_confidence=config.speech_start_confidence,
            sustain_confidence=config.speech_sustain_confidence,
            max_pitch_jump_semitones=config.speech_max_pitch_jump_semitones,
            median_filter_window=config.speech_median_filter_window,
            min_note_duration_s=max(0.08, config.min_note_duration_s),
            source="speech_torchcrepe",
        )
        return notes, warnings
    except Exception as exc:
        warnings.append(f"torchcrepe backend failed: {exc}")
        return [], warnings


def _speech_with_librosa_pyin(vocals_audio: Path, *, config: ConversionConfig) -> tuple[list[CandidateNote], list[str]]:
    warnings: list[str] = []
    try:
        import librosa  # type: ignore[import-not-found]
        import numpy as np  # type: ignore[import-not-found]
    except Exception:
        warnings.append("librosa pYIN backend unavailable.")
        return [], warnings

    try:
        y, sr = librosa.load(str(vocals_audio), sr=16000, mono=True)
        f0, voiced_flag, voiced_probs = librosa.pyin(
            y,
            sr=sr,
            fmin=librosa.note_to_hz("C2"),
            fmax=librosa.note_to_hz("C7"),
            frame_length=1024,
            hop_length=160,
        )
        f0_filled = np.nan_to_num(f0, nan=0.0, posinf=0.0, neginf=0.0)
        confidence = np.asarray(voiced_probs, dtype=float) * np.asarray(voiced_flag, dtype=float)
        times = librosa.times_like(f0_filled, sr=sr, hop_length=160)
        notes = segment_speech_pitch_track(
            times_s=times.tolist(),
            freq_hz=f0_filled.tolist(),
            confidence=confidence.tolist(),
            min_confidence=config.speech_start_confidence,
            sustain_confidence=max(0.01, min(config.speech_start_confidence, config.speech_sustain_confidence)),
            max_pitch_jump_semitones=config.speech_max_pitch_jump_semitones,
            median_filter_window=config.speech_median_filter_window,
            min_note_duration_s=max(0.08, config.min_note_duration_s),
            source="speech_pyin",
        )
        return notes, warnings
    except Exception as exc:
        warnings.append(f"librosa pYIN backend failed: {exc}")
        return [], warnings


def read_midi_as_candidate_notes(midi_path: Path, *, source: str) -> list[CandidateNote]:
    mid = MidiFile(str(midi_path))
    notes: list[CandidateNote] = []
    abs_time_s = 0.0
    active: dict[tuple[int, int], tuple[float, int]] = {}
    program_by_channel: dict[int, int] = {}
    # Iterating over MidiFile yields merged playback events with time in seconds.
    for msg in mid:
        abs_time_s += float(getattr(msg, "time", 0.0))
        msg_type = getattr(msg, "type", "")
        channel = int(getattr(msg, "channel", 0))
        if msg_type == "program_change":
            program_by_channel[channel] = int(getattr(msg, "program", 0))
            continue
        if msg_type not in {"note_on", "note_off"}:
            continue

        note = int(getattr(msg, "note", 0))
        velocity = int(getattr(msg, "velocity", 0))
        key = (channel, note)
        if msg_type == "note_on" and velocity > 0:
            if is_non_playable_midi_part(channel=channel, program=program_by_channel.get(channel)):
                continue
            active[key] = (abs_time_s, velocity)
            continue

        if key not in active:
            continue
        start_s, start_vel = active.pop(key)
        if abs_time_s <= start_s:
            continue
        notes.append(
            CandidateNote(
                start_s=start_s,
                end_s=abs_time_s,
                midi_note=note,
                velocity=max(1, min(127, start_vel)),
                confidence=max(0.0, min(1.0, start_vel / 127.0)),
                source=source,
            )
        )

    notes.sort(key=lambda note: (note.start_s, note.end_s, note.midi_note))
    return notes


def _event_is_non_playable(event: object) -> bool:
    if not isinstance(event, dict):
        return False
    if bool(event.get("is_drum", False)):
        return True
    program = event.get("program")
    if program is not None and is_non_playable_gm_program(int(program)):
        return True
    channel = event.get("channel")
    if channel is not None and is_non_playable_midi_part(channel=int(channel)):
        return True
    return False


def _resolve_torch_device(*, torch_module: object, requested: str) -> str:
    if requested and requested not in {"auto", ""}:
        return requested

    if hasattr(torch_module, "cuda") and getattr(torch_module.cuda, "is_available")():
        return "cuda:0"
    if hasattr(torch_module.backends, "mps") and getattr(torch_module.backends.mps, "is_available")():
        return "mps"
    return "cpu"


def choose_python_runtime_warning() -> list[str]:
    if sys.version_info[:2] >= (3, 14):
        return ["Python 3.14 detected. Use Python 3.13 for best transcription dependency compatibility."]
    return []


def environment_summary() -> dict[str, str]:
    return {
        "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "cwd": str(Path.cwd()),
        "platform": os.uname().sysname if hasattr(os, "uname") else sys.platform,
    }


def list_backends_used(
    config: ConversionConfig,
    *,
    used_piano_transcription: bool = False,
    used_mt3: bool = False,
    used_torchcrepe: bool = False,
    used_pyin: bool = False,
) -> tuple[str, ...]:
    names: list[str] = []
    if config.mode == "music":
        if used_piano_transcription:
            names.append("piano_transcription")
        names.append("basic_pitch")
        names.append("demucs" if config.use_demucs else "demucs_disabled")
        if used_mt3:
            names.append("mt3_command")
    else:
        if used_torchcrepe:
            names.append("torchcrepe")
        if used_pyin:
            names.append("librosa_pyin")
    return tuple(names)

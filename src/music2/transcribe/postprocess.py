from __future__ import annotations

from bisect import bisect_left
from pathlib import Path

from .types import CandidateNote, ConversionConfig


def filter_short_notes(notes: list[CandidateNote], min_duration_s: float = 0.05) -> list[CandidateNote]:
    threshold = max(0.0, float(min_duration_s))
    return [note for note in notes if note.duration_s >= threshold]


def filter_low_confidence(notes: list[CandidateNote], min_confidence: float = 0.3) -> list[CandidateNote]:
    threshold = max(0.0, min(1.0, float(min_confidence)))
    return [note for note in notes if float(note.confidence) >= threshold]


def quantize_onsets_to_beats(
    notes: list[CandidateNote],
    audio_path: Path,
    *,
    max_shift_s: float = 0.03,
) -> list[CandidateNote]:
    if not notes:
        return []

    try:
        import librosa  # type: ignore[import-not-found]
    except ImportError:
        return list(notes)

    y, sr = librosa.load(str(audio_path), sr=None, mono=True)
    _tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
    if len(beat_frames) == 0:
        return list(notes)
    beat_times = sorted(float(t) for t in librosa.frames_to_time(beat_frames, sr=sr))
    if not beat_times:
        return list(notes)

    shift_limit_s = max(0.0, float(max_shift_s))
    snapped: list[CandidateNote] = []
    for note in notes:
        nearest = _nearest_time(beat_times, note.start_s)
        if nearest is None or abs(nearest - note.start_s) > shift_limit_s:
            snapped.append(note)
            continue

        duration = max(0.01, note.duration_s)
        snapped.append(
            CandidateNote(
                start_s=nearest,
                end_s=nearest + duration,
                midi_note=note.midi_note,
                velocity=note.velocity,
                confidence=note.confidence,
                source=note.source,
                bends=note.bends,
            ).clamped()
        )
    snapped.sort(key=lambda note: (note.start_s, note.end_s, note.midi_note))
    return snapped


def compress_velocity(notes: list[CandidateNote]) -> list[CandidateNote]:
    compressed: list[CandidateNote] = []
    for note in notes:
        scaled = 64 + 63 * ((max(1, min(127, note.velocity)) / 127.0) ** 0.5)
        compressed.append(
            CandidateNote(
                start_s=note.start_s,
                end_s=note.end_s,
                midi_note=note.midi_note,
                velocity=int(max(1, min(127, round(scaled)))),
                confidence=note.confidence,
                source=note.source,
                bends=note.bends,
            ).clamped()
        )
    return compressed


def correct_octave_errors(notes: list[CandidateNote]) -> list[CandidateNote]:
    if len(notes) < 3:
        return sorted(notes, key=lambda note: (note.start_s, note.end_s, note.midi_note))

    ordered = sorted(notes, key=lambda note: (note.start_s, note.end_s, note.midi_note))
    patched: list[CandidateNote] = list(ordered)
    for idx in range(1, len(patched) - 1):
        prev_note = patched[idx - 1]
        cur_note = patched[idx]
        next_note = patched[idx + 1]
        if cur_note.start_s - prev_note.end_s > 0.05:
            continue
        if next_note.start_s - cur_note.end_s > 0.05:
            continue
        if abs(prev_note.midi_note - next_note.midi_note) > 2:
            continue
        if abs(cur_note.midi_note - prev_note.midi_note) not in {11, 12, 13}:
            continue
        if abs(cur_note.midi_note - next_note.midi_note) not in {11, 12, 13}:
            continue

        target_note = int(round((prev_note.midi_note + next_note.midi_note) / 2.0))
        patched[idx] = CandidateNote(
            start_s=cur_note.start_s,
            end_s=cur_note.end_s,
            midi_note=target_note,
            velocity=cur_note.velocity,
            confidence=cur_note.confidence,
            source=cur_note.source,
            bends=cur_note.bends,
        ).clamped()

    patched.sort(key=lambda note: (note.start_s, note.end_s, note.midi_note))
    return patched


def apply_music_postprocessing(
    notes: list[CandidateNote],
    audio_path: Path,
    config: ConversionConfig,
) -> list[CandidateNote]:
    processed = filter_short_notes(notes, min_duration_s=config.min_note_duration_s)
    processed = filter_low_confidence(processed, min_confidence=config.min_confidence)
    processed = correct_octave_errors(processed)
    if config.quantize_to_beats:
        processed = quantize_onsets_to_beats(
            processed,
            audio_path=audio_path,
            max_shift_s=config.beat_quantize_max_shift_s,
        )
    if config.velocity_compression:
        processed = compress_velocity(processed)
    return processed


def apply_speech_postprocessing(
    notes: list[CandidateNote],
    config: ConversionConfig,
) -> list[CandidateNote]:
    processed = filter_short_notes(notes, min_duration_s=max(0.08, config.min_note_duration_s))
    processed = filter_low_confidence(processed, min_confidence=config.min_confidence)
    processed = correct_octave_errors(processed)
    return processed


def _nearest_time(times: list[float], target: float) -> float | None:
    if not times:
        return None
    pos = bisect_left(times, target)
    candidates: list[float] = []
    if pos < len(times):
        candidates.append(times[pos])
    if pos > 0:
        candidates.append(times[pos - 1])
    if not candidates:
        return None
    return min(candidates, key=lambda t: abs(t - target))

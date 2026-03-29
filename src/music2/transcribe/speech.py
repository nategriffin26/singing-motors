from __future__ import annotations

import math
from typing import Sequence

from .types import CandidateNote, PitchBendPoint


def hz_to_midi(freq_hz: float) -> float:
    if freq_hz <= 0.0:
        raise ValueError("frequency must be positive")
    return 69.0 + 12.0 * math.log2(freq_hz / 440.0)


def segment_speech_pitch_track(
    *,
    times_s: Sequence[float],
    freq_hz: Sequence[float],
    confidence: Sequence[float],
    min_confidence: float = 0.35,
    sustain_confidence: float = 0.20,
    max_pitch_jump_semitones: float = 1.5,
    min_note_duration_s: float = 0.08,
    median_filter_window: int = 5,
    source: str = "speech",
) -> list[CandidateNote]:
    if not (len(times_s) == len(freq_hz) == len(confidence)):
        raise ValueError("times_s, freq_hz, confidence must have equal length")
    if len(times_s) < 2:
        return []

    hop_s = max(0.001, float(times_s[1]) - float(times_s[0]))
    voiced = _compute_hysteresis_voicing(
        freq_hz=freq_hz,
        confidence=confidence,
        min_confidence=min_confidence,
        sustain_confidence=sustain_confidence,
    )
    filtered_freq_hz = _median_filter_freq(
        freq_hz=freq_hz,
        voiced=voiced,
        window=max(1, int(median_filter_window)),
    )
    midi_track: list[float | None] = []
    for idx, is_voiced in enumerate(voiced):
        if not is_voiced:
            midi_track.append(None)
            continue
        try:
            midi_track.append(hz_to_midi(float(filtered_freq_hz[idx])))
        except ValueError:
            midi_track.append(None)
    _correct_octave_jumps(midi_track=midi_track, hop_s=hop_s)

    notes: list[CandidateNote] = []
    segment_start: int | None = None
    for idx, midi_val in enumerate(midi_track):
        if midi_val is None:
            if segment_start is not None:
                notes.extend(
                    _emit_segment(
                        segment_start=segment_start,
                        segment_end=idx,
                        times_s=times_s,
                        midi_track=midi_track,
                        confidence=confidence,
                        max_pitch_jump_semitones=max_pitch_jump_semitones,
                        min_note_duration_s=min_note_duration_s,
                        source=source,
                        hop_s=hop_s,
                    )
                )
                segment_start = None
            continue

        if segment_start is None:
            segment_start = idx

    if segment_start is not None:
        notes.extend(
            _emit_segment(
                segment_start=segment_start,
                segment_end=len(midi_track),
                times_s=times_s,
                midi_track=midi_track,
                confidence=confidence,
                max_pitch_jump_semitones=max_pitch_jump_semitones,
                min_note_duration_s=min_note_duration_s,
                source=source,
                hop_s=hop_s,
            )
        )

    notes.sort(key=lambda note: (note.start_s, note.end_s, note.midi_note))
    return notes


def _emit_segment(
    *,
    segment_start: int,
    segment_end: int,
    times_s: Sequence[float],
    midi_track: Sequence[float | None],
    confidence: Sequence[float],
    max_pitch_jump_semitones: float,
    min_note_duration_s: float,
    source: str,
    hop_s: float,
) -> list[CandidateNote]:
    notes: list[CandidateNote] = []
    if segment_end - segment_start <= 0:
        return notes

    chunk_start = segment_start
    for idx in range(segment_start + 1, segment_end):
        prev = midi_track[idx - 1]
        cur = midi_track[idx]
        if prev is None or cur is None:
            continue
        if abs(cur - prev) >= max_pitch_jump_semitones:
            built = _build_note(
                chunk_start,
                idx,
                times_s,
                midi_track,
                confidence,
                source,
                hop_s,
                min_note_duration_s,
            )
            if built is not None:
                notes.append(built)
            chunk_start = idx

    final = _build_note(
        chunk_start,
        segment_end,
        times_s,
        midi_track,
        confidence,
        source,
        hop_s,
        min_note_duration_s,
    )
    if final is not None:
        notes.append(final)
    return notes


def _build_note(
    start_idx: int,
    end_idx: int,
    times_s: Sequence[float],
    midi_track: Sequence[float | None],
    confidence: Sequence[float],
    source: str,
    hop_s: float,
    min_note_duration_s: float,
) -> CandidateNote | None:
    values = [midi_track[idx] for idx in range(start_idx, end_idx) if midi_track[idx] is not None]
    if not values:
        return None

    values_f = [float(val) for val in values]
    values_f.sort()
    center = values_f[len(values_f) // 2]
    midi_note = int(round(center))
    if end_idx <= start_idx:
        return None

    bends: list[PitchBendPoint] = []
    conf_slice = []
    for idx in range(start_idx, end_idx):
        midi_val = midi_track[idx]
        if midi_val is None:
            continue
        conf = max(0.0, min(1.0, float(confidence[idx])))
        conf_slice.append(conf)
        bends.append(
            PitchBendPoint(
                time_s=float(times_s[idx]),
                semitones=float(midi_val - midi_note),
                confidence=conf,
            )
        )

    note_conf = sum(conf_slice) / max(1, len(conf_slice))
    velocity = int(max(1, min(127, round(20 + note_conf * 107))))
    start_s = max(0.0, float(times_s[start_idx]))
    end_index = min(len(times_s) - 1, end_idx - 1)
    end_s = max(start_s + 0.01, float(times_s[end_index]) + hop_s)
    if end_s - start_s < min_note_duration_s:
        return None

    return CandidateNote(
        start_s=start_s,
        end_s=end_s,
        midi_note=midi_note,
        velocity=velocity,
        confidence=note_conf,
        source=source,
        bends=tuple(bends),
    )


def _compute_hysteresis_voicing(
    *,
    freq_hz: Sequence[float],
    confidence: Sequence[float],
    min_confidence: float,
    sustain_confidence: float,
) -> list[bool]:
    voiced: list[bool] = []
    in_voiced = False
    for idx in range(len(freq_hz)):
        conf = float(confidence[idx])
        freq = float(freq_hz[idx])
        valid = freq > 0.0
        if not valid:
            in_voiced = False
            voiced.append(False)
            continue

        threshold = sustain_confidence if in_voiced else min_confidence
        is_voiced = conf >= threshold
        if not is_voiced:
            in_voiced = False
            voiced.append(False)
            continue

        in_voiced = True
        voiced.append(True)
    return voiced


def _median_filter_freq(*, freq_hz: Sequence[float], voiced: Sequence[bool], window: int) -> list[float]:
    if window <= 1:
        return [float(val) for val in freq_hz]

    radius = max(0, window // 2)
    filtered: list[float] = []
    size = len(freq_hz)
    for idx in range(size):
        if not voiced[idx]:
            filtered.append(float(freq_hz[idx]))
            continue

        low = max(0, idx - radius)
        high = min(size, idx + radius + 1)
        window_vals = [
            float(freq_hz[pos])
            for pos in range(low, high)
            if voiced[pos] and float(freq_hz[pos]) > 0.0
        ]
        if not window_vals:
            filtered.append(float(freq_hz[idx]))
            continue
        window_vals.sort()
        filtered.append(window_vals[len(window_vals) // 2])
    return filtered


def _correct_octave_jumps(*, midi_track: list[float | None], hop_s: float) -> None:
    if len(midi_track) < 3:
        return

    max_gap_frames = max(1, int(round(0.05 / max(hop_s, 1e-6))))
    for idx, cur in enumerate(midi_track):
        if cur is None:
            continue

        prev = _nearest_voiced(midi_track, idx, -1, max_gap_frames)
        next_val = _nearest_voiced(midi_track, idx, +1, max_gap_frames)
        if prev is None or next_val is None:
            continue

        # Correct isolated octave flips where neighbors agree and current frame jumps by ~12 semitones.
        if abs(prev - next_val) > 1.5:
            continue
        if not (11.0 <= abs(cur - prev) <= 13.0):
            continue
        if not (11.0 <= abs(cur - next_val) <= 13.0):
            continue
        target = 0.5 * (prev + next_val)
        shift = 12.0 if target > cur else -12.0
        midi_track[idx] = cur + shift


def _nearest_voiced(
    midi_track: Sequence[float | None],
    idx: int,
    direction: int,
    max_gap_frames: int,
) -> float | None:
    cur = idx + direction
    steps = 0
    while 0 <= cur < len(midi_track) and steps < max_gap_frames:
        value = midi_track[cur]
        if value is not None:
            return float(value)
        cur += direction
        steps += 1
    return None

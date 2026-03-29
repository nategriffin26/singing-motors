from __future__ import annotations

import math
from bisect import bisect_right
import logging
from collections import defaultdict, deque
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable

import mido

from .midi_filter import is_non_playable_midi_part
from .models import MidiAnalysisReport, NoteEvent

DEFAULT_TEMPO = 500000
_LEADING_SILENCE_THRESHOLD_S = 0.5
_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class TempoPoint:
    tick: int
    seconds: float
    tempo: int


@dataclass(frozen=True)
class TempoMap:
    points: list[TempoPoint]
    ticks_per_beat: int


_NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


def midi_note_to_freq(note: int) -> float:
    return 440.0 * (2.0 ** ((note - 69) / 12.0))


def freq_to_note_name(hz: float) -> str:
    """Convert frequency to note name. Returns '-- ' for silence, e.g. 'C4 ', 'A#3'."""
    if hz < 1.0:
        return "-- "
    midi_note = round(12.0 * math.log2(hz / 440.0) + 69)
    name = _NOTE_NAMES[midi_note % 12]
    octave = (midi_note // 12) - 1
    result = f"{name}{octave}"
    return result if len(result) == 3 else f"{result} "


def _collect_events(mid: mido.MidiFile) -> list[tuple[int, int, int, mido.Message]]:
    events: list[tuple[int, int, int, mido.Message]] = []
    for track_idx, track in enumerate(mid.tracks):
        abs_tick = 0
        for seq_idx, msg in enumerate(track):
            abs_tick += int(msg.time)
            events.append((abs_tick, track_idx, seq_idx, msg))
    events.sort(key=lambda item: (item[0], item[1], item[2]))
    return events


def _track_labels(mid: mido.MidiFile) -> dict[int, str]:
    labels: dict[int, str] = {}
    for track_idx, track in enumerate(mid.tracks):
        parts: list[str] = []
        for msg in track:
            if msg.type not in {"track_name", "instrument_name"}:
                continue
            name = str(getattr(msg, "name", "")).strip()
            if not name or name in parts:
                continue
            parts.append(name)
        if parts:
            labels[track_idx] = " | ".join(parts)
    return labels


def build_tempo_map(mid: mido.MidiFile, events: Iterable[tuple[int, int, int, mido.Message]]) -> list[TempoPoint]:
    points = [TempoPoint(tick=0, seconds=0.0, tempo=DEFAULT_TEMPO)]
    last_tick = 0
    elapsed_s = 0.0
    active_tempo = DEFAULT_TEMPO

    for abs_tick, _, _, msg in events:
        if msg.type != "set_tempo":
            continue

        delta_ticks = abs_tick - last_tick
        if delta_ticks < 0:
            raise ValueError("tempo map tick regression")

        elapsed_s += mido.tick2second(delta_ticks, mid.ticks_per_beat, active_tempo)
        active_tempo = int(msg.tempo)
        last_tick = abs_tick
        new_point = TempoPoint(tick=abs_tick, seconds=elapsed_s, tempo=active_tempo)
        # Replace the initial default point if the first explicit set_tempo is also at tick=0.
        if abs_tick == 0 and len(points) == 1:
            points[0] = new_point
        else:
            points.append(new_point)

    return points


def _tick_to_seconds(abs_tick: int, ticks_per_beat: int, tempo_points: list[TempoPoint]) -> float:
    tempo_ticks = [point.tick for point in tempo_points]
    idx = bisect_right(tempo_ticks, abs_tick) - 1
    if idx < 0:
        idx = 0
    point = tempo_points[idx]
    delta_ticks = abs_tick - point.tick
    return point.seconds + mido.tick2second(delta_ticks, ticks_per_beat, point.tempo)


def _compute_polyphony(notes: list[NoteEvent]) -> int:
    edges: list[tuple[float, int]] = []
    for note in notes:
        edges.append((note.start_s, 1))
        edges.append((note.end_s, -1))

    # End edges apply before start edges at identical timestamps.
    edges.sort(key=lambda item: (item[0], item[1]))

    active = 0
    peak = 0
    for _, delta in edges:
        active += delta
        peak = max(peak, active)
    return peak


def _choose_auto_transpose(notes: list[int], min_freq_hz: float, max_freq_hz: float) -> int:
    best_shift = 0
    best_clamps = None
    for shift in range(-36, 37):
        clamps = 0
        for note in notes:
            freq = midi_note_to_freq(note + shift)
            if freq < min_freq_hz or freq > max_freq_hz:
                clamps += 1
        candidate = (clamps, abs(shift), shift)
        if best_clamps is None or candidate < best_clamps:
            best_clamps = candidate
            best_shift = shift
    return best_shift


def _fold_frequency(freq_hz: float, min_freq_hz: float, max_freq_hz: float) -> tuple[float, bool]:
    if min_freq_hz <= freq_hz <= max_freq_hz:
        return freq_hz, False
    original = freq_hz
    while freq_hz < min_freq_hz and freq_hz > 0:
        freq_hz *= 2.0
    while freq_hz > max_freq_hz:
        freq_hz /= 2.0
    # If folding pushed it below min (very narrow range), clamp as fallback
    if freq_hz < min_freq_hz:
        freq_hz = min_freq_hz
    return freq_hz, freq_hz != original


def analyze_midi(
    midi_path: str | Path,
    min_freq_hz: float,
    max_freq_hz: float,
    transpose_override: int | None,
    auto_transpose: bool,
    strip_leading_silence: bool = True,
) -> tuple[MidiAnalysisReport, TempoMap]:
    mid = mido.MidiFile(str(midi_path))
    events = _collect_events(mid)
    tempo_points = build_tempo_map(mid, events)
    track_labels = _track_labels(mid)

    active_notes: dict[tuple[int, int], deque[tuple[int, float, int, int, int]]] = defaultdict(deque)
    raw_notes: list[tuple[float, float, int, int, int, int]] = []
    source_notes_for_shift: list[int] = []
    max_tick = 0
    program_by_channel = defaultdict(int)

    for abs_tick, track_idx, _, msg in events:
        max_tick = max(max_tick, abs_tick)
        if msg.type == "program_change":
            channel = int(getattr(msg, "channel", 0))
            program_by_channel[channel] = int(getattr(msg, "program", 0))
            continue
        if msg.type not in {"note_on", "note_off"}:
            continue

        note_num = int(msg.note)
        channel = int(getattr(msg, "channel", 0))
        note_key = (channel, note_num)
        abs_seconds = _tick_to_seconds(abs_tick, mid.ticks_per_beat, tempo_points)

        if msg.type == "note_on" and int(msg.velocity) > 0:
            program = int(program_by_channel[channel])
            if is_non_playable_midi_part(channel=channel, program=program):
                continue
            active_notes[note_key].append((abs_tick, abs_seconds, int(msg.velocity), program, track_idx))
            continue

        if not active_notes[note_key]:
            continue

        start_tick, start_s, velocity, _program, start_track_idx = active_notes[note_key].popleft()
        if abs_tick <= start_tick:
            continue

        raw_notes.append((start_s, abs_seconds, note_num, velocity, channel, start_track_idx))
        source_notes_for_shift.append(note_num)

    duration_s = _tick_to_seconds(max_tick, mid.ticks_per_beat, tempo_points)
    for (channel, source_note), starts in active_notes.items():
        for _, start_s, velocity, _program, start_track_idx in starts:
            raw_notes.append((start_s, duration_s, source_note, velocity, channel, start_track_idx))
            source_notes_for_shift.append(source_note)

    if transpose_override is not None:
        transpose = transpose_override
    elif auto_transpose and source_notes_for_shift:
        transpose = _choose_auto_transpose(source_notes_for_shift, min_freq_hz=min_freq_hz, max_freq_hz=max_freq_hz)
    else:
        transpose = 0

    converted: list[NoteEvent] = []
    clamped_count = 0
    for start_s, end_s, source_note, velocity, channel, source_track in raw_notes:
        transposed_note = source_note + transpose
        unclamped_freq_hz = midi_note_to_freq(transposed_note)
        final_freq_hz, clamped = _fold_frequency(
            unclamped_freq_hz,
            min_freq_hz=min_freq_hz,
            max_freq_hz=max_freq_hz,
        )
        clamped_count += int(clamped)
        converted.append(
            NoteEvent(
                start_s=start_s,
                end_s=end_s,
                source_note=source_note,
                transposed_note=transposed_note,
                frequency_hz=final_freq_hz,
                velocity=velocity,
                channel=channel,
                source_track=source_track,
                source_track_name=track_labels.get(source_track),
            )
        )

    converted.sort(key=lambda note: (note.start_s, note.end_s, note.channel, note.source_note))
    if converted:
        duration_s = max(duration_s, max(note.end_s for note in converted))

    min_note = min(source_notes_for_shift) if source_notes_for_shift else None
    max_note = max(source_notes_for_shift) if source_notes_for_shift else None

    if strip_leading_silence and converted:
        earliest = converted[0].start_s  # sorted by start_s
        if earliest > _LEADING_SILENCE_THRESHOLD_S:
            _log.info("Stripping %.1fs of leading silence.", earliest)
            converted = [
                replace(note, start_s=note.start_s - earliest, end_s=note.end_s - earliest)
                for note in converted
            ]
            duration_s = max(0.0, duration_s - earliest)

    return (
        MidiAnalysisReport(
            notes=converted,
            duration_s=duration_s,
            note_count=len(converted),
            max_polyphony=_compute_polyphony(converted),
            transpose_semitones=transpose,
            clamped_note_count=clamped_count,
            min_source_note=min_note,
            max_source_note=max_note,
        ),
        TempoMap(points=tempo_points, ticks_per_beat=mid.ticks_per_beat),
    )

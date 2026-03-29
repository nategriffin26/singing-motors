from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import mido

from .types import CandidateNote


@dataclass(frozen=True)
class MidiWriteSettings:
    ticks_per_beat: int = 480
    tempo_us_per_beat: int = 500_000
    pitch_bend_range_semitones: float = 2.0


@dataclass(frozen=True)
class MidiWriteResult:
    path: Path
    note_count: int


def write_motor_midi(
    notes: list[CandidateNote],
    *,
    output_path: Path,
    settings: MidiWriteSettings,
) -> MidiWriteResult:
    mid = mido.MidiFile(ticks_per_beat=settings.ticks_per_beat)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.append(mido.MetaMessage("set_tempo", tempo=settings.tempo_us_per_beat, time=0))

    events: list[tuple[float, int, mido.Message]] = []
    for note in notes:
        on = mido.Message(
            "note_on",
            channel=0,
            note=note.midi_note,
            velocity=note.velocity,
            time=0,
        )
        off = mido.Message(
            "note_off",
            channel=0,
            note=note.midi_note,
            velocity=0,
            time=0,
        )
        events.append((note.start_s, 1, on))
        events.append((note.end_s, 0, off))

    _append_sorted_events(track, events, settings=settings)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    mid.save(output_path)
    return MidiWriteResult(path=output_path, note_count=len(notes))


def write_expressive_midi(
    notes: list[CandidateNote],
    *,
    output_path: Path,
    settings: MidiWriteSettings,
    max_channels: int = 6,
) -> MidiWriteResult:
    if max_channels < 1 or max_channels > 6:
        raise ValueError("max_channels must be in range [1, 6]")

    mid = mido.MidiFile(ticks_per_beat=settings.ticks_per_beat)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.append(mido.MetaMessage("set_tempo", tempo=settings.tempo_us_per_beat, time=0))

    events: list[tuple[float, int, mido.Message]] = []
    channel_assignments = _assign_channels(notes, max_channels=max_channels)
    for idx, note in enumerate(notes):
        channel = channel_assignments[idx]
        events.append(
            (
                note.start_s,
                1,
                mido.Message("note_on", channel=channel, note=note.midi_note, velocity=note.velocity, time=0),
            )
        )

        for bend in note.bends:
            clamped_time = min(max(bend.time_s, note.start_s), note.end_s)
            bend_value = _semitones_to_pitchwheel(
                bend.semitones,
                bend_range=settings.pitch_bend_range_semitones,
            )
            events.append((clamped_time, 2, mido.Message("pitchwheel", channel=channel, pitch=bend_value, time=0)))

        events.append((note.end_s, 0, mido.Message("note_off", channel=channel, note=note.midi_note, velocity=0, time=0)))
        events.append((note.end_s, 3, mido.Message("pitchwheel", channel=channel, pitch=0, time=0)))

    _append_sorted_events(track, events, settings=settings)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    mid.save(output_path)
    return MidiWriteResult(path=output_path, note_count=len(notes))


def _assign_channels(notes: list[CandidateNote], *, max_channels: int) -> list[int]:
    entries = sorted(enumerate(notes), key=lambda item: (item[1].start_s, item[1].end_s, item[0]))
    assignments = [-1] * len(notes)
    active: list[tuple[float, int, int]] = []  # (end_s, idx, channel)
    free_channels = list(range(max_channels))
    free_channels.sort()

    for idx, note in entries:
        active = [entry for entry in active if entry[0] > note.start_s]
        used = {channel for _, _, channel in active}
        free_channels = [channel for channel in range(max_channels) if channel not in used]
        if not free_channels:
            raise ValueError("channel assignment overflow; run polyphony cap before writing expressive MIDI")
        channel = free_channels[0]
        assignments[idx] = channel
        active.append((note.end_s, idx, channel))

    return assignments


def _semitones_to_pitchwheel(semitones: float, *, bend_range: float) -> int:
    if bend_range <= 0.0:
        return 0
    normalized = max(-1.0, min(1.0, semitones / bend_range))
    return int(round(normalized * 8191.0))


def _append_sorted_events(
    track: mido.MidiTrack,
    events: list[tuple[float, int, mido.Message]],
    *,
    settings: MidiWriteSettings,
) -> None:
    events.sort(key=lambda event: (event[0], event[1]))
    prev_ticks = 0
    for time_s, _, message in events:
        abs_ticks = int(round(mido.second2tick(time_s, settings.ticks_per_beat, settings.tempo_us_per_beat)))
        delta_ticks = max(0, abs_ticks - prev_ticks)
        prev_ticks = abs_ticks
        track.append(message.copy(time=delta_ticks))
    track.append(mido.MetaMessage("end_of_track", time=0))

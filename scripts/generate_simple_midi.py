#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

from mido import Message, MetaMessage, MidiFile, MidiTrack


OUTPUT = Path("assets/midi/simple4.mid")


def generate_simple4(output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    mid = MidiFile(ticks_per_beat=480)
    track = MidiTrack()
    mid.tracks.append(track)

    track.append(MetaMessage("set_tempo", tempo=500000, time=0))

    notes = [60, 64, 67, 72]
    velocity = 90
    beat_ticks = 480
    for note in notes:
        track.append(Message("note_on", note=note, velocity=velocity, time=0))
        track.append(Message("note_off", note=note, velocity=0, time=beat_ticks))

    track.append(MetaMessage("end_of_track", time=0))
    mid.save(output_path)


def main() -> None:
    generate_simple4(OUTPUT)
    print(f"wrote {OUTPUT}")


if __name__ == "__main__":
    main()

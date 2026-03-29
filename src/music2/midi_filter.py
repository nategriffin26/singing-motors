from __future__ import annotations

from typing import Final

MIDI_DRUM_CHANNEL: Final[int] = 9

_NON_PLAYABLE_PROGRAM_RANGES: Final[tuple[range, ...]] = (
    range(8, 16),    # Chromatic Percussion
    range(112, 120),  # Percussive
    range(120, 128),  # Sound effects
)


def is_non_playable_gm_program(program: int | None) -> bool:
    if program is None:
        return False
    normalized = int(program)
    if normalized < 0 or normalized > 127:
        return False
    return any(normalized in program_range for program_range in _NON_PLAYABLE_PROGRAM_RANGES)


def is_non_playable_midi_part(
    *,
    channel: int | None = None,
    program: int | None = None,
    is_drum: bool = False,
) -> bool:
    if is_drum:
        return True
    if channel is not None and int(channel) == MIDI_DRUM_CHANNEL:
        return True
    return is_non_playable_gm_program(program)

from __future__ import annotations

from typing import Literal, cast


ColorModeId = Literal[
    "monochrome_accent",
    "channel",
    "octave_bands",
    "frequency_bands",
    "motor_slot",
    "velocity_intensity",
]

DEFAULT_COLOR_MODE: ColorModeId = "monochrome_accent"
COLOR_MODE_IDS: tuple[ColorModeId, ...] = (
    "monochrome_accent",
    "channel",
    "octave_bands",
    "frequency_bands",
    "motor_slot",
    "velocity_intensity",
)


def coerce_color_mode_id(value: str) -> ColorModeId:
    if value not in COLOR_MODE_IDS:
        allowed = ", ".join(COLOR_MODE_IDS)
        raise ValueError(f"invalid ui color mode: {value!r} (allowed: {allowed})")
    return cast(ColorModeId, value)

from __future__ import annotations

from typing import Literal, cast


ThemeId = Literal["neon", "retro", "minimal", "oceanic", "terminal", "sunset", "chalkboard", "blueprint", "holographic", "botanical"]

DEFAULT_THEME: ThemeId = "neon"
THEME_IDS: tuple[ThemeId, ...] = ("neon", "retro", "minimal", "oceanic", "terminal", "sunset", "chalkboard", "blueprint", "holographic", "botanical")


def coerce_theme_id(value: str) -> ThemeId:
    if value not in THEME_IDS:
        allowed = ", ".join(THEME_IDS)
        raise ValueError(f"invalid ui theme: {value!r} (allowed: {allowed})")
    return cast(ThemeId, value)

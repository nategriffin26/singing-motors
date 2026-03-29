from __future__ import annotations

from typing import Any

from ..instrument_profile import InstrumentProfile
from ..playback_program import PlaybackProgram
from .core import simulate_playback_plan


def simulate_playback_program(
    *,
    playback_program: PlaybackProgram,
    instrument_profile: InstrumentProfile,
) -> dict[str, Any]:
    sections: list[dict[str, Any]] = []
    for section in playback_program.sections:
        simulated = simulate_playback_plan(
            playback_plan=section.playback_plan,
            instrument_profile=instrument_profile,
        )
        sections.append(
            {
                "section_id": section.section_id,
                "display_name": section.display_name,
                "metadata": dict(section.metadata),
                **simulated,
            }
        )
    return {
        "mode_id": playback_program.mode_id,
        "display_name": playback_program.display_name,
        "section_count": len(playback_program.sections),
        "sections": sections,
    }

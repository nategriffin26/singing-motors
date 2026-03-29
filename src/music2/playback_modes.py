from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .models import CompileReport, MidiAnalysisReport
from .playback_program import PlaybackProgram, ProgramSection, playback_plan_from_compile_report


class PlaybackMode(Protocol):
    mode_id: str
    display_name: str

    def build_program(
        self,
        *,
        analysis: MidiAnalysisReport,
        compiled: CompileReport,
    ) -> PlaybackProgram: ...


@dataclass(frozen=True)
class FullSongPlaybackMode:
    mode_id: str = "full-song"
    display_name: str = "Full song"

    def build_program(
        self,
        *,
        analysis: MidiAnalysisReport,
        compiled: CompileReport,
    ) -> PlaybackProgram:
        playback_plan = compiled.playback_plan or playback_plan_from_compile_report(compiled)
        return PlaybackProgram(
            mode_id=self.mode_id,
            display_name=self.display_name,
            sections=(
                ProgramSection(
                    section_id="section-1",
                    display_name="Full song",
                    playback_plan=playback_plan,
                    metadata={
                        "note_count": analysis.note_count,
                        "transpose_semitones": analysis.transpose_semitones,
                    },
                ),
            ),
        )


DEFAULT_PLAYBACK_MODE = FullSongPlaybackMode()


def build_default_playback_program(
    *,
    analysis: MidiAnalysisReport,
    compiled: CompileReport,
) -> PlaybackProgram:
    return DEFAULT_PLAYBACK_MODE.build_program(analysis=analysis, compiled=compiled)

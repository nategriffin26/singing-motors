from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .models import OverflowMode, PlaybackEventGroup, Segment

if TYPE_CHECKING:
    from .models import CompileReport


@dataclass(frozen=True)
class PlaybackPlan:
    plan_id: str
    display_name: str
    event_groups: tuple[PlaybackEventGroup, ...]
    shadow_segments: tuple[Segment, ...]
    connected_motors: int
    overflow_mode: OverflowMode
    motor_change_count: int

    @property
    def event_group_count(self) -> int:
        return len(self.event_groups)

    @property
    def shadow_segment_count(self) -> int:
        return len(self.shadow_segments)

    @property
    def duration_total_us(self) -> int:
        return sum(max(0, group.delta_us) for group in self.event_groups)


@dataclass(frozen=True)
class ProgramSection:
    section_id: str
    display_name: str
    playback_plan: PlaybackPlan
    start_offset_us: int = 0
    metadata: dict[str, int | float | str] = field(default_factory=dict)

    @property
    def duration_us(self) -> int:
        return self.playback_plan.duration_total_us


@dataclass(frozen=True)
class PlaybackProgram:
    mode_id: str
    display_name: str
    sections: tuple[ProgramSection, ...]

    def __post_init__(self) -> None:
        if not self.sections:
            raise ValueError("sections cannot be empty")

    @property
    def total_duration_us(self) -> int:
        return sum(section.duration_us for section in self.sections)

    @property
    def playback_plan(self) -> PlaybackPlan:
        if len(self.sections) != 1:
            raise ValueError("playback_plan is only available for single-section programs")
        return self.sections[0].playback_plan


def playback_plan_from_compile_report(
    compiled: CompileReport,
    *,
    plan_id: str = "full-song",
    display_name: str = "Full song",
) -> PlaybackPlan:
    return PlaybackPlan(
        plan_id=plan_id,
        display_name=display_name,
        event_groups=tuple(compiled.event_groups),
        shadow_segments=tuple(compiled.segments),
        connected_motors=compiled.connected_motors,
        overflow_mode=compiled.overflow_mode,
        motor_change_count=compiled.motor_change_count,
    )

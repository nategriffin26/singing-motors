from __future__ import annotations

from typing import Literal, TypedDict
from ..viewer_color_mode import ColorModeId
from ..viewer_theme import ThemeId


RenderMode = Literal["live", "prerender_30fps"]


class SongInfo(TypedDict):
    file_name: str
    duration_us: int
    duration_s: float
    note_count: int
    max_polyphony: int
    transpose_semitones: int


class NoteRange(TypedDict):
    min_note: int
    max_note: int


class ViewerWindow(TypedDict):
    history_us: int
    lookahead_us: int


class ViewerAllocationStats(TypedDict):
    policy: str
    stolen_notes: int
    dropped_notes: int
    playable_notes: int
    retained_ratio: float


class ViewerProgramSection(TypedDict):
    section_id: str
    display_name: str
    start_offset_us: int
    duration_us: int
    event_group_count: int
    shadow_segment_count: int


class ViewerProgram(TypedDict):
    mode_id: str
    display_name: str
    section_count: int
    total_duration_us: int
    sections: list[ViewerProgramSection]


class ViewerSession(TypedDict):
    song: SongInfo
    program: ViewerProgram
    note_range: NoteRange
    connected_motors: int
    lanes: int
    allocation: ViewerAllocationStats
    window: ViewerWindow
    render_mode: RenderMode
    fps: int
    timeline_version: str
    timeline_ready: bool
    timeline_url: str
    theme_default: ThemeId
    themes_available: list[ThemeId]
    color_mode_default: ColorModeId
    color_modes_available: list[ColorModeId]
    show_controls: bool
    sync_offset_ms: float
    sync_strategy: Literal["scheduled_start_v1", "legacy_poll_v1"]
    scheduled_start_unix_ms: int | None
    scheduled_start_device_us: int | None
    drift_rebase_threshold_ms: float
    generated_at_unix_ms: int


class PlaybackState(TypedDict):
    playing: bool
    stream_open: bool
    stream_end_received: bool


class ViewerNoteBar(TypedDict):
    id: int
    pitch: int
    start_us: int
    end_us: int
    velocity: int
    frequency_hz: float
    channel: int
    motor_slot: int
    active: bool


class ViewerFrame(TypedDict):
    type: Literal["frame"]
    seq: int
    playhead_us: int
    window_start_us: int
    window_end_us: int
    duration_us: int
    bars: list[ViewerNoteBar]
    active_note_ids: list[int]
    beat_markers_us: list[int]
    state: PlaybackState


class ViewerHello(TypedDict):
    type: Literal["hello"]
    protocol: str
    server_time_unix_ms: int
    session: ViewerSession | None
    frame: ViewerFrame | None


class ViewerTimelineBar(TypedDict):
    id: int
    pitch: int
    start_us: int
    end_us: int
    velocity: int
    frequency_hz: float
    channel: int
    motor_slot: int


class ViewerTimelineFrame(TypedDict):
    playhead_us: int
    window_start_us: int
    window_end_us: int
    active_note_ids: list[int]
    visible_bar_ids: list[int]
    beat_markers_us: list[int]


class ViewerTimelineStyleHints(TypedDict):
    quality: Literal["performance", "cinematic"]
    allow_glow: bool
    dpr_cap: float


class ViewerTimeline(TypedDict):
    version: Literal["v1"]
    fps: int
    duration_us: int
    frame_count: int
    note_range: NoteRange
    bars_static: list[ViewerTimelineBar]
    frames: list[ViewerTimelineFrame]
    style_hints: ViewerTimelineStyleHints
    generated_at_unix_ms: int

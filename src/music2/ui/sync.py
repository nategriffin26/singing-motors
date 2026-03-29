from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import time

from ..models import CompileReport, MidiAnalysisReport, PlaybackMetrics, StreamStatus
from ..playback_modes import build_default_playback_program
from ..playback_program import PlaybackProgram
from ..viewer_color_mode import COLOR_MODE_IDS, ColorModeId, DEFAULT_COLOR_MODE
from ..viewer_theme import DEFAULT_THEME, THEME_IDS, ThemeId
from .types import (
    NoteRange,
    PlaybackState,
    RenderMode,
    ViewerProgram,
    ViewerProgramSection,
    SongInfo,
    ViewerFrame,
    ViewerNoteBar,
    ViewerSession,
    ViewerTimeline,
    ViewerTimelineBar,
    ViewerTimelineFrame,
    ViewerTimelineStyleHints,
    ViewerWindow,
)

_DEFAULT_HISTORY_US = 350_000
_DEFAULT_LOOKAHEAD_US = 3_000_000
_GRID_STEP_US = 250_000
_DEFAULT_TIMELINE_FPS = 30


@dataclass(frozen=True)
class _IndexedNote:
    id: int
    start_us: int
    end_us: int
    pitch: int
    velocity: int
    frequency_hz: float
    channel: int
    motor_slot: int


def _build_note_intervals(notes: list[_IndexedNote]) -> tuple[list[int], list[tuple[int, ...]]]:
    if not notes:
        return [], []

    boundaries = sorted({note.start_us for note in notes} | {note.end_us for note in notes})
    events: list[tuple[int, int, int]] = []
    for note in notes:
        events.append((note.start_us, 1, note.id))
        events.append((note.end_us, 0, note.id))
    events.sort(key=lambda event: (event[0], event[1], event[2]))

    active: set[int] = set()
    event_idx = 0
    intervals: list[tuple[int, ...]] = []

    for boundary_idx, boundary in enumerate(boundaries):
        while event_idx < len(events) and events[event_idx][0] == boundary:
            _, event_type, note_id = events[event_idx]
            if event_type == 0:
                active.discard(note_id)
            else:
                active.add(note_id)
            event_idx += 1

        if boundary_idx + 1 < len(boundaries):
            intervals.append(tuple(sorted(active)))

    return boundaries, intervals


class PlaybackSyncEngine:
    def __init__(
        self,
        *,
        analysis: MidiAnalysisReport,
        compiled: CompileReport,
        midi_path: Path,
        queue_capacity: int,
        scheduler_tick_us: int,
        playback_program: PlaybackProgram | None = None,
        history_us: int = _DEFAULT_HISTORY_US,
        lookahead_us: int = _DEFAULT_LOOKAHEAD_US,
    ) -> None:
        self._analysis = analysis
        self._compiled = compiled
        self._midi_path = midi_path
        self._queue_capacity = queue_capacity
        self._scheduler_tick_us = scheduler_tick_us
        self._history_us = max(50_000, history_us)
        self._lookahead_us = max(250_000, lookahead_us)
        self._playback_program = playback_program or build_default_playback_program(
            analysis=analysis,
            compiled=compiled,
        )

        self._duration_us = max(0, int(round(analysis.duration_s * 1_000_000.0)))
        self._note_range: NoteRange = {"min_note": 60, "max_note": 72}

        indexed_notes: list[_IndexedNote] = []
        effective_ends = compiled.effective_end_s if len(compiled.effective_end_s) == len(analysis.notes) else []
        min_note: int | None = None
        max_note: int | None = None
        for idx, note in enumerate(analysis.notes):
            motor_slot = compiled.assignments[idx] if idx < len(compiled.assignments) else -1
            if motor_slot < 0:
                continue

            end_s = effective_ends[idx] if idx < len(effective_ends) else note.end_s
            end_s = max(note.start_s, end_s)
            start_us = max(0, int(round(note.start_s * 1_000_000.0)))
            end_us = max(0, int(round(end_s * 1_000_000.0)))
            if end_us <= start_us:
                continue

            pitch = int(note.transposed_note)
            min_note = pitch if min_note is None else min(min_note, pitch)
            max_note = pitch if max_note is None else max(max_note, pitch)
            indexed_notes.append(
                _IndexedNote(
                    id=idx,
                    start_us=start_us,
                    end_us=end_us,
                    pitch=pitch,
                    velocity=int(note.velocity),
                    frequency_hz=float(note.frequency_hz),
                    channel=int(note.channel) + 1,
                    motor_slot=motor_slot,
                )
            )

        if min_note is not None and max_note is not None:
            self._note_range = {"min_note": min_note, "max_note": max_note}

        self._notes_by_id = {note.id: note for note in indexed_notes}
        self._notes_by_start = sorted(indexed_notes, key=lambda note: (note.start_us, note.id))
        self._note_start_us = [note.start_us for note in self._notes_by_start]

        self._max_note_duration_us = 0
        for note in indexed_notes:
            self._max_note_duration_us = max(self._max_note_duration_us, note.end_us - note.start_us)

        self._note_boundaries_us, self._active_note_ids_by_interval = _build_note_intervals(indexed_notes)

        # Identify duplicate notes: same pitch overlapping in time.  Keep the
        # first (by start_us then id) and mark the rest so the UI only shows
        # one bar per pitch per time region.
        self._duplicate_note_ids: set[int] = self._find_pitch_duplicates(indexed_notes)

        # Duration should include any compiler-shaped extension and any note tail.
        segment_total_us = sum(segment.duration_us for segment in compiled.segments)
        max_note_end_us = max((note.end_us for note in indexed_notes), default=0)
        self._duration_us = max(
            self._duration_us,
            segment_total_us,
            max_note_end_us,
            self._playback_program.total_duration_us,
        )
        self._timeline_cache: dict[int, ViewerTimeline] = {}

    def _viewer_program(self) -> ViewerProgram:
        return {
            "mode_id": self._playback_program.mode_id,
            "display_name": self._playback_program.display_name,
            "section_count": len(self._playback_program.sections),
            "total_duration_us": self._playback_program.total_duration_us,
            "sections": [
                ViewerProgramSection(
                    section_id=section.section_id,
                    display_name=section.display_name,
                    start_offset_us=section.start_offset_us,
                    duration_us=section.duration_us,
                    event_group_count=section.playback_plan.event_group_count,
                    shadow_segment_count=section.playback_plan.shadow_segment_count,
                )
                for section in self._playback_program.sections
            ],
        }

    @staticmethod
    def _find_pitch_duplicates(notes: list[_IndexedNote]) -> set[int]:
        """Return IDs of notes that overlap at the same pitch as an earlier note."""
        by_pitch: dict[int, list[_IndexedNote]] = {}
        for note in notes:
            by_pitch.setdefault(note.pitch, []).append(note)

        duplicates: set[int] = set()
        for pitch_notes in by_pitch.values():
            pitch_notes.sort(key=lambda n: (n.start_us, n.id))
            # Track the end of the "primary" note window; any note that starts
            # before this end is a duplicate.
            primary_end = -1
            for note in pitch_notes:
                if note.start_us < primary_end:
                    duplicates.add(note.id)
                else:
                    primary_end = note.end_us
        return duplicates

    @property
    def duration_us(self) -> int:
        return self._duration_us

    @property
    def connected_motors(self) -> int:
        if self._compiled.connected_motors > 0:
            return self._compiled.connected_motors
        if self._compiled.segments:
            return len(self._compiled.segments[0].motor_freq_hz)
        return max(self._analysis.max_polyphony, 0)

    def viewer_session(
        self,
        *,
        render_mode: RenderMode = "live",
        fps: int = _DEFAULT_TIMELINE_FPS,
        timeline_ready: bool = False,
        theme_default: ThemeId = DEFAULT_THEME,
        themes_available: list[ThemeId] | None = None,
        color_mode_default: ColorModeId = DEFAULT_COLOR_MODE,
        color_modes_available: list[ColorModeId] | None = None,
        show_controls: bool = True,
        sync_offset_ms: float = 0.0,
        sync_strategy: str = "legacy_poll_v1",
        scheduled_start_unix_ms: int | None = None,
        scheduled_start_device_us: int | None = None,
        drift_rebase_threshold_ms: float = 40.0,
    ) -> ViewerSession:
        song: SongInfo = {
            "file_name": self._midi_path.name,
            "duration_us": self._duration_us,
            "duration_s": self._duration_us / 1_000_000.0,
            "note_count": self._analysis.note_count,
            "max_polyphony": self._analysis.max_polyphony,
            "transpose_semitones": self._analysis.transpose_semitones,
        }
        window: ViewerWindow = {
            "history_us": self._history_us,
            "lookahead_us": self._lookahead_us,
        }
        lane_span = self._note_range["max_note"] - self._note_range["min_note"] + 1
        playable_notes = len(self._notes_by_start)
        retained_ratio = (playable_notes / self._analysis.note_count) if self._analysis.note_count > 0 else 1.0
        return {
            "song": song,
            "program": self._viewer_program(),
            "note_range": self._note_range,
            "connected_motors": self.connected_motors,
            "lanes": max(1, lane_span),
            "allocation": {
                "policy": self._compiled.overflow_mode,
                "stolen_notes": self._compiled.stolen_note_count,
                "dropped_notes": self._compiled.dropped_note_count,
                "playable_notes": playable_notes,
                "retained_ratio": retained_ratio,
            },
            "window": window,
            "render_mode": render_mode,
            "fps": max(1, int(fps)),
            "timeline_version": "v1",
            "timeline_ready": timeline_ready,
            "timeline_url": "/api/viewer/timeline",
            "theme_default": theme_default,
            "themes_available": list(themes_available or THEME_IDS),
            "color_mode_default": color_mode_default,
            "color_modes_available": list(color_modes_available or COLOR_MODE_IDS),
            "show_controls": bool(show_controls),
            "sync_offset_ms": float(sync_offset_ms),
            "sync_strategy": "scheduled_start_v1" if sync_strategy == "scheduled_start_v1" else "legacy_poll_v1",
            "scheduled_start_unix_ms": scheduled_start_unix_ms,
            "scheduled_start_device_us": scheduled_start_device_us,
            "drift_rebase_threshold_ms": float(drift_rebase_threshold_ms),
            "generated_at_unix_ms": int(time.time() * 1000),
        }

    # Backward-compatible alias for existing CLI wiring.
    def session_metadata(self) -> ViewerSession:
        return self.viewer_session()

    def _active_note_ids(self, playhead_us: int) -> tuple[int, ...]:
        if not self._note_boundaries_us:
            return ()
        if playhead_us < self._note_boundaries_us[0] or playhead_us >= self._note_boundaries_us[-1]:
            return ()
        interval_idx = bisect_right(self._note_boundaries_us, playhead_us) - 1
        if interval_idx < 0 or interval_idx >= len(self._active_note_ids_by_interval):
            return ()
        return self._active_note_ids_by_interval[interval_idx]

    def _window_bounds(self, playhead_us: int) -> tuple[int, int]:
        start = max(0, playhead_us - self._history_us)
        end = min(self._duration_us, playhead_us + self._lookahead_us)
        if end <= start:
            end = min(self._duration_us, start + self._lookahead_us)
        return start, end

    def _beat_markers(self, window_start_us: int, window_end_us: int) -> list[int]:
        if window_end_us <= window_start_us:
            return []
        marker = (window_start_us // _GRID_STEP_US) * _GRID_STEP_US
        if marker < window_start_us:
            marker += _GRID_STEP_US

        result: list[int] = []
        while marker <= window_end_us:
            result.append(int(marker))
            marker += _GRID_STEP_US
        return result

    def _bars_in_window(self, *, window_start_us: int, window_end_us: int, active_ids: set[int]) -> list[ViewerNoteBar]:
        if not self._notes_by_start:
            return []

        scan_start_us = max(0, window_start_us - self._max_note_duration_us)
        left = bisect_left(self._note_start_us, scan_start_us)
        right = bisect_right(self._note_start_us, window_end_us)

        bars: list[ViewerNoteBar] = []
        for note in self._notes_by_start[left:right]:
            if note.id in self._duplicate_note_ids:
                continue
            if note.end_us < window_start_us or note.start_us > window_end_us:
                continue
            bars.append(
                {
                    "id": note.id,
                    "pitch": note.pitch,
                    "start_us": note.start_us,
                    "end_us": note.end_us,
                    "velocity": note.velocity,
                    "frequency_hz": note.frequency_hz,
                    "channel": note.channel,
                    "motor_slot": note.motor_slot,
                    "active": note.id in active_ids,
                }
            )

        bars.sort(key=lambda bar: (bar["start_us"], bar["pitch"], bar["id"]))
        return bars

    def _visible_bar_ids_in_window(self, *, window_start_us: int, window_end_us: int) -> list[int]:
        if not self._notes_by_start:
            return []

        scan_start_us = max(0, window_start_us - self._max_note_duration_us)
        left = bisect_left(self._note_start_us, scan_start_us)
        right = bisect_right(self._note_start_us, window_end_us)

        visible: list[int] = []
        for note in self._notes_by_start[left:right]:
            if note.id in self._duplicate_note_ids:
                continue
            if note.end_us < window_start_us or note.start_us > window_end_us:
                continue
            visible.append(note.id)
        return visible

    def _bars_static(self) -> list[ViewerTimelineBar]:
        bars: list[ViewerTimelineBar] = []
        for note in self._notes_by_start:
            if note.id in self._duplicate_note_ids:
                continue
            bars.append(
                {
                    "id": note.id,
                    "pitch": note.pitch,
                    "start_us": note.start_us,
                    "end_us": note.end_us,
                    "velocity": note.velocity,
                    "frequency_hz": note.frequency_hz,
                    "channel": note.channel,
                    "motor_slot": note.motor_slot,
                }
            )
        return bars

    def viewer_timeline(
        self,
        *,
        fps: int = _DEFAULT_TIMELINE_FPS,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> ViewerTimeline:
        normalized_fps = max(1, int(fps))
        cached = self._timeline_cache.get(normalized_fps)
        if cached is not None:
            return cached

        frame_step_us = max(1, int(round(1_000_000.0 / float(normalized_fps))))
        frame_count = max(1, (self._duration_us // frame_step_us) + 1)

        # -- Sweep-line pre-computation for visible_bar_ids -----------------
        # Instead of binary-searching per frame, maintain a set of currently
        # visible notes and advance enter/exit cursors as the window slides.
        dup_ids = self._duplicate_note_ids
        non_dup_by_start = [n for n in self._notes_by_start if n.id not in dup_ids]
        non_dup_by_end = sorted(non_dup_by_start, key=lambda n: n.end_us)
        enter_cursor = 0  # next note to potentially add (by start_us)
        exit_cursor = 0   # next note to potentially remove (by end_us)
        visible: set[int] = set()

        _progress_step = max(1, frame_count // 100)
        _active_ids_fn = self._active_note_ids
        _window_fn = self._window_bounds
        _duration_us = self._duration_us

        frames: list[ViewerTimelineFrame] = []
        beat_cursor_us = 0  # running cursor for beat markers

        for frame_idx in range(frame_count):
            if progress_callback is not None and (
                frame_idx % _progress_step == 0 or frame_idx == frame_count - 1
            ):
                progress_callback(frame_idx + 1, frame_count)

            playhead_us = min(_duration_us, frame_idx * frame_step_us)
            window_start_us, window_end_us = _window_fn(playhead_us)

            # Active note ids — tuples from _build_note_intervals are
            # already sorted, so just convert to list.
            active_note_ids = list(_active_ids_fn(playhead_us))

            # Sweep-line: add notes whose start_us <= window_end_us
            while enter_cursor < len(non_dup_by_start) and non_dup_by_start[enter_cursor].start_us <= window_end_us:
                visible.add(non_dup_by_start[enter_cursor].id)
                enter_cursor += 1
            # Sweep-line: remove notes whose end_us < window_start_us
            while exit_cursor < len(non_dup_by_end) and non_dup_by_end[exit_cursor].end_us < window_start_us:
                visible.discard(non_dup_by_end[exit_cursor].id)
                exit_cursor += 1

            visible_bar_ids = sorted(visible)

            # Beat markers — inline to avoid repeated function-call overhead
            beat_markers: list[int] = []
            if window_end_us > window_start_us:
                marker = (window_start_us // _GRID_STEP_US) * _GRID_STEP_US
                if marker < window_start_us:
                    marker += _GRID_STEP_US
                while marker <= window_end_us:
                    beat_markers.append(int(marker))
                    marker += _GRID_STEP_US

            frames.append(
                {
                    "playhead_us": int(playhead_us),
                    "window_start_us": int(window_start_us),
                    "window_end_us": int(window_end_us),
                    "active_note_ids": active_note_ids,
                    "visible_bar_ids": visible_bar_ids,
                    "beat_markers_us": beat_markers,
                }
            )

        # Ensure the timeline includes an exact terminal frame at duration_us.
        if frames[-1]["playhead_us"] < _duration_us:
            window_start_us, window_end_us = _window_fn(_duration_us)

            # Advance sweep cursors for the terminal frame
            while enter_cursor < len(non_dup_by_start) and non_dup_by_start[enter_cursor].start_us <= window_end_us:
                visible.add(non_dup_by_start[enter_cursor].id)
                enter_cursor += 1
            while exit_cursor < len(non_dup_by_end) and non_dup_by_end[exit_cursor].end_us < window_start_us:
                visible.discard(non_dup_by_end[exit_cursor].id)
                exit_cursor += 1

            beat_markers = []
            if window_end_us > window_start_us:
                marker = (window_start_us // _GRID_STEP_US) * _GRID_STEP_US
                if marker < window_start_us:
                    marker += _GRID_STEP_US
                while marker <= window_end_us:
                    beat_markers.append(int(marker))
                    marker += _GRID_STEP_US

            frames.append(
                {
                    "playhead_us": int(_duration_us),
                    "window_start_us": int(window_start_us),
                    "window_end_us": int(window_end_us),
                    "active_note_ids": list(_active_ids_fn(_duration_us)),
                    "visible_bar_ids": sorted(visible),
                    "beat_markers_us": beat_markers,
                }
            )
        frame_count = len(frames)

        style_hints: ViewerTimelineStyleHints = {
            "quality": "performance",
            "allow_glow": False,
            "dpr_cap": 1.25,
        }
        timeline: ViewerTimeline = {
            "version": "v1",
            "fps": normalized_fps,
            "duration_us": self._duration_us,
            "frame_count": frame_count,
            "note_range": self._note_range,
            "bars_static": self._bars_static(),
            "frames": frames,
            "style_hints": style_hints,
            "generated_at_unix_ms": int(time.time() * 1000),
        }
        self._timeline_cache[normalized_fps] = timeline
        return timeline

    def frame(
        self,
        *,
        playhead_us: int,
        sent_segments: int,
        total_segments: int,
        status: StreamStatus | None,
        metrics: PlaybackMetrics | None,
    ) -> ViewerFrame:
        _ = sent_segments
        _ = total_segments
        _ = metrics

        playhead = max(0, min(playhead_us, self._duration_us))
        window_start_us, window_end_us = self._window_bounds(playhead)
        active_note_ids = set(self._active_note_ids(playhead))

        state: PlaybackState = {
            "playing": status.playing if status is not None else False,
            "stream_open": status.stream_open if status is not None else False,
            "stream_end_received": status.stream_end_received if status is not None else False,
        }

        return {
            "type": "frame",
            "seq": 0,
            "playhead_us": playhead,
            "window_start_us": window_start_us,
            "window_end_us": window_end_us,
            "duration_us": self._duration_us,
            "bars": self._bars_in_window(
                window_start_us=window_start_us,
                window_end_us=window_end_us,
                active_ids=active_note_ids,
            ),
            "active_note_ids": sorted(active_note_ids),
            "beat_markers_us": self._beat_markers(window_start_us, window_end_us),
            "state": state,
        }

    # Backward-compatible alias for existing CLI wiring.
    def snapshot(
        self,
        *,
        playhead_us: int,
        sent_segments: int,
        total_segments: int,
        status: StreamStatus | None,
        metrics: PlaybackMetrics | None,
    ) -> ViewerFrame:
        return self.frame(
            playhead_us=playhead_us,
            sent_segments=sent_segments,
            total_segments=total_segments,
            status=status,
            metrics=metrics,
        )


def build_session_metadata(
    analysis: MidiAnalysisReport,
    compiled: CompileReport,
    midi_path: Path,
    queue_capacity: int,
    scheduler_tick_us: int,
    playback_program: PlaybackProgram | None = None,
) -> ViewerSession:
    engine = PlaybackSyncEngine(
        analysis=analysis,
        compiled=compiled,
        midi_path=midi_path,
        queue_capacity=queue_capacity,
        scheduler_tick_us=scheduler_tick_us,
        playback_program=playback_program,
    )
    return engine.viewer_session()


def snapshot_at(
    engine: PlaybackSyncEngine,
    *,
    playhead_us: int,
    sent_segments: int,
    total_segments: int,
    status: StreamStatus | None,
    metrics: PlaybackMetrics | None,
) -> ViewerFrame:
    return engine.frame(
        playhead_us=playhead_us,
        sent_segments=sent_segments,
        total_segments=total_segments,
        status=status,
        metrics=metrics,
    )


def build_timeline(engine: PlaybackSyncEngine, *, fps: int = _DEFAULT_TIMELINE_FPS) -> ViewerTimeline:
    return engine.viewer_timeline(fps=fps)

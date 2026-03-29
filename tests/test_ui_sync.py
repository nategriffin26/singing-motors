from __future__ import annotations

from pathlib import Path

from music2.models import CompileReport, MidiAnalysisReport, NoteEvent, OverflowMode, PlaybackMetrics, Segment, StreamStatus
from music2.ui.sync import PlaybackSyncEngine
from music2.viewer_color_mode import COLOR_MODE_IDS
from music2.viewer_theme import THEME_IDS


def _analysis(notes: list[NoteEvent], duration_s: float) -> MidiAnalysisReport:
    return MidiAnalysisReport(
        notes=notes,
        duration_s=duration_s,
        note_count=len(notes),
        max_polyphony=1,
        transpose_semitones=0,
        clamped_note_count=0,
        min_source_note=min((note.source_note for note in notes), default=None),
        max_source_note=max((note.source_note for note in notes), default=None),
    )


def _status(
    *,
    playhead_us: int,
    playing: bool = True,
    stream_open: bool = True,
    stream_end_received: bool = False,
    queue_depth: int = 3,
    credits: int = 5,
    active_motors: int = 1,
) -> StreamStatus:
    return StreamStatus(
        playing=playing,
        stream_open=stream_open,
        stream_end_received=stream_end_received,
        motor_count=8,
        queue_depth=queue_depth,
        queue_capacity=16,
        credits=credits,
        active_motors=active_motors,
        playhead_us=playhead_us,
    )


def _metrics() -> PlaybackMetrics:
    return PlaybackMetrics(
        underrun_count=2,
        queue_high_water=12,
        scheduling_late_max_us=450,
        crc_parse_errors=1,
        queue_depth=3,
        credits=5,
    )


def _engine(
    notes: list[NoteEvent],
    *,
    duration_s: float,
    assignments: list[int],
    effective_end_s: list[float] | None = None,
    stolen_note_count: int = 0,
    dropped_note_count: int = 0,
    overflow_mode: OverflowMode = "steal_quietest",
) -> PlaybackSyncEngine:
    if effective_end_s is None:
        effective_end_s = [note.end_s for note in notes]
    compiled = CompileReport(
        segments=[
            Segment(duration_us=500_000, motor_freq_hz=(261.6, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)),
            Segment(duration_us=500_000, motor_freq_hz=(293.7, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)),
        ],
        assignments=assignments,
        duplicated_slots=0,
        connected_motors=8,
        overflow_mode=overflow_mode,
        effective_end_s=effective_end_s,
        stolen_note_count=stolen_note_count,
        dropped_note_count=dropped_note_count,
    )
    return PlaybackSyncEngine(
        analysis=_analysis(notes, duration_s=duration_s),
        compiled=compiled,
        midi_path=Path("song.mid"),
        queue_capacity=16,
        scheduler_tick_us=25,
    )


def test_viewer_session_contains_song_and_window_metadata() -> None:
    notes = [NoteEvent(0.0, 0.5, 60, 60, 261.6, 96, 0)]
    engine = _engine(notes, duration_s=1.0, assignments=[0])

    session = engine.viewer_session()

    assert session["song"]["file_name"] == "song.mid"
    assert session["song"]["duration_us"] == 1_000_000
    assert session["note_range"]["min_note"] == 60
    assert session["note_range"]["max_note"] == 60
    assert session["allocation"]["policy"] == "steal_quietest"
    assert session["allocation"]["playable_notes"] == 1
    assert session["window"]["history_us"] > 0
    assert session["window"]["lookahead_us"] > 0
    assert session["theme_default"] == "neon"
    assert session["themes_available"] == list(THEME_IDS)
    assert session["color_mode_default"] == "monochrome_accent"
    assert session["color_modes_available"] == list(COLOR_MODE_IDS)
    assert session["show_controls"] is True
    assert session["sync_offset_ms"] == 0.0


def test_viewer_session_can_include_signed_sync_offset() -> None:
    notes = [NoteEvent(0.0, 0.5, 60, 60, 261.6, 96, 0)]
    engine = _engine(notes, duration_s=1.0, assignments=[0])

    session = engine.viewer_session(sync_offset_ms=-125.0)

    assert session["sync_offset_ms"] == -125.0


def test_viewer_session_can_hide_controls() -> None:
    notes = [NoteEvent(0.0, 0.5, 60, 60, 261.6, 96, 0)]
    engine = _engine(notes, duration_s=1.0, assignments=[0])

    session = engine.viewer_session(show_controls=False)
    assert session["show_controls"] is False


def test_frame_note_activation_boundaries_start_inclusive_end_exclusive() -> None:
    notes = [
        NoteEvent(0.0, 1.0, 60, 60, 261.6, 96, 0),
        NoteEvent(1.0, 2.0, 62, 62, 293.7, 88, 1),
    ]
    engine = _engine(notes, duration_s=2.0, assignments=[0, 1])

    just_before = engine.frame(
        playhead_us=999_999,
        sent_segments=1,
        total_segments=2,
        status=_status(playhead_us=999_999),
        metrics=_metrics(),
    )
    at_boundary = engine.frame(
        playhead_us=1_000_000,
        sent_segments=1,
        total_segments=2,
        status=_status(playhead_us=1_000_000),
        metrics=_metrics(),
    )

    assert just_before["active_note_ids"] == [0]
    assert at_boundary["active_note_ids"] == [1]

    before_flags = {bar["id"]: bar["active"] for bar in just_before["bars"]}
    at_flags = {bar["id"]: bar["active"] for bar in at_boundary["bars"]}
    assert before_flags[0] is True and before_flags[1] is False
    assert at_flags[0] is False and at_flags[1] is True
    freq_by_id = {bar["id"]: bar["frequency_hz"] for bar in at_boundary["bars"]}
    assert freq_by_id[0] == 261.6
    assert freq_by_id[1] == 293.7


def test_frame_window_bounds_and_beat_markers_clamp_to_duration() -> None:
    notes = [NoteEvent(0.2, 0.4, 67, 67, 392.0, 80, 0)]
    engine = _engine(notes, duration_s=0.6, assignments=[0])

    frame = engine.frame(
        playhead_us=50_000,
        sent_segments=0,
        total_segments=2,
        status=_status(playhead_us=50_000),
        metrics=None,
    )

    assert frame["window_start_us"] == 0
    assert frame["window_end_us"] == 1_000_000
    assert frame["duration_us"] == 1_000_000
    assert frame["beat_markers_us"][:3] == [0, 250_000, 500_000]


def test_frame_state_fields_reflect_status() -> None:
    notes = [NoteEvent(0.0, 0.5, 64, 64, 329.6, 90, 0)]
    engine = _engine(notes, duration_s=1.0, assignments=[0])

    frame = engine.frame(
        playhead_us=400_000,
        sent_segments=1,
        total_segments=2,
        status=_status(
            playhead_us=400_000,
            playing=False,
            stream_open=False,
            stream_end_received=True,
            active_motors=0,
        ),
        metrics=_metrics(),
    )

    assert frame["state"]["playing"] is False
    assert frame["state"]["stream_open"] is False
    assert frame["state"]["stream_end_received"] is True


def test_frame_rewind_to_zero_clears_active_notes_when_note_starts_later() -> None:
    notes = [NoteEvent(0.2, 0.4, 67, 67, 392.0, 80, 0)]
    engine = _engine(notes, duration_s=0.6, assignments=[0])

    active = engine.frame(
        playhead_us=250_000,
        sent_segments=1,
        total_segments=2,
        status=_status(playhead_us=250_000),
        metrics=None,
    )
    rewound = engine.frame(
        playhead_us=0,
        sent_segments=0,
        total_segments=2,
        status=_status(playhead_us=0, active_motors=0),
        metrics=None,
    )

    assert active["active_note_ids"] == [0]
    assert rewound["active_note_ids"] == []


def test_timeline_frame_count_and_monotonic_playhead() -> None:
    notes = [NoteEvent(0.0, 0.8, 60, 60, 261.6, 96, 0)]
    engine = _engine(notes, duration_s=1.0, assignments=[0])

    timeline = engine.viewer_timeline(fps=30)

    assert timeline["fps"] == 30
    assert timeline["frame_count"] >= 31
    assert len(timeline["frames"]) == timeline["frame_count"]
    playheads = [frame["playhead_us"] for frame in timeline["frames"]]
    assert playheads[0] == 0
    assert playheads[-1] == timeline["duration_us"]
    assert all(playheads[idx] <= playheads[idx + 1] for idx in range(len(playheads) - 1))
    assert timeline["bars_static"][0]["frequency_hz"] == 261.6


def test_timeline_active_note_boundaries_use_precomputed_frames() -> None:
    notes = [
        NoteEvent(0.0, 1.0, 60, 60, 261.6, 96, 0),
        NoteEvent(1.0, 2.0, 62, 62, 293.7, 88, 1),
    ]
    engine = _engine(notes, duration_s=2.0, assignments=[0, 1])

    timeline = engine.viewer_timeline(fps=30)
    frame_before = timeline["frames"][30]  # 999_990us at 30fps
    frame_after = timeline["frames"][31]   # 1_033_323us at 30fps

    assert frame_before["active_note_ids"] == [0]
    assert frame_after["active_note_ids"] == [1]


def test_frame_uses_effective_end_from_compiler_for_truncated_note() -> None:
    notes = [NoteEvent(0.0, 1.0, 60, 60, 261.6, 96, 0)]
    engine = _engine(notes, duration_s=1.0, assignments=[0], effective_end_s=[0.4], stolen_note_count=1)

    frame = engine.frame(
        playhead_us=500_000,
        sent_segments=1,
        total_segments=2,
        status=_status(playhead_us=500_000),
        metrics=None,
    )
    assert frame["active_note_ids"] == []


def test_frame_omits_dropped_notes_with_negative_assignment() -> None:
    notes = [
        NoteEvent(0.0, 0.5, 60, 60, 261.6, 96, 0),
        NoteEvent(0.1, 0.4, 64, 64, 329.6, 90, 0),
    ]
    engine = _engine(
        notes,
        duration_s=1.0,
        assignments=[0, -1],
        effective_end_s=[0.5, 0.1],
        dropped_note_count=1,
    )

    frame = engine.frame(
        playhead_us=200_000,
        sent_segments=1,
        total_segments=2,
        status=_status(playhead_us=200_000),
        metrics=None,
    )
    ids = {bar["id"] for bar in frame["bars"]}
    assert ids == {0}

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from music2 import cli
from music2.config import HostConfig
from music2.models import CompileReport, MidiAnalysisReport, NoteEvent, Segment


def _analysis(notes: list[NoteEvent], *, max_polyphony: int) -> MidiAnalysisReport:
    return MidiAnalysisReport(
        notes=notes,
        duration_s=max((note.end_s for note in notes), default=0.0),
        note_count=len(notes),
        max_polyphony=max_polyphony,
        transpose_semitones=0,
        clamped_note_count=0,
        min_source_note=min((note.source_note for note in notes), default=None),
        max_source_note=max((note.source_note for note in notes), default=None),
    )


def test_analyze_and_compile_allows_overflow_when_policy_is_adaptive(monkeypatch, tmp_path: Path) -> None:
    notes = [
        NoteEvent(0.00, 0.40, 60, 60, 261.6, 60, 0),
        NoteEvent(0.00, 0.40, 64, 64, 329.6, 100, 0),
        NoteEvent(0.10, 0.40, 67, 67, 392.0, 90, 0),
    ]
    from music2.midi import TempoMap, TempoPoint
    fake_analysis = _analysis(notes, max_polyphony=3)
    fake_tempo_map = TempoMap(points=[TempoPoint(tick=0, seconds=0.0, tempo=500000)], ticks_per_beat=480)
    monkeypatch.setattr(cli, "analyze_midi", lambda **_: (fake_analysis, fake_tempo_map))

    midi_path = tmp_path / "song.mid"
    midi_path.write_bytes(b"")

    cfg = HostConfig(
        connected_motors=2,
        idle_mode="idle",
        overflow_mode="steal_quietest",
    )
    analysis, compiled, _avg_active, _ = cli._analyze_and_compile(cfg, midi_path)

    assert analysis.max_polyphony == 3
    assert compiled.stolen_note_count == 1
    assert compiled.dropped_note_count == 0
    assert len(compiled.segments) > 0


def test_supports_home_checks_feature_bit() -> None:
    assert cli._supports_home(0x02)
    assert not cli._supports_home(0x01)


def test_preflight_stats_reports_frequency_and_clamp_breakdown() -> None:
    cfg = HostConfig(min_freq_hz=80.0, max_freq_hz=500.0)
    analysis = MidiAnalysisReport(
        notes=[
            NoteEvent(0.00, 0.20, 40, 40, 82.41, 100, 0),   # in range
            NoteEvent(0.20, 0.40, 28, 28, 82.41, 100, 0),   # folded up
            NoteEvent(0.40, 0.60, 90, 90, 369.99, 100, 0),  # folded down
        ],
        duration_s=0.60,
        note_count=3,
        max_polyphony=1,
        transpose_semitones=0,
        clamped_note_count=2,
        min_source_note=28,
        max_source_note=90,
    )

    stats = cli._preflight_stats(analysis, cfg)

    assert stats["min_transposed_note"] == 28
    assert stats["max_transposed_note"] == 90
    assert stats["clamped_below_min"] == 1
    assert stats["clamped_above_max"] == 1
    assert stats["clamped_pct"] == pytest.approx(66.66666666666666)
    assert stats["raw_min_freq_hz"] is not None
    assert stats["raw_max_freq_hz"] is not None
    assert stats["output_min_freq_hz"] == 82.41
    assert stats["output_max_freq_hz"] == 369.99


def test_prompt_play_accepts_enter(monkeypatch: pytest.MonkeyPatch) -> None:
    args = argparse.Namespace(yes=False)
    monkeypatch.setattr("builtins.input", lambda _prompt: "")
    assert cli._prompt_play(args)


def test_prompt_play_can_cancel_with_q(monkeypatch: pytest.MonkeyPatch) -> None:
    args = argparse.Namespace(yes=False)
    monkeypatch.setattr("builtins.input", lambda _prompt: "q")
    assert not cli._prompt_play(args)


def test_start_playback_countdown_waits_ten_seconds(monkeypatch: pytest.MonkeyPatch) -> None:
    args = argparse.Namespace(yes=False)
    captured: list[str] = []
    sleeps: list[float] = []

    monkeypatch.setattr(cli, "_out", lambda text="", end="\n": captured.append(text))
    monkeypatch.setattr(cli.time, "sleep", lambda seconds: sleeps.append(seconds))

    cli._start_playback_countdown(args, seconds=10)

    assert sleeps == [1.0] * 10
    assert any("Starting warmup/music in" in line and "10" in line for line in captured)
    assert any("Starting now." in line for line in captured)


def test_start_playback_countdown_skips_when_yes(monkeypatch: pytest.MonkeyPatch) -> None:
    args = argparse.Namespace(yes=True)
    sleeps: list[float] = []

    monkeypatch.setattr(cli.time, "sleep", lambda seconds: sleeps.append(seconds))

    cli._start_playback_countdown(args, seconds=10)

    assert sleeps == []


def test_start_playback_countdown_skips_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    args = argparse.Namespace(yes=False)
    sleeps: list[float] = []

    monkeypatch.setattr(cli.time, "sleep", lambda seconds: sleeps.append(seconds))

    cli._start_playback_countdown(args, seconds=0)

    assert sleeps == []


def test_frequency_histogram_lines_include_caps_and_timeline() -> None:
    cfg = HostConfig(min_freq_hz=80.0, max_freq_hz=500.0)
    analysis = MidiAnalysisReport(
        notes=[
            NoteEvent(0.00, 0.20, 40, 40, 82.41, 100, 0),
            NoteEvent(0.20, 0.40, 52, 52, 164.81, 90, 0),
            NoteEvent(0.40, 0.60, 69, 69, 440.00, 80, 0),
        ],
        duration_s=0.60,
        note_count=3,
        max_polyphony=1,
        transpose_semitones=0,
        clamped_note_count=0,
        min_source_note=40,
        max_source_note=69,
    )

    lines = cli._frequency_histogram_lines(analysis=analysis, cfg=cfg, width=36, height=6)
    rendered = "\n".join(lines)

    assert "caps: min 80.0 Hz, max 500.0 Hz" in rendered
    assert "timeline:" in rendered
    assert "█" in rendered
    assert any(marker in rendered for marker in ("│", "┼", "╋"))


def test_transpose_studio_prompt_steps_up_with_arrow_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    args = argparse.Namespace(yes=False)
    midi_path = tmp_path / "song.mid"
    midi_path.write_bytes(b"MThd")

    cfg = HostConfig(min_freq_hz=80.0, max_freq_hz=500.0, auto_transpose=True, transpose_override=None)
    base_analysis = MidiAnalysisReport(
        notes=[NoteEvent(0.00, 0.20, 60, 60, 261.63, 100, 0)],
        duration_s=0.20,
        note_count=1,
        max_polyphony=1,
        transpose_semitones=0,
        clamped_note_count=0,
        min_source_note=60,
        max_source_note=60,
    )
    base_compiled = CompileReport(
        segments=[Segment(duration_us=200_000, motor_freq_hz=(261.63, 0.0, 0.0, 0.0, 0.0, 0.0))],
        assignments=[0],
        duplicated_slots=0,
        connected_motors=6,
        overflow_mode="steal_quietest",
    )

    stepped_analysis = MidiAnalysisReport(
        notes=[NoteEvent(0.00, 0.20, 60, 61, 277.18, 100, 0)],
        duration_s=0.20,
        note_count=1,
        max_polyphony=1,
        transpose_semitones=1,
        clamped_note_count=0,
        min_source_note=60,
        max_source_note=60,
    )
    stepped_compiled = CompileReport(
        segments=[Segment(duration_us=200_000, motor_freq_hz=(277.18, 0.0, 0.0, 0.0, 0.0, 0.0))],
        assignments=[0],
        duplicated_slots=0,
        connected_motors=6,
        overflow_mode="steal_quietest",
    )

    recompute_calls: list[int | None] = []

    def fake_analyze_and_compile(next_cfg: HostConfig, _midi_path: Path):
        from music2.midi import TempoMap, TempoPoint
        recompute_calls.append(next_cfg.transpose_override)
        assert next_cfg.auto_transpose is False
        fake_tm = TempoMap(points=[TempoPoint(tick=0, seconds=0.0, tempo=500000)], ticks_per_beat=480)
        return stepped_analysis, stepped_compiled, 1.0, fake_tm

    monkeypatch.setattr(cli, "_analyze_and_compile", fake_analyze_and_compile)
    key_inputs = iter(["\x1b[A", "\n"])

    accepted, out_cfg, out_analysis, out_compiled, out_avg = cli._transpose_studio_prompt(
        args=args,
        cfg=cfg,
        midi_path=midi_path,
        analysis=base_analysis,
        compiled=base_compiled,
        avg_active=1.0,
        key_reader=lambda: next(key_inputs),
        render_screen=lambda _lines: None,
    )

    assert accepted
    assert recompute_calls == [1]
    assert out_cfg.transpose_override == 1
    assert out_cfg.auto_transpose is False
    assert out_analysis == stepped_analysis
    assert out_compiled == stepped_compiled
    assert out_avg == 1.0


def test_transpose_studio_prompt_falls_back_to_line_prompt(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    args = argparse.Namespace(yes=False)
    midi_path = tmp_path / "song.mid"
    midi_path.write_bytes(b"MThd")

    cfg = HostConfig()
    analysis = MidiAnalysisReport(
        notes=[NoteEvent(0.00, 0.20, 60, 60, 261.63, 100, 0)],
        duration_s=0.20,
        note_count=1,
        max_polyphony=1,
        transpose_semitones=0,
        clamped_note_count=0,
        min_source_note=60,
        max_source_note=60,
    )
    compiled = CompileReport(
        segments=[Segment(duration_us=200_000, motor_freq_hz=(261.63, 0.0, 0.0, 0.0, 0.0, 0.0))],
        assignments=[0],
        duplicated_slots=0,
    )

    prompt_calls: list[str] = []
    monkeypatch.setattr(cli, "_supports_transpose_studio", lambda: False)
    monkeypatch.setattr(cli, "_prompt_play", lambda _args: prompt_calls.append("prompt") or True)

    accepted, out_cfg, out_analysis, out_compiled, out_avg = cli._transpose_studio_prompt(
        args=args,
        cfg=cfg,
        midi_path=midi_path,
        analysis=analysis,
        compiled=compiled,
        avg_active=1.0,
    )

    assert accepted
    assert out_cfg is cfg
    assert out_analysis is analysis
    assert out_compiled is compiled
    assert out_avg == 1.0
    assert prompt_calls == []


def test_build_warmup_params_passes_steps_per_rev(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = HostConfig(
        connected_motors=6,
        home_steps_per_rev=800,
        pre_song_warmups=("slot_machine_lock_in",),
        warmup_motor_order=(4, 2, 1, 3, 0, 5),
        warmup_speed_multipliers=(("slot_machine_lock_in", 1.35),),
    )
    seen: dict[str, object] = {}

    def fake_build(
        sequence,
        *,
        connected_motors: int,
        steps_per_rev: int,
        motor_order,
        speed_multipliers,
        max_accel_hz_per_s: float,
    ):
        seen["sequence"] = tuple(sequence)
        seen["connected_motors"] = connected_motors
        seen["steps_per_rev"] = steps_per_rev
        seen["motor_order"] = tuple(motor_order)
        seen["speed_multipliers"] = dict(speed_multipliers)
        seen["max_accel_hz_per_s"] = max_accel_hz_per_s
        return []

    monkeypatch.setattr(cli, "build_warmup_params", fake_build)
    warmup_routines = cli._build_warmup_params(cfg)

    assert warmup_routines == []
    assert seen["sequence"] == ("slot_machine_lock_in",)
    assert seen["connected_motors"] == 6
    assert seen["steps_per_rev"] == 800
    assert seen["motor_order"] == (4, 2, 1, 3, 0, 5)
    assert seen["speed_multipliers"] == {"slot_machine_lock_in": 1.35}
    assert seen["max_accel_hz_per_s"] == pytest.approx(180.0)


def test_build_warmup_step_motion_routines_passes_motor_order(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = HostConfig(
        connected_motors=6,
        home_steps_per_rev=800,
        pre_song_warmups=("chord_bloom",),
        warmup_motor_order=(4, 2, 1, 3, 0, 5),
        warmup_speed_multipliers=(("chord_bloom", 1.1),),
    )
    seen: dict[str, object] = {}

    def fake_build(
        sequence,
        *,
        connected_motors: int,
        steps_per_rev: int,
        motor_order,
        speed_multipliers,
        max_accel_hz_per_s: float,
    ):
        seen["sequence"] = tuple(sequence)
        seen["connected_motors"] = connected_motors
        seen["steps_per_rev"] = steps_per_rev
        seen["motor_order"] = tuple(motor_order)
        seen["speed_multipliers"] = dict(speed_multipliers)
        seen["max_accel_hz_per_s"] = max_accel_hz_per_s
        return []

    monkeypatch.setattr(cli, "build_warmup_step_motion_params", fake_build)
    warmup_routines = cli._build_warmup_step_motion_routines(cfg)

    assert warmup_routines == []
    assert seen["sequence"] == ("chord_bloom",)
    assert seen["connected_motors"] == 6
    assert seen["steps_per_rev"] == 800
    assert seen["motor_order"] == (4, 2, 1, 3, 0, 5)
    assert seen["speed_multipliers"] == {"chord_bloom": 1.1}
    assert seen["max_accel_hz_per_s"] == pytest.approx(180.0)


def test_log_axis_maps_octaves_to_similar_width() -> None:
    width = 120
    lo = 20.0
    hi = 1_280.0
    low_oct = cli._to_index(40.0, lo, hi, width, axis="log") - cli._to_index(20.0, lo, hi, width, axis="log")
    high_oct = cli._to_index(640.0, lo, hi, width, axis="log") - cli._to_index(320.0, lo, hi, width, axis="log")
    assert abs(low_oct - high_oct) <= 1

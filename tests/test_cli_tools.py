from __future__ import annotations

import json
from pathlib import Path

import pytest

from music2 import cli
from music2.models import (
    CompileReport,
    MidiAnalysisReport,
    NoteEvent,
    PlaybackEventGroup,
    PlaybackMetrics,
    PlaybackMotorChange,
    PlaybackStartAnchor,
    Segment,
)
from music2.render_wav import RenderWavResult

_CONTINUOUS_FEATURES = (
    cli.FEATURE_FLAG_TIMED_STREAMING
    | cli.FEATURE_FLAG_CONTINUOUS_PLAYBACK_ENGINE
    | cli.FEATURE_FLAG_PLAYBACK_SETUP_PROFILE
)


def _event_group(delta_us: int, target_hz: float, *, motor_idx: int = 0, flip_before_restart: bool = False) -> PlaybackEventGroup:
    return PlaybackEventGroup(
        delta_us=delta_us,
        changes=(PlaybackMotorChange(motor_idx=motor_idx, target_hz=target_hz, flip_before_restart=flip_before_restart),),
    )


def _single_note_report(freq_hz: float, *, duration_us: int = 500_000) -> CompileReport:
    return CompileReport(
        segments=[Segment(duration_us=duration_us, motor_freq_hz=(freq_hz, 0.0, 0.0, 0.0, 0.0, 0.0))],
        event_groups=[_event_group(duration_us, freq_hz)],
        assignments=[0],
        duplicated_slots=0,
        connected_motors=6,
        overflow_mode="steal_quietest",
    )


def test_parser_supports_analyze_and_doctor_commands() -> None:
    parser = cli.build_parser()
    analyze_args = parser.parse_args(["analyze", "assets/midi/simple4.mid"])
    doctor_args = parser.parse_args(["doctor"])
    render_args = parser.parse_args(["render-wav", "assets/midi/simple4.mid"])
    assert analyze_args.command == "analyze"
    assert doctor_args.command == "doctor"
    assert render_args.command == "render-wav"
    assert render_args.transpose == 12


def test_parser_accepts_percentile_lookahead_strategy() -> None:
    parser = cli.build_parser()
    args = parser.parse_args(
        [
            "run",
            "assets/midi/simple4.mid",
            "--lookahead-strategy",
            "percentile",
            "--lookahead-percentile",
            "88",
        ]
    )
    assert args.lookahead_strategy == "percentile"
    assert args.lookahead_percentile == 88


def test_render_wav_command_invokes_renderer(monkeypatch, tmp_path: Path) -> None:
    midi_path = tmp_path / "song.mid"
    midi_path.write_bytes(b"MThd")
    out_path = tmp_path / "song.stepper.wav"
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text("", encoding="utf-8")

    parser = cli.build_parser()
    args = parser.parse_args(
        [
            "render-wav",
            str(midi_path),
            "--config",
            str(cfg_path),
            "--out",
            str(out_path),
            "--sample-rate",
            "8000",
        ]
    )

    called: dict[str, object] = {}
    captured: list[str] = []

    def _fake_render(*, midi_path, cfg, out_wav, options):
        called["midi_path"] = midi_path
        called["out_wav"] = out_wav
        called["sample_rate"] = options.sample_rate
        called["transpose_override"] = cfg.transpose_override
        called["clamp_frequencies"] = options.clamp_frequencies
        return RenderWavResult(
            wav_path=out_path.resolve(),
            metadata_path=(tmp_path / "song.stepper.wav.meta.json").resolve(),
            duration_s=1.0,
            sample_rate=8_000,
            peak=0.9,
            rms=0.2,
            segment_count=4,
        )

    monkeypatch.setattr(cli, "_out", lambda text="", end="\n": captured.append(text))
    monkeypatch.setattr(cli, "render_midi_to_stepper_wav", _fake_render)

    rc = cli.render_wav_command(args)

    assert rc == 0
    assert called["midi_path"] == midi_path
    assert called["out_wav"] == out_path
    assert called["sample_rate"] == 8_000
    assert called["transpose_override"] == 12
    assert called["clamp_frequencies"] is False
    assert any("music2 · render-wav" in line for line in captured)


def test_render_wav_command_respects_explicit_transpose(monkeypatch, tmp_path: Path) -> None:
    midi_path = tmp_path / "song.mid"
    midi_path.write_bytes(b"MThd")
    out_path = tmp_path / "song.stepper.wav"
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text("", encoding="utf-8")

    parser = cli.build_parser()
    args = parser.parse_args(
        [
            "render-wav",
            str(midi_path),
            "--config",
            str(cfg_path),
            "--out",
            str(out_path),
            "--transpose",
            "12",
        ]
    )

    called: dict[str, object] = {}

    def _fake_render(*, midi_path, cfg, out_wav, options):
        called["transpose_override"] = cfg.transpose_override
        return RenderWavResult(
            wav_path=out_path.resolve(),
            metadata_path=(tmp_path / "song.stepper.wav.meta.json").resolve(),
            duration_s=1.0,
            sample_rate=48_000,
            peak=0.9,
            rms=0.2,
            segment_count=4,
        )

    monkeypatch.setattr(cli, "render_midi_to_stepper_wav", _fake_render)
    monkeypatch.setattr(cli, "_out", lambda text="", end="\n": None)

    rc = cli.render_wav_command(args)
    assert rc == 0
    assert called["transpose_override"] == 12


def test_render_wav_command_can_enable_clamp_frequencies(monkeypatch, tmp_path: Path) -> None:
    midi_path = tmp_path / "song.mid"
    midi_path.write_bytes(b"MThd")
    out_path = tmp_path / "song.stepper.wav"
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text("", encoding="utf-8")

    parser = cli.build_parser()
    args = parser.parse_args(
        [
            "render-wav",
            str(midi_path),
            "--config",
            str(cfg_path),
            "--out",
            str(out_path),
            "--clamp-frequencies",
        ]
    )

    called: dict[str, object] = {}

    def _fake_render(*, midi_path, cfg, out_wav, options):
        called["clamp_frequencies"] = options.clamp_frequencies
        return RenderWavResult(
            wav_path=out_path.resolve(),
            metadata_path=(tmp_path / "song.stepper.wav.meta.json").resolve(),
            duration_s=1.0,
            sample_rate=48_000,
            peak=0.9,
            rms=0.2,
            segment_count=4,
        )

    monkeypatch.setattr(cli, "render_midi_to_stepper_wav", _fake_render)
    monkeypatch.setattr(cli, "_out", lambda text="", end="\n": None)
    rc = cli.render_wav_command(args)
    assert rc == 0
    assert called["clamp_frequencies"] is True


def test_profile_defaults_apply_to_run_config(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text("", encoding="utf-8")
    parser = cli.build_parser()
    args = parser.parse_args([
        "run",
        "assets/midi/simple4.mid",
        "--config",
        str(cfg_path),
        "--profile",
        "clean",
    ])
    cfg = cli._build_config(args)
    assert cfg.lookahead_strategy == "p95"
    assert cfg.lookahead_min_segments == 24


def test_build_config_overrides_instrument_profile_path(tmp_path: Path) -> None:
    profile_path = tmp_path / "profile.toml"
    profile_path.write_text(
        """
[instrument]
name = "solo"
profile_version = 1
motor_count = 1

[[instrument.motors]]
motor_idx = 0
min_hz = 30.0
max_hz = 300.0
""".strip()
        + "\n",
        encoding="utf-8",
    )
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text("", encoding="utf-8")
    parser = cli.build_parser()
    args = parser.parse_args([
        "run",
        "assets/midi/simple4.mid",
        "--config",
        str(cfg_path),
        "--instrument-profile",
        str(profile_path),
    ])

    cfg = cli._build_config(args)

    assert cfg.instrument_profile_path == str(profile_path.resolve())


def test_build_config_preserves_ui_defaults_from_config(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        """
[ui]
color_mode = "frequency_bands"
color_modes = ["frequency_bands", "motor_slot"]
show_controls = false
""".strip()
        + "\n",
        encoding="utf-8",
    )
    parser = cli.build_parser()
    args = parser.parse_args([
        "run",
        "assets/midi/simple4.mid",
        "--config",
        str(cfg_path),
    ])

    cfg = cli._build_config(args)

    assert cfg.ui_color_mode == "frequency_bands"
    assert cfg.ui_color_modes == ("frequency_bands", "motor_slot")
    assert cfg.ui_show_controls is False


def test_build_config_preserves_playback_countdown_from_config(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        """
[playback]
startup_countdown_s = 3
""".strip()
        + "\n",
        encoding="utf-8",
    )
    parser = cli.build_parser()
    args = parser.parse_args([
        "run",
        "assets/midi/simple4.mid",
        "--config",
        str(cfg_path),
    ])

    cfg = cli._build_config(args)

    assert cfg.startup_countdown_s == 3


def test_build_config_preserves_playback_flip_guards_from_config(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        """
[playback]
direction_flip_cooldown_ms = 220
direction_flip_safety_margin_ms = 75
""".strip()
        + "\n",
        encoding="utf-8",
    )
    parser = cli.build_parser()
    args = parser.parse_args([
        "run",
        "assets/midi/simple4.mid",
        "--config",
        str(cfg_path),
    ])

    cfg = cli._build_config(args)

    assert cfg.direction_flip_cooldown_ms == 220.0
    assert cfg.direction_flip_safety_margin_ms == 75.0


def test_build_config_rejects_removed_pipeline_mitigation_config(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        """
[pipeline]
max_active_playback_motors = 4
max_aggregate_step_rate = 6000
""".strip()
        + "\n",
        encoding="utf-8",
    )
    parser = cli.build_parser()
    args = parser.parse_args([
        "run",
        "assets/midi/simple4.mid",
        "--config",
        str(cfg_path),
    ])

    with pytest.raises(ValueError, match="obsolete \\[pipeline\\] playback-mitigation keys"):
        cli._build_config(args)


def test_analyze_command_json_output(monkeypatch, tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text("", encoding="utf-8")
    parser = cli.build_parser()
    args = parser.parse_args([
        "analyze",
        "assets/midi/simple4.mid",
        "--config",
        str(cfg_path),
        "--json",
    ])

    captured: list[str] = []
    monkeypatch.setattr(cli, "_out", lambda text="", end="\n": captured.append(text))

    rc = cli.analyze_command(args)
    assert rc == 0
    assert captured
    payload = json.loads(captured[-1])
    assert "playback_plan" in payload
    assert "allocation" in payload
    assert payload["instrument"]["motor_count"] == 6
    assert payload["instrument"]["profile_name"] == "default_bench_6motor_v2"
    assert "arrangement" in payload
    assert "weighted_musical_loss" in payload["arrangement"]
    assert "direction_flip_cooldown_suppressed_count" in payload["allocation"]


def test_run_command_renders_summary_before_transpose_prompt(monkeypatch, tmp_path: Path) -> None:
    midi_path = tmp_path / "song.mid"
    midi_path.write_bytes(b"MThd")
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text("", encoding="utf-8")

    analysis = MidiAnalysisReport(
        notes=[NoteEvent(0.0, 0.5, 60, 60, 261.63, 100, 0)],
        duration_s=0.5,
        note_count=1,
        max_polyphony=1,
        transpose_semitones=0,
        clamped_note_count=0,
        min_source_note=60,
        max_source_note=60,
    )
    compiled = _single_note_report(261.63)

    parser = cli.build_parser()
    args = parser.parse_args(
        [
            "run",
            str(midi_path),
            "--config",
            str(cfg_path),
        ]
    )

    call_order: list[str] = []
    captured: list[str] = []

    monkeypatch.setattr(cli, "_out", lambda text="", end="\n": captured.append(text))
    monkeypatch.setattr(cli, "_analyze_and_compile", lambda _cfg, _path, **_kw: (analysis, compiled, 1.0, None))
    monkeypatch.setattr(cli, "_render_run_summary", lambda **_kwargs: call_order.append("summary"))

    def fake_prompt(**kwargs):
        call_order.append("prompt")
        return False, kwargs["cfg"], kwargs["analysis"], kwargs["compiled"], kwargs["avg_active"]

    monkeypatch.setattr(cli, "_transpose_studio_prompt", fake_prompt)

    rc = cli.run_command(args)

    assert rc == 0
    assert call_order == ["summary", "prompt"]
    assert any("Canceled before playback start." in line for line in captured)


def test_run_command_captures_metrics_before_auto_home(monkeypatch, tmp_path: Path) -> None:
    midi_path = tmp_path / "song.mid"
    midi_path.write_bytes(b"MThd")
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text("", encoding="utf-8")

    note = NoteEvent(
        start_s=0.0,
        end_s=0.5,
        source_note=60,
        transposed_note=48,
        frequency_hz=130.81,
        velocity=100,
        channel=0,
    )
    analysis = MidiAnalysisReport(
        notes=[note],
        duration_s=0.5,
        note_count=1,
        max_polyphony=1,
        transpose_semitones=-12,
        clamped_note_count=0,
        min_source_note=60,
        max_source_note=60,
    )
    compiled = _single_note_report(130.81)

    good_metrics = PlaybackMetrics(
        underrun_count=0,
        queue_high_water=32,
        scheduling_late_max_us=250,
        crc_parse_errors=0,
        queue_depth=0,
        credits=128,
        rx_parse_errors=0,
        timer_empty_events=0,
        timer_restart_count=0,
        event_groups_started=1,
        scheduler_guard_hits=0,
        control_late_max_us=40,
        control_overrun_count=0,
        launch_guard_count=0,
    )
    bad_metrics = PlaybackMetrics(
        underrun_count=0,
        queue_high_water=32,
        scheduling_late_max_us=250,
        crc_parse_errors=0,
        queue_depth=0,
        credits=128,
        rx_parse_errors=0,
        timer_empty_events=0,
        timer_restart_count=0,
        event_groups_started=1,
        scheduler_guard_hits=0,
        control_late_max_us=400,
        control_overrun_count=9,
        launch_guard_count=0,
    )

    calls: list[str] = []
    home_done = {"value": False}

    class _FakeSerialClient:
        def __init__(self, **_kwargs) -> None:
            self.status_soft_fail_count = 0

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def hello(self) -> dict[str, int]:
            calls.append("hello")
            return {
                "queue_capacity": 128,
                "scheduler_tick_us": 10,
                "motor_count": 8,
                "playback_motor_count": 6,
                "protocol_version": 2,
                "feature_flags": _CONTINUOUS_FEATURES | cli.FEATURE_FLAG_HOME,
            }

        def setup(self, **_kwargs) -> None:
            calls.append("setup")

        def stream_song_and_play(self, *_args, **_kwargs) -> None:
            calls.append("stream_song_and_play")

        def stop(self) -> None:
            calls.append("stop")

        def home(self, **_kwargs) -> None:
            calls.append("home")
            home_done["value"] = True

        def metrics(self) -> PlaybackMetrics:
            calls.append("metrics")
            return bad_metrics if home_done["value"] else good_metrics

    parser = cli.build_parser()
    args = parser.parse_args(
        [
            "run",
            str(midi_path),
            "--config",
            str(cfg_path),
            "--yes",
        ]
    )

    captured: list[str] = []
    monkeypatch.setattr(cli, "_out", lambda text="", end="\n": captured.append(text))
    monkeypatch.setattr(cli, "SerialClient", _FakeSerialClient)
    monkeypatch.setattr(cli, "_analyze_and_compile", lambda _cfg, _path, **_kw: (analysis, compiled, 1.0, None))
    monkeypatch.setattr(cli, "_prompt_play", lambda _args: True)

    rc = cli.run_command(args)

    assert rc == 0
    assert "metrics" in calls
    assert "home" in calls
    assert calls.index("metrics") < calls.index("home")
    assert "pulse edge drops" not in "\n".join(captured)
    assert "Pulse late" not in "\n".join(captured)
    assert "Event groups" in "\n".join(captured)
    assert "Control overruns" not in "\n".join(captured)


def test_run_command_skips_auto_home_when_position_tracking_is_unreliable(
    monkeypatch, tmp_path: Path
) -> None:
    midi_path = tmp_path / "song.mid"
    midi_path.write_bytes(b"MThd")
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text("", encoding="utf-8")

    note = NoteEvent(
        start_s=0.0,
        end_s=0.5,
        source_note=60,
        transposed_note=60,
        frequency_hz=261.63,
        velocity=100,
        channel=0,
    )
    analysis = MidiAnalysisReport(
        notes=[note],
        duration_s=0.5,
        note_count=1,
        max_polyphony=1,
        transpose_semitones=0,
        clamped_note_count=0,
        min_source_note=60,
        max_source_note=60,
    )
    compiled = _single_note_report(261.63)
    metrics = PlaybackMetrics(
        underrun_count=0,
        queue_high_water=16,
        scheduling_late_max_us=100,
        crc_parse_errors=0,
        queue_depth=0,
        credits=128,
        rx_parse_errors=0,
        timer_empty_events=0,
        timer_restart_count=0,
        event_groups_started=1,
        scheduler_guard_hits=0,
        control_late_max_us=50,
        control_overrun_count=0,
        launch_guard_count=0,
        exact_position_lost_mask=0x03,
        playback_signed_position_drift_total=21,
    )
    calls: list[str] = []

    class _FakeSerialClient:
        def __init__(self, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def hello(self) -> dict[str, int]:
            calls.append("hello")
            return {
                "queue_capacity": 128,
                "scheduler_tick_us": 10,
                "motor_count": 8,
                "playback_motor_count": 6,
                "protocol_version": 2,
                "feature_flags": _CONTINUOUS_FEATURES | cli.FEATURE_FLAG_HOME,
            }

        def setup(self, **_kwargs) -> None:
            calls.append("setup")

        def stream_song_and_play(self, *_args, **_kwargs) -> None:
            calls.append("stream_song_and_play")

        def stop(self) -> None:
            calls.append("stop")

        def home(self, **_kwargs) -> None:
            calls.append("home")

        def metrics(self) -> PlaybackMetrics:
            calls.append("metrics")
            return metrics

    parser = cli.build_parser()
    args = parser.parse_args([
        "run",
        str(midi_path),
        "--config",
        str(cfg_path),
        "--yes",
    ])

    captured: list[str] = []
    monkeypatch.setattr(cli, "_out", lambda text="", end="\n": captured.append(text))
    monkeypatch.setattr(cli, "SerialClient", _FakeSerialClient)
    monkeypatch.setattr(cli, "_analyze_and_compile", lambda _cfg, _path, **_kw: (analysis, compiled, 1.0, None))
    monkeypatch.setattr(cli, "_prompt_play", lambda _args: True)

    rc = cli.run_command(args)

    joined = "\n".join(captured)
    assert rc == 0
    assert "metrics" in calls
    assert "home" not in calls
    assert "Auto-home" in joined
    assert "skipped: exact position tracking unreliable" in joined
    assert "Exact pos lost" in joined
    assert "Signed drift" in joined


def test_run_command_surfaces_engine_fault_warnings(monkeypatch, tmp_path: Path) -> None:
    midi_path = tmp_path / "song.mid"
    midi_path.write_bytes(b"MThd")
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text("", encoding="utf-8")

    note = NoteEvent(
        start_s=0.0,
        end_s=0.5,
        source_note=60,
        transposed_note=60,
        frequency_hz=261.63,
        velocity=100,
        channel=0,
    )
    analysis = MidiAnalysisReport(
        notes=[note],
        duration_s=0.5,
        note_count=1,
        max_polyphony=1,
        transpose_semitones=0,
        clamped_note_count=0,
        min_source_note=60,
        max_source_note=60,
    )
    compiled = _single_note_report(261.63)
    metrics = PlaybackMetrics(
        underrun_count=0,
        queue_high_water=32,
        scheduling_late_max_us=80,
        crc_parse_errors=0,
        queue_depth=0,
        credits=128,
        rx_parse_errors=0,
        timer_empty_events=0,
        timer_restart_count=0,
        event_groups_started=1,
        scheduler_guard_hits=0,
        control_late_max_us=30,
        control_overrun_count=0,
        wave_period_update_count=44,
        motor_start_count=41,
        motor_stop_count=39,
        flip_restart_count=6,
        launch_guard_count=2,
        engine_fault_count=2,
        engine_fault_mask=0x03,
    )

    class _FakeSerialClient:
        def __init__(self, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def hello(self) -> dict[str, int]:
            return {
                "queue_capacity": 128,
                "scheduler_tick_us": 10,
                "motor_count": 8,
                "playback_motor_count": 6,
                "protocol_version": 2,
                "feature_flags": _CONTINUOUS_FEATURES | cli.FEATURE_FLAG_HOME,
            }

        def setup(self, **_kwargs) -> None:
            return None

        def stream_song_and_play(self, *_args, **_kwargs) -> None:
            return None

        def stop(self) -> None:
            return None

        def home(self, **_kwargs) -> None:
            return None

        def metrics(self) -> PlaybackMetrics:
            return metrics

    parser = cli.build_parser()
    args = parser.parse_args(
        [
            "run",
            str(midi_path),
            "--config",
            str(cfg_path),
            "--yes",
        ]
    )

    captured: list[str] = []
    monkeypatch.setattr(cli, "_out", lambda text="", end="\n": captured.append(text))
    monkeypatch.setattr(cli, "SerialClient", _FakeSerialClient)
    monkeypatch.setattr(cli, "_analyze_and_compile", lambda _cfg, _path, **_kw: (analysis, compiled, 1.0, None))
    monkeypatch.setattr(cli, "_prompt_play", lambda _args: True)

    rc = cli.run_command(args)

    joined = "\n".join(captured)
    assert rc == 0
    assert "Complete (with warnings)" in joined
    assert "Wave updates" in joined
    assert "Motor state" in joined
    assert "Launch guards" in joined
    assert "Engine faults" in joined
    assert "Engine mask" in joined
    assert "Control overruns" not in joined


def test_run_command_skips_auto_home_when_disabled(monkeypatch, tmp_path: Path) -> None:
    midi_path = tmp_path / "song.mid"
    midi_path.write_bytes(b"MThd")
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        """
[homing]
auto_home = false
""".strip()
        + "\n",
        encoding="utf-8",
    )

    note = NoteEvent(
        start_s=0.0,
        end_s=0.5,
        source_note=60,
        transposed_note=60,
        frequency_hz=261.63,
        velocity=100,
        channel=0,
    )
    analysis = MidiAnalysisReport(
        notes=[note],
        duration_s=0.5,
        note_count=1,
        max_polyphony=1,
        transpose_semitones=0,
        clamped_note_count=0,
        min_source_note=60,
        max_source_note=60,
    )
    compiled = _single_note_report(261.63)
    metrics = PlaybackMetrics(
        underrun_count=0,
        queue_high_water=16,
        scheduling_late_max_us=100,
        crc_parse_errors=0,
        queue_depth=0,
        credits=128,
        rx_parse_errors=0,
        timer_empty_events=0,
        timer_restart_count=0,
        event_groups_started=1,
        scheduler_guard_hits=0,
        control_late_max_us=50,
        control_overrun_count=0,
        launch_guard_count=0,
    )
    calls: list[str] = []

    class _FakeSerialClient:
        def __init__(self, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def hello(self) -> dict[str, int]:
            calls.append("hello")
            return {
                "queue_capacity": 128,
                "scheduler_tick_us": 10,
                "motor_count": 8,
                "playback_motor_count": 6,
                "protocol_version": 2,
                "feature_flags": _CONTINUOUS_FEATURES | cli.FEATURE_FLAG_HOME,
            }

        def setup(self, **_kwargs) -> None:
            calls.append("setup")

        def stream_song_and_play(self, *_args, **_kwargs) -> None:
            calls.append("stream_song_and_play")

        def stop(self) -> None:
            calls.append("stop")

        def home(self, **_kwargs) -> None:
            calls.append("home")

        def metrics(self) -> PlaybackMetrics:
            calls.append("metrics")
            return metrics

    parser = cli.build_parser()
    args = parser.parse_args([
        "run",
        str(midi_path),
        "--config",
        str(cfg_path),
        "--yes",
    ])

    captured: list[str] = []
    monkeypatch.setattr(cli, "_out", lambda text="", end="\n": captured.append(text))
    monkeypatch.setattr(cli, "SerialClient", _FakeSerialClient)
    monkeypatch.setattr(cli, "_analyze_and_compile", lambda _cfg, _path, **_kw: (analysis, compiled, 1.0, None))
    monkeypatch.setattr(cli, "_prompt_play", lambda _args: True)

    rc = cli.run_command(args)

    assert rc == 0
    assert "home" not in calls
    assert "metrics" in calls
    assert any("disabled in config" in line.lower() for line in captured)


def test_run_command_warns_when_post_playback_auto_home_fails(monkeypatch, tmp_path: Path) -> None:
    midi_path = tmp_path / "song.mid"
    midi_path.write_bytes(b"MThd")
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text("", encoding="utf-8")

    note = NoteEvent(
        start_s=0.0,
        end_s=0.5,
        source_note=60,
        transposed_note=60,
        frequency_hz=261.63,
        velocity=100,
        channel=0,
    )
    analysis = MidiAnalysisReport(
        notes=[note],
        duration_s=0.5,
        note_count=1,
        max_polyphony=1,
        transpose_semitones=0,
        clamped_note_count=0,
        min_source_note=60,
        max_source_note=60,
    )
    compiled = _single_note_report(261.63)
    metrics = PlaybackMetrics(
        underrun_count=0,
        queue_high_water=16,
        scheduling_late_max_us=100,
        crc_parse_errors=0,
        queue_depth=0,
        credits=128,
        rx_parse_errors=0,
        timer_empty_events=0,
        timer_restart_count=0,
        event_groups_started=1,
        scheduler_guard_hits=0,
        control_late_max_us=50,
        control_overrun_count=0,
        launch_guard_count=0,
    )
    calls: list[str] = []

    class _FakeSerialClient:
        def __init__(self, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def hello(self) -> dict[str, int]:
            calls.append("hello")
            return {
                "queue_capacity": 128,
                "scheduler_tick_us": 10,
                "motor_count": 8,
                "playback_motor_count": 6,
                "protocol_version": 2,
                "feature_flags": _CONTINUOUS_FEATURES | cli.FEATURE_FLAG_HOME,
            }

        def setup(self, **_kwargs) -> None:
            calls.append("setup")

        def stream_song_and_play(self, *_args, **_kwargs) -> None:
            calls.append("stream_song_and_play")

        def stop(self) -> None:
            calls.append("stop")

        def home(self, **_kwargs) -> None:
            calls.append("home")
            raise cli.SerialClientError("device error command=0x0a code=6")

        def metrics(self) -> PlaybackMetrics:
            calls.append("metrics")
            return metrics

    parser = cli.build_parser()
    args = parser.parse_args([
        "run",
        str(midi_path),
        "--config",
        str(cfg_path),
        "--yes",
    ])

    captured: list[str] = []
    monkeypatch.setattr(cli, "_out", lambda text="", end="\n": captured.append(text))
    monkeypatch.setattr(cli, "SerialClient", _FakeSerialClient)
    monkeypatch.setattr(cli, "_analyze_and_compile", lambda _cfg, _path, **_kw: (analysis, compiled, 1.0, None))
    monkeypatch.setattr(cli, "_prompt_play", lambda _args: True)

    rc = cli.run_command(args)

    joined = "\n".join(captured)
    assert rc == 0
    assert calls.index("metrics") < calls.index("home")
    assert "Auto-home" in joined
    assert "failed: device error command=0x0a code=6" in joined


def test_run_command_rejects_direction_flip_when_firmware_lacks_support(monkeypatch, tmp_path: Path) -> None:
    midi_path = tmp_path / "song.mid"
    midi_path.write_bytes(b"MThd")
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        """
[playback]
flip_direction_on_note_change = true
""".strip()
        + "\n",
        encoding="utf-8",
    )

    analysis = MidiAnalysisReport(
        notes=[NoteEvent(0.0, 0.5, 60, 60, 261.63, 100, 0)],
        duration_s=0.5,
        note_count=1,
        max_polyphony=1,
        transpose_semitones=0,
        clamped_note_count=0,
        min_source_note=60,
        max_source_note=60,
    )
    compiled = _single_note_report(261.63)

    class _FakeSerialClient:
        def __init__(self, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def hello(self) -> dict[str, int]:
            return {
                "queue_capacity": 128,
                "scheduler_tick_us": 10,
                "motor_count": 8,
                "playback_motor_count": 6,
                "protocol_version": 2,
                "feature_flags": _CONTINUOUS_FEATURES | cli.FEATURE_FLAG_HOME,
            }

        def stop(self) -> None:
            return None

        def metrics(self) -> PlaybackMetrics:
            return PlaybackMetrics(
                underrun_count=0,
                queue_high_water=0,
                scheduling_late_max_us=0,
                crc_parse_errors=0,
                queue_depth=0,
                credits=128,
            )

    parser = cli.build_parser()
    args = parser.parse_args([
        "run",
        str(midi_path),
        "--config",
        str(cfg_path),
        "--yes",
    ])

    captured: list[str] = []
    monkeypatch.setattr(cli, "_out", lambda text="", end="\n": captured.append(text))
    monkeypatch.setattr(cli, "SerialClient", _FakeSerialClient)
    monkeypatch.setattr(cli, "_analyze_and_compile", lambda _cfg, _path, **_kw: (analysis, compiled, 1.0, None))

    with pytest.raises(RuntimeError, match="direction-flip playback support"):
        cli.run_command(args)


def test_run_command_requires_final_enter_after_transpose_lock_in(monkeypatch, tmp_path: Path) -> None:
    midi_path = tmp_path / "song.mid"
    midi_path.write_bytes(b"MThd")
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text("", encoding="utf-8")

    analysis = MidiAnalysisReport(
        notes=[NoteEvent(0.0, 0.5, 60, 60, 261.63, 100, 0)],
        duration_s=0.5,
        note_count=1,
        max_polyphony=1,
        transpose_semitones=1,
        clamped_note_count=0,
        min_source_note=60,
        max_source_note=60,
    )
    compiled = _single_note_report(261.63)

    calls: list[str] = []

    class _FakeSerialClient:
        def __init__(self, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def hello(self) -> dict[str, int]:
            calls.append("hello")
            return {
                "queue_capacity": 128,
                "scheduler_tick_us": 10,
                "motor_count": 8,
                "playback_motor_count": 6,
                "protocol_version": 2,
                "feature_flags": _CONTINUOUS_FEATURES,
            }

        def setup(self, **_kwargs) -> None:
            calls.append("setup")

        def stream_song_and_play(self, *_args, **_kwargs) -> None:
            calls.append("stream_song_and_play")

    parser = cli.build_parser()
    args = parser.parse_args(
        [
            "run",
            str(midi_path),
            "--config",
            str(cfg_path),
        ]
    )

    captured: list[str] = []
    monkeypatch.setattr(cli, "_out", lambda text="", end="\n": captured.append(text))
    monkeypatch.setattr(cli, "SerialClient", _FakeSerialClient)
    monkeypatch.setattr(cli, "_analyze_and_compile", lambda _cfg, _path, **_kw: (analysis, compiled, 1.0, None))
    monkeypatch.setattr(
        cli,
        "_transpose_studio_prompt",
        lambda **kwargs: (True, kwargs["cfg"], kwargs["analysis"], kwargs["compiled"], kwargs["avg_active"]),
    )
    monkeypatch.setattr(cli, "_prompt_play", lambda _args: False)

    rc = cli.run_command(args)

    assert rc == 0
    assert "setup" in calls
    assert "stream_song_and_play" not in calls
    assert any("Canceled before playback start." in line for line in captured)


def test_run_command_counts_down_before_playback_start(monkeypatch, tmp_path: Path) -> None:
    midi_path = tmp_path / "song.mid"
    midi_path.write_bytes(b"MThd")
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text("", encoding="utf-8")

    analysis = MidiAnalysisReport(
        notes=[NoteEvent(0.0, 0.5, 60, 60, 261.63, 100, 0)],
        duration_s=0.5,
        note_count=1,
        max_polyphony=1,
        transpose_semitones=0,
        clamped_note_count=0,
        min_source_note=60,
        max_source_note=60,
    )
    compiled = _single_note_report(261.63)
    metrics = PlaybackMetrics(
        underrun_count=0,
        queue_high_water=16,
        scheduling_late_max_us=100,
        crc_parse_errors=0,
        queue_depth=0,
        credits=128,
        rx_parse_errors=0,
        timer_empty_events=0,
        timer_restart_count=0,
        event_groups_started=1,
        scheduler_guard_hits=0,
        control_late_max_us=50,
        control_overrun_count=0,
        launch_guard_count=0,
    )
    calls: list[str] = []

    class _FakeSerialClient:
        def __init__(self, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def hello(self) -> dict[str, int]:
            calls.append("hello")
            return {
                "queue_capacity": 128,
                "scheduler_tick_us": 10,
                "motor_count": 8,
                "playback_motor_count": 6,
                "protocol_version": 2,
                "feature_flags": _CONTINUOUS_FEATURES,
            }

        def setup(self, **_kwargs) -> None:
            calls.append("setup")

        def stream_song_and_play(self, *_args, **_kwargs) -> None:
            calls.append("stream_song_and_play")

        def stop(self) -> None:
            calls.append("stop")

        def metrics(self) -> PlaybackMetrics:
            calls.append("metrics")
            return metrics

    parser = cli.build_parser()
    args = parser.parse_args(
        [
            "run",
            str(midi_path),
            "--config",
            str(cfg_path),
        ]
    )

    monkeypatch.setattr(cli, "_out", lambda text="", end="\n": None)
    monkeypatch.setattr(cli, "SerialClient", _FakeSerialClient)
    monkeypatch.setattr(cli, "_analyze_and_compile", lambda _cfg, _path, **_kw: (analysis, compiled, 1.0, None))
    monkeypatch.setattr(
        cli,
        "_transpose_studio_prompt",
        lambda **kwargs: (True, kwargs["cfg"], kwargs["analysis"], kwargs["compiled"], kwargs["avg_active"]),
    )
    monkeypatch.setattr(cli, "_prompt_play", lambda _args: calls.append("prompt") or True)
    monkeypatch.setattr(
        cli,
        "_start_playback_countdown",
        lambda _args, *, seconds: calls.append(f"countdown:{seconds}"),
    )

    rc = cli.run_command(args)

    assert rc == 0
    assert calls.index("prompt") < calls.index("countdown:10") < calls.index("stream_song_and_play")


def test_run_command_uses_configured_startup_countdown(monkeypatch, tmp_path: Path) -> None:
    midi_path = tmp_path / "song.mid"
    midi_path.write_bytes(b"MThd")
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        """
[playback]
startup_countdown_s = 4
""".strip()
        + "\n",
        encoding="utf-8",
    )

    analysis = MidiAnalysisReport(
        notes=[NoteEvent(0.0, 0.5, 60, 60, 261.63, 100, 0)],
        duration_s=0.5,
        note_count=1,
        max_polyphony=1,
        transpose_semitones=0,
        clamped_note_count=0,
        min_source_note=60,
        max_source_note=60,
    )
    compiled = _single_note_report(261.63)
    metrics = PlaybackMetrics(
        underrun_count=0,
        queue_high_water=16,
        scheduling_late_max_us=0,
        crc_parse_errors=0,
        queue_depth=0,
        credits=128,
        rx_parse_errors=0,
        timer_empty_events=0,
        timer_restart_count=0,
        event_groups_started=0,
        scheduler_guard_hits=0,
        control_late_max_us=0,
        control_overrun_count=0,
        launch_guard_count=0,
    )
    countdown_calls: list[int] = []

    class _FakeSerialClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def hello(self) -> dict[str, int]:
            return {
                "queue_capacity": 128,
                "scheduler_tick_us": 10,
                "motor_count": 8,
                "playback_motor_count": 6,
                "protocol_version": 2,
                "feature_flags": _CONTINUOUS_FEATURES,
            }

        def setup(self, **_kwargs) -> None:
            pass

        def stream_song_and_play(self, *_args, **_kwargs) -> None:
            pass

        def stop(self) -> None:
            pass

        def metrics(self) -> PlaybackMetrics:
            return metrics

    parser = cli.build_parser()
    args = parser.parse_args(
        [
            "run",
            str(midi_path),
            "--config",
            str(cfg_path),
        ]
    )

    monkeypatch.setattr(cli, "_out", lambda text="", end="\n": None)
    monkeypatch.setattr(cli, "SerialClient", _FakeSerialClient)
    monkeypatch.setattr(cli, "_analyze_and_compile", lambda _cfg, _path, **_kw: (analysis, compiled, 1.0, None))
    monkeypatch.setattr(
        cli,
        "_transpose_studio_prompt",
        lambda **kwargs: (True, kwargs["cfg"], kwargs["analysis"], kwargs["compiled"], kwargs["avg_active"]),
    )
    monkeypatch.setattr(cli, "_prompt_play", lambda _args: True)
    monkeypatch.setattr(cli, "_start_playback_countdown", lambda _args, *, seconds: countdown_calls.append(seconds))

    rc = cli.run_command(args)

    assert rc == 0
    assert countdown_calls == [4]


def test_run_command_streams_warmups_before_song_and_relocks(monkeypatch, tmp_path: Path) -> None:
    midi_path = tmp_path / "song.mid"
    midi_path.write_bytes(b"MThd")
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        """
[warmups]
sequence = ["slot_machine_lock_in", "phase_alignment"]
""".strip()
        + "\n",
        encoding="utf-8",
    )

    analysis = MidiAnalysisReport(
        notes=[NoteEvent(0.0, 0.5, 60, 60, 261.63, 100, 0)],
        duration_s=0.5,
        note_count=1,
        max_polyphony=1,
        transpose_semitones=0,
        clamped_note_count=0,
        min_source_note=60,
        max_source_note=60,
    )
    song_segment = Segment(duration_us=500_000, motor_freq_hz=(261.63, 0.0, 0.0, 0.0, 0.0, 0.0))
    song_event_group = _event_group(500_000, 261.63)
    compiled = CompileReport(
        segments=[song_segment],
        event_groups=[song_event_group],
        assignments=[0],
        duplicated_slots=0,
    )

    streamed_event_groups: list[list[PlaybackEventGroup]] = []
    warmup_calls: list[object] = []
    step_motion_calls: list[object] = []
    calls: list[str] = []

    class _FakeSerialClient:
        def __init__(self, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def hello(self) -> dict[str, int]:
            calls.append("hello")
            return {
                "protocol_version": 2,
                "queue_capacity": 128,
                "scheduler_tick_us": 10,
                "motor_count": 8,
                "playback_motor_count": 6,
                "feature_flags": _CONTINUOUS_FEATURES | cli.FEATURE_FLAG_STEP_MOTION | cli.FEATURE_FLAG_WARMUP | cli.FEATURE_FLAG_HOME,
                "exact_motion_flags": cli.EXACT_MOTION_FEATURE_DIRECTIONAL_STEP_MOTION,
            }

        def setup(self, **_kwargs) -> None:
            calls.append("setup")
            return None

        def stream_song_and_play(self, segments, *_args, **_kwargs) -> None:
            calls.append("stream_song_and_play")
            streamed_event_groups.append(list(segments))

        def warmup(self, motor_params, **_kwargs) -> None:
            calls.append("warmup")
            warmup_calls.append(motor_params)

        def step_motion(self, motor_params, **_kwargs) -> None:
            calls.append("step_motion")
            step_motion_calls.append(motor_params)

        def stop(self) -> None:
            calls.append("stop")
            return None

        def home(self, **_kwargs) -> None:
            calls.append("home")
            return None

        def metrics(self) -> PlaybackMetrics:
            return PlaybackMetrics(
                underrun_count=0,
                queue_high_water=16,
                scheduling_late_max_us=100,
                crc_parse_errors=0,
                queue_depth=0,
                credits=128,
                rx_parse_errors=0,
                timer_empty_events=0,
                timer_restart_count=0,
                event_groups_started=len(streamed_event_groups[0]) if streamed_event_groups else 0,
                scheduler_guard_hits=0,
                control_late_max_us=50,
                control_overrun_count=0,
                launch_guard_count=0,
            )

    parser = cli.build_parser()
    args = parser.parse_args([
        "run",
        str(midi_path),
        "--config",
        str(cfg_path),
        "--yes",
    ])

    monkeypatch.setattr(cli, "_out", lambda text="", end="\n": None)
    monkeypatch.setattr(cli, "SerialClient", _FakeSerialClient)
    monkeypatch.setattr(cli, "_analyze_and_compile", lambda _cfg, _path, **_kw: (analysis, compiled, 1.0, None))

    rc = cli.run_command(args)

    assert rc == 0
    assert calls.count("step_motion") == 2
    assert calls.count("warmup") == 0
    assert calls.count("stream_song_and_play") == 1
    assert streamed_event_groups == [[song_event_group]]
    assert len(step_motion_calls) == 2
    home_indexes = [idx for idx, name in enumerate(calls) if name == "home"]
    assert len(home_indexes) >= 2
    assert home_indexes[0] < calls.index("stream_song_and_play")
    assert home_indexes[-1] > calls.index("stream_song_and_play")


def test_run_command_streams_chord_bloom_via_step_motion_runtime(
    monkeypatch,
    tmp_path: Path,
) -> None:
    midi_path = tmp_path / "song.mid"
    midi_path.write_bytes(b"MThd")
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        """
[warmups]
sequence = ["chord_bloom"]
""".strip()
        + "\n",
        encoding="utf-8",
    )

    analysis = MidiAnalysisReport(
        notes=[NoteEvent(0.0, 0.5, 60, 60, 261.63, 100, 0)],
        duration_s=0.5,
        note_count=1,
        max_polyphony=1,
        transpose_semitones=0,
        clamped_note_count=0,
        min_source_note=60,
        max_source_note=60,
    )
    song_event_group = _event_group(500_000, 261.63)
    compiled = CompileReport(
        segments=[Segment(duration_us=500_000, motor_freq_hz=(261.63, 0.0, 0.0, 0.0, 0.0, 0.0))],
        event_groups=[song_event_group],
        assignments=[0],
        duplicated_slots=0,
    )

    calls: list[str] = []
    warmup_calls: list[object] = []
    step_motion_calls: list[object] = []
    streamed_event_groups: list[list[PlaybackEventGroup]] = []

    class _FakeSerialClient:
        def __init__(self, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def hello(self) -> dict[str, int]:
            calls.append("hello")
            return {
                "protocol_version": 2,
                "queue_capacity": 128,
                "scheduler_tick_us": 10,
                "motor_count": 8,
                "playback_motor_count": 6,
                "feature_flags": _CONTINUOUS_FEATURES | cli.FEATURE_FLAG_STEP_MOTION | cli.FEATURE_FLAG_WARMUP | cli.FEATURE_FLAG_HOME,
                "exact_motion_flags": cli.EXACT_MOTION_FEATURE_DIRECTIONAL_STEP_MOTION,
            }

        def setup(self, **_kwargs) -> None:
            calls.append("setup")

        def stream_song_and_play(self, segments, *_args, **_kwargs) -> None:
            calls.append("stream_song_and_play")
            streamed_event_groups.append(list(segments))

        def warmup(self, motor_params, **_kwargs) -> None:
            calls.append("warmup")
            warmup_calls.append(motor_params)

        def step_motion(self, motor_params, **_kwargs) -> None:
            calls.append("step_motion")
            step_motion_calls.append(motor_params)

        def stop(self) -> None:
            calls.append("stop")

        def home(self, **_kwargs) -> None:
            calls.append("home")

        def metrics(self) -> PlaybackMetrics:
            started = len(streamed_event_groups[-1]) if streamed_event_groups else 0
            return PlaybackMetrics(
                underrun_count=0,
                queue_high_water=16,
                scheduling_late_max_us=100,
                crc_parse_errors=0,
                queue_depth=0,
                credits=128,
                rx_parse_errors=0,
                timer_empty_events=0,
                timer_restart_count=0,
                event_groups_started=started,
                scheduler_guard_hits=0,
                control_late_max_us=50,
                control_overrun_count=0,
                launch_guard_count=0,
            )

    parser = cli.build_parser()
    args = parser.parse_args([
        "run",
        str(midi_path),
        "--config",
        str(cfg_path),
        "--yes",
    ])

    monkeypatch.setattr(cli, "_out", lambda text="", end="\n": None)
    monkeypatch.setattr(cli, "SerialClient", _FakeSerialClient)
    monkeypatch.setattr(cli, "_analyze_and_compile", lambda _cfg, _path, **_kw: (analysis, compiled, 1.0, None))

    rc = cli.run_command(args)

    assert rc == 0
    assert calls.count("warmup") == 0
    assert calls.count("step_motion") == 1
    assert len(warmup_calls) == 0
    assert len(step_motion_calls) == 1
    assert calls.count("stream_song_and_play") == 1
    assert streamed_event_groups == [[song_event_group]]
    assert [params.start_delay_ms for params in step_motion_calls[0] if any(ph.peak_hz > 0.0 for ph in params.phases)] == [
        0,
        300,
        600,
        900,
        1200,
        1500,
    ]


def test_run_command_streams_warmup_when_legacy_warmup_is_unavailable(
    monkeypatch,
    tmp_path: Path,
) -> None:
    midi_path = tmp_path / "song.mid"
    midi_path.write_bytes(b"MThd")
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        """
[warmups]
sequence = ["slot_machine_lock_in"]
require_home_before_sequence = false
""".strip()
        + "\n",
        encoding="utf-8",
    )

    analysis = MidiAnalysisReport(
        notes=[NoteEvent(0.0, 0.5, 60, 60, 261.63, 100, 0)],
        duration_s=0.5,
        note_count=1,
        max_polyphony=1,
        transpose_semitones=0,
        clamped_note_count=0,
        min_source_note=60,
        max_source_note=60,
    )
    song_event_group = _event_group(500_000, 261.63)
    compiled = CompileReport(
        segments=[Segment(duration_us=500_000, motor_freq_hz=(261.63, 0.0, 0.0, 0.0, 0.0, 0.0))],
        event_groups=[song_event_group],
        assignments=[0],
        duplicated_slots=0,
    )

    calls: list[str] = []
    warmup_calls: list[object] = []
    step_motion_calls: list[object] = []
    streamed_event_groups: list[list[PlaybackEventGroup]] = []

    class _FakeSerialClient:
        def __init__(self, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def hello(self) -> dict[str, int]:
            calls.append("hello")
            return {
                "protocol_version": 2,
                "queue_capacity": 128,
                "scheduler_tick_us": 10,
                "motor_count": 8,
                "playback_motor_count": 6,
                "feature_flags": _CONTINUOUS_FEATURES | cli.FEATURE_FLAG_STEP_MOTION,
                "exact_motion_flags": cli.EXACT_MOTION_FEATURE_DIRECTIONAL_STEP_MOTION,
            }

        def setup(self, **_kwargs) -> None:
            calls.append("setup")

        def stream_song_and_play(self, segments, *_args, **_kwargs) -> None:
            calls.append("stream_song_and_play")
            streamed_event_groups.append(list(segments))

        def warmup(self, motor_params, **_kwargs) -> None:
            calls.append("warmup")
            warmup_calls.append(motor_params)

        def step_motion(self, motor_params, **_kwargs) -> None:
            calls.append("step_motion")
            step_motion_calls.append(motor_params)

        def stop(self) -> None:
            calls.append("stop")

        def metrics(self) -> PlaybackMetrics:
            return PlaybackMetrics(
                underrun_count=0,
                queue_high_water=16,
                scheduling_late_max_us=100,
                crc_parse_errors=0,
                queue_depth=0,
                credits=128,
                rx_parse_errors=0,
                timer_empty_events=0,
                timer_restart_count=0,
                event_groups_started=1,
                scheduler_guard_hits=0,
                control_late_max_us=50,
                control_overrun_count=0,
                launch_guard_count=0,
            )

    parser = cli.build_parser()
    args = parser.parse_args([
        "run",
        str(midi_path),
        "--config",
        str(cfg_path),
        "--yes",
    ])

    monkeypatch.setattr(cli, "_out", lambda text="", end="\n": None)
    monkeypatch.setattr(cli, "SerialClient", _FakeSerialClient)
    monkeypatch.setattr(cli, "_analyze_and_compile", lambda _cfg, _path, **_kw: (analysis, compiled, 1.0, None))

    rc = cli.run_command(args)

    assert rc == 0
    assert calls.count("step_motion") == 1
    assert calls.count("warmup") == 0
    assert calls.count("stream_song_and_play") == 1
    assert len(warmup_calls) == 0
    assert len(step_motion_calls) == 1
    assert streamed_event_groups == [[song_event_group]]

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from music2 import cli
from music2.models import PlaybackMetrics


def test_parser_supports_speech_commands() -> None:
    parser = cli.build_parser()
    preview = parser.parse_args(["speech-preview", "--text", "hello nate"])
    render = parser.parse_args(["speech-render-wav", "--text", "hello nate"])
    analyze = parser.parse_args(["speech-analyze", "--text", "hello nate"])
    run = parser.parse_args(["speech-run", "--text", "hello nate"])
    corpus = parser.parse_args(["speech-corpus"])

    assert preview.command == "speech-preview"
    assert render.command == "speech-render-wav"
    assert analyze.command == "speech-analyze"
    assert run.command == "speech-run"
    assert corpus.command == "speech-corpus"
    assert preview.engine is None


@dataclass
class _FakeResult:
    metrics: PlaybackMetrics
    capabilities: object
    auto_home_error: Exception | None = None


class _FakeSession:
    def __init__(self) -> None:
        self.capabilities = type("Caps", (), {"queue_capacity": 6, "feature_flags": 0, "home_supported": False})()
        self.validated = None
        self.setup_kwargs = None
        self.playback_plan = None

    def validate(self, *, connected_motors: int, requires_direction_flip: bool) -> None:
        self.validated = (connected_motors, requires_direction_flip)

    def setup(self, **kwargs) -> None:
        self.setup_kwargs = kwargs

    def execute_plan(self, **kwargs):
        self.playback_plan = kwargs["playback_plan"]
        return _FakeResult(
            metrics=PlaybackMetrics(
                underrun_count=0,
                queue_high_water=4,
                scheduling_late_max_us=0,
                crc_parse_errors=0,
                queue_depth=0,
                credits=6,
                rx_parse_errors=0,
                scheduler_guard_hits=0,
                engine_fault_count=0,
            ),
            capabilities=self.capabilities,
        )


class _FakeRunner:
    last_session: _FakeSession | None = None

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs

    def session(self):
        class _Ctx:
            def __enter__(self_nonlocal):
                _FakeRunner.last_session = _FakeSession()
                return _FakeRunner.last_session

            def __exit__(self_nonlocal, exc_type, exc, tb):
                return False

        return _Ctx()


def test_speech_run_command_streams_compiled_plan(monkeypatch) -> None:
    parser = cli.build_parser()
    args = parser.parse_args(["speech-run", "--text", "hello nate", "--yes"])

    monkeypatch.setattr(cli, "PlaybackRunner", _FakeRunner)
    monkeypatch.setattr(cli, "_prompt_play", lambda args: True)
    monkeypatch.setattr(cli, "_emit_speech_panel", lambda *args, **kwargs: None)

    rc = cli.speech_run_command(args)

    assert rc == 0
    assert _FakeRunner.last_session is not None
    assert _FakeRunner.last_session.validated == (6, False)
    assert _FakeRunner.last_session.setup_kwargs["motors"] == 6
    assert _FakeRunner.last_session.playback_plan.plan_id == "speech-acoustic-v2"


def test_speech_run_command_skips_warmups_even_if_configured(monkeypatch) -> None:
    parser = cli.build_parser()
    args = parser.parse_args(["speech-run", "--text", "hello nate", "--yes"])

    captured = {}

    class _WarmupCapturingSession(_FakeSession):
        def execute_plan(self, **kwargs):
            captured["warmup_step_motion_routines"] = kwargs["warmup_step_motion_routines"]
            return super().execute_plan(**kwargs)

    class _WarmupCapturingRunner(_FakeRunner):
        def session(self):
            class _Ctx:
                def __enter__(self_nonlocal):
                    _WarmupCapturingRunner.last_session = _WarmupCapturingSession()
                    return _WarmupCapturingRunner.last_session

                def __exit__(self_nonlocal, exc_type, exc, tb):
                    return False

            return _Ctx()

    monkeypatch.setattr(cli, "PlaybackRunner", _WarmupCapturingRunner)
    monkeypatch.setattr(cli, "_prompt_play", lambda args: True)
    monkeypatch.setattr(cli, "_emit_speech_panel", lambda *args, **kwargs: None)

    rc = cli.speech_run_command(args)

    assert rc == 0
    assert captured["warmup_step_motion_routines"] == []


def test_speech_run_command_enables_speech_assist_when_firmware_supports_it(monkeypatch) -> None:
    parser = cli.build_parser()
    args = parser.parse_args(["speech-run", "--text", "hello nate", "--yes"])

    class _SpeechAssistSession(_FakeSession):
        def __init__(self) -> None:
            super().__init__()
            self.capabilities = type("Caps", (), {"queue_capacity": 6, "feature_flags": cli.FEATURE_FLAG_SPEECH_ASSIST})()

    class _SpeechAssistRunner(_FakeRunner):
        def session(self):
            class _Ctx:
                def __enter__(self_nonlocal):
                    _SpeechAssistRunner.last_session = _SpeechAssistSession()
                    return _SpeechAssistRunner.last_session

                def __exit__(self_nonlocal, exc_type, exc, tb):
                    return False

            return _Ctx()

    monkeypatch.setattr(cli, "PlaybackRunner", _SpeechAssistRunner)
    monkeypatch.setattr(cli, "_prompt_play", lambda args: True)
    monkeypatch.setattr(cli, "_emit_speech_panel", lambda *args, **kwargs: None)

    rc = cli.speech_run_command(args)

    assert rc == 0
    assert _SpeechAssistRunner.last_session is not None
    assert _SpeechAssistRunner.last_session.setup_kwargs["speech_assist_control_interval_us"] is not None
    assert _SpeechAssistRunner.last_session.setup_kwargs["speech_assist_release_accel_hz_per_s"] is not None

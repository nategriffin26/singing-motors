from __future__ import annotations

from pathlib import Path

import pytest

from music2.transcribe import cli
from music2.transcribe.types import ConversionResult, ConversionStats


def test_cli_rejects_polyphony_above_six() -> None:
    parser = cli.build_parser()
    args = parser.parse_args(["song.mp3", "--max-polyphony", "7"])
    with pytest.raises(RuntimeError, match="cannot exceed 6"):
        cli.run_from_args(args)


def test_cli_builds_config_and_calls_pipeline(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: dict[str, object] = {}

    def _fake_convert(input_audio: str, *, output_dir: str, cache_dir: str, config: object) -> ConversionResult:
        calls["input_audio"] = input_audio
        calls["output_dir"] = output_dir
        calls["cache_dir"] = cache_dir
        calls["config"] = config
        motor = tmp_path / "song.motor6.mid"
        expressive = tmp_path / "song.expressive6.mid"
        motor.write_bytes(b"MThd")
        expressive.write_bytes(b"MThd")
        stats = ConversionStats(
            input_path=str(input_audio),
            motor_midi_path=str(motor),
            expressive_midi_path=str(expressive),
            report_path=None,
            max_polyphony_requested=6,
            max_polyphony_output=4,
            notes_music_candidates=10,
            notes_speech_candidates=5,
            notes_fused_before_cap=11,
            notes_after_cap=9,
            dropped_by_polyphony_cap=2,
            transcriber_backends=("basic_pitch",),
            warnings=(),
        )
        return ConversionResult(
            motor_midi_path=motor,
            expressive_midi_path=expressive,
            report_path=None,
            stats=stats,
        )

    monkeypatch.setattr(cli, "convert_mp3_to_dual_midi", _fake_convert)
    parser = cli.build_parser()
    args = parser.parse_args(
        [
            "song.mp3",
            "--out-dir",
            str(tmp_path),
            "--cache-dir",
            str(tmp_path / "cache"),
            "--max-polyphony",
            "6",
            "--no-report",
            "--json",
        ]
    )
    rc = cli.run_from_args(args)
    assert rc == 0
    assert calls["input_audio"] == "song.mp3"
    assert calls["output_dir"] == str(tmp_path)
    assert calls["cache_dir"] == str(tmp_path / "cache")
    cfg = calls["config"]
    assert getattr(cfg, "max_polyphony") == 6
    assert getattr(cfg, "write_report") is False


def test_cli_exposes_tuning_flags(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: dict[str, object] = {}

    def _fake_convert(input_audio: str, *, output_dir: str, cache_dir: str, config: object) -> ConversionResult:
        calls["config"] = config
        motor = tmp_path / "song.motor6.mid"
        expressive = tmp_path / "song.expressive6.mid"
        motor.write_bytes(b"MThd")
        expressive.write_bytes(b"MThd")
        stats = ConversionStats(
            input_path=str(input_audio),
            motor_midi_path=str(motor),
            expressive_midi_path=str(expressive),
            report_path=None,
            max_polyphony_requested=6,
            max_polyphony_output=4,
            notes_music_candidates=0,
            notes_speech_candidates=0,
            notes_fused_before_cap=0,
            notes_after_cap=0,
            dropped_by_polyphony_cap=0,
            transcriber_backends=(),
            warnings=(),
        )
        return ConversionResult(
            motor_midi_path=motor,
            expressive_midi_path=expressive,
            report_path=None,
            stats=stats,
        )

    monkeypatch.setattr(cli, "convert_mp3_to_dual_midi", _fake_convert)
    parser = cli.build_parser()
    args = parser.parse_args(
        [
            "song.mp3",
            "--speech-start-confidence",
            "0.40",
            "--speech-sustain-confidence",
            "0.25",
            "--speech-max-pitch-jump-semitones",
            "2.0",
            "--speech-median-filter-window",
            "7",
            "--beat-quantize-max-shift-s",
            "0.02",
            "--no-beat-quantize",
            "--no-velocity-compression",
        ]
    )
    rc = cli.run_from_args(args)
    assert rc == 0
    cfg = calls["config"]
    assert getattr(cfg, "speech_start_confidence") == 0.40
    assert getattr(cfg, "speech_sustain_confidence") == 0.25
    assert getattr(cfg, "speech_max_pitch_jump_semitones") == 2.0
    assert getattr(cfg, "speech_median_filter_window") == 7
    assert getattr(cfg, "beat_quantize_max_shift_s") == 0.02
    assert getattr(cfg, "quantize_to_beats") is False
    assert getattr(cfg, "velocity_compression") is False

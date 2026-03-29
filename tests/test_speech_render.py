from __future__ import annotations

import json
from pathlib import Path
import wave

from music2.render_wav import RenderWavOptions
from music2.speech_text import compile_utterance, load_speech_preset, render_speech_to_wav, utterance_from_text


def test_render_speech_to_wav_writes_artifacts(tmp_path: Path) -> None:
    preset = load_speech_preset("robot_clear")
    utterance = utterance_from_text(
        "hello nate",
        voice="en-us",
        backend="rules",
        word_gap_ms=preset.word_gap_ms,
        pause_ms=preset.pause_ms,
    )
    playback = compile_utterance(utterance, preset=preset)
    out_wav = tmp_path / "hello.speech.wav"

    result = render_speech_to_wav(
        playback=playback,
        out_wav=out_wav,
        options=RenderWavOptions(sample_rate=8_000, normalize=True, clamp_frequencies=True),
    )

    assert result.wav_path.exists()
    assert result.metadata_path.exists()
    with wave.open(str(result.wav_path), "rb") as handle:
        assert handle.getframerate() == 8_000
        assert handle.getnframes() > 0
    metadata = json.loads(result.metadata_path.read_text(encoding="utf-8"))
    assert metadata["source_text"] == "hello nate"
    assert metadata["compile"]["event_group_count"] == playback.report.event_group_count
    assert metadata["playback_program"]["mode_id"] == "speech-text"
    assert metadata["engine"] == "symbolic_v1"

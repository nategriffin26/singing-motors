from __future__ import annotations

from pathlib import Path

from music2.render_wav import RenderWavOptions
from music2.speech_text import compile_utterance, evaluate_render, load_speech_preset, render_speech_to_wav, utterance_from_text
from music2.speech_text.eval import RecognitionResult


def test_evaluate_render_scores_word_accuracy(tmp_path: Path) -> None:
    preset = load_speech_preset("robot_clear")
    utterance = utterance_from_text(
        "hello nate",
        voice="en-us",
        backend="rules",
        word_gap_ms=preset.word_gap_ms,
        pause_ms=preset.pause_ms,
    )
    playback = compile_utterance(utterance, preset=preset)
    render = render_speech_to_wav(
        playback=playback,
        out_wav=tmp_path / "hello.wav",
        options=RenderWavOptions(sample_rate=8_000, normalize=False, clamp_frequencies=True),
    )

    result = evaluate_render(
        playback=playback,
        render=render,
        recognizer=lambda path: RecognitionResult(
            text="hello nate",
            recognizer="fake",
            available=True,
        ),
    )

    assert result.available is True
    assert result.recognizer == "fake"
    assert result.word_accuracy == 1.0

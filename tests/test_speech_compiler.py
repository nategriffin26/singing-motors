from __future__ import annotations

from music2.speech_text import compile_utterance, load_speech_preset, utterance_from_text


def test_compile_utterance_produces_playback_plan() -> None:
    preset = load_speech_preset("command_voice")
    utterance = utterance_from_text(
        "please start now",
        voice="en-us",
        backend="rules",
        word_gap_ms=preset.word_gap_ms,
        pause_ms=preset.pause_ms,
    )
    playback = compile_utterance(utterance, preset=preset)

    assert playback.playback_plan.plan_id == "speech-text"
    assert playback.playback_program.mode_id == "speech-text"
    assert playback.report.event_group_count > 0
    assert playback.report.segment_count > 0
    assert len(playback.report.lane_retarget_count) == 6
    assert sum(playback.report.lane_retarget_count) > 0


def test_compile_utterance_is_deterministic() -> None:
    preset = load_speech_preset("robot_clear")
    utterance = utterance_from_text(
        "hello nate",
        voice="en-us",
        backend="rules",
        word_gap_ms=preset.word_gap_ms,
        pause_ms=preset.pause_ms,
    )
    first = compile_utterance(utterance, preset=preset)
    second = compile_utterance(utterance, preset=preset)

    assert [group.delta_us for group in first.event_groups] == [group.delta_us for group in second.event_groups]
    assert [group.changes for group in first.event_groups] == [group.changes for group in second.event_groups]


def test_compile_utterance_acoustic_v2_uses_distinct_mode_id() -> None:
    preset = load_speech_preset("robot_clear")
    utterance = utterance_from_text(
        "hello nate",
        voice="en-us",
        backend="rules",
        word_gap_ms=preset.word_gap_ms,
        pause_ms=preset.pause_ms,
    )

    playback = compile_utterance(utterance, preset=preset, engine="acoustic_v2")

    assert playback.engine_id == "acoustic_v2"
    assert playback.playback_plan.plan_id == "speech-acoustic-v2"
    assert playback.playback_program.mode_id == "speech-acoustic-v2"
    assert playback.report.engine_id == "acoustic_v2"
    assert playback.frames[0].formant_hz[0] >= 0.0

from __future__ import annotations

import math

from .phoneme_map import phoneme_feature
from .presets import SpeechPreset
from .types import SpeechFrame, SpeechUtterance


def build_speech_frames(utterance: SpeechUtterance, *, preset: SpeechPreset) -> tuple[SpeechFrame, ...]:
    if not utterance.phonemes:
        return ()

    total_duration_s = max(0.001, utterance.duration_s)
    frame_s = max(0.01, preset.frame_ms / 1000.0)
    frames: list[SpeechFrame] = []
    for phoneme in utterance.phonemes:
        feature = phoneme_feature(phoneme.symbol)
        count = max(1, int(round(phoneme.duration_s / frame_s)))
        actual_frame_s = phoneme.duration_s / count
        for idx in range(count):
            start_s = phoneme.start_s + (idx * actual_frame_s)
            progress = min(1.0, max(0.0, start_s / total_duration_s))
            pitch_curve = math.sin(progress * math.pi) * 0.45 + (1.0 - progress) * 0.25
            vibrato = math.sin((start_s + 0.011) * math.tau * max(0.5, preset.vibrato_hz)) * preset.vibrato_depth_hz
            stress_boost = 0.16 if phoneme.stress > 0 else 0.0
            emphasis = min(1.0, 0.25 + stress_boost + (0.18 if idx == 0 and phoneme.burst else 0.0))
            f0_hz = 0.0
            if feature.voiced and not feature.pause:
                f0_hz = max(
                    preset.lanes[0].min_hz,
                    preset.base_f0_hz + (pitch_curve * preset.pitch_span_hz) + vibrato + (phoneme.stress * 4.0),
                )
            noise_level = min(1.0, feature.noise_level + (0.18 if phoneme.burst and idx == 0 else 0.0))
            burst_level = feature.burst_level if idx == 0 else max(0.0, feature.burst_level * 0.55)
            frames.append(
                SpeechFrame(
                    start_s=start_s,
                    duration_s=actual_frame_s,
                    phoneme_symbol=phoneme.symbol,
                    voiced=feature.voiced,
                    vowel=feature.vowel,
                    stress=phoneme.stress,
                    f0_hz=f0_hz,
                    open_level=feature.open_level,
                    front_level=feature.front_level,
                    contrast_level=feature.contrast_level,
                    noise_level=noise_level,
                    burst_level=burst_level,
                    emphasis=emphasis,
                )
            )
    return tuple(frames)

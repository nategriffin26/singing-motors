from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path

from ..render_wav import (
    RenderWavOptions,
    _effective_segments,
    _estimate_peak,
    _iter_samples,
    _metadata_path,
    _write_wav,
)
from .types import SpeechPlaybackPlan, SpeechRenderResult


def render_speech_to_wav(
    *,
    playback: SpeechPlaybackPlan,
    out_wav: Path,
    options: RenderWavOptions,
) -> SpeechRenderResult:
    out_wav = out_wav.expanduser().resolve()
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    playback_duration_us = int(round(playback.report.duration_s * 1_000_000.0))
    effective_segments, command_summary = _effective_segments(
        playback.playback_plan,
        playback_duration_us=playback_duration_us,
        safe_max_freq_hz=max(max(target.lane_freqs_hz) for target in playback.targets if target.lane_freqs_hz),
        clamp_frequencies=True,
    )
    pre_gain_peak = 0.0
    gain = 1.0
    if options.normalize:
        pre_gain_peak = _estimate_peak(_iter_samples(effective_segments, options=options))
        if pre_gain_peak > 1e-9:
            gain = 0.98 / pre_gain_peak
    post_gain_peak, rms, sample_count = _write_wav(
        out_wav,
        _iter_samples(effective_segments, options=options),
        sample_rate=options.sample_rate,
        gain=gain,
    )
    if not options.normalize:
        pre_gain_peak = post_gain_peak
    metadata_path = _metadata_path(out_wav)
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_text": playback.utterance.source_text,
        "normalized_text": playback.utterance.normalized_text,
        "voice": playback.utterance.voice,
        "backend": playback.utterance.backend,
        "engine": playback.engine_id,
        "preset": playback.report.preset_id,
        "render_options": asdict(options),
        "compile": {
            "engine_id": playback.report.engine_id,
            "frame_count": playback.report.frame_count,
            "target_count": playback.report.target_count,
            "event_group_count": playback.report.event_group_count,
            "segment_count": playback.report.segment_count,
            "duration_s": playback.report.duration_s,
            "lane_active_ratio": list(playback.report.lane_active_ratio),
            "lane_retarget_count": list(playback.report.lane_retarget_count),
            "burst_count": playback.report.burst_count,
            "max_event_rate_hz": playback.report.max_event_rate_hz,
            "warnings": list(playback.report.warnings),
        },
        "utterance": {
            "phoneme_count": len(playback.utterance.phonemes),
            "phonemes": [
                {
                    "symbol": phoneme.symbol,
                    "start_s": phoneme.start_s,
                    "duration_s": phoneme.duration_s,
                    "stress": phoneme.stress,
                }
                for phoneme in playback.utterance.phonemes
            ],
        },
        "playback_program": {
            "mode_id": playback.playback_program.mode_id,
            "display_name": playback.playback_program.display_name,
            "section_count": len(playback.playback_program.sections),
            "duration_total_us": playback.playback_program.total_duration_us,
        },
        "frame_preview": [
            {
                "start_s": frame.start_s,
                "duration_s": frame.duration_s,
                "phoneme_symbol": frame.phoneme_symbol,
                "f0_hz": frame.f0_hz,
                "formant_hz": list(frame.formant_hz),
                "high_band_energy": frame.high_band_energy,
            }
            for frame in playback.frames[:24]
        ],
        "command_timeline": command_summary,
        "audio": {
            "sample_rate": options.sample_rate,
            "sample_count": sample_count,
            "duration_s": sample_count / float(options.sample_rate),
            "pre_gain_peak": pre_gain_peak,
            "gain": gain,
            "peak": post_gain_peak,
            "rms": rms,
        },
    }
    metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
    return SpeechRenderResult(
        wav_path=out_wav.expanduser().resolve(),
        metadata_path=metadata_path.expanduser().resolve(),
        duration_s=sample_count / float(options.sample_rate),
        sample_rate=options.sample_rate,
        peak=post_gain_peak,
        rms=rms,
        segment_count=len(effective_segments),
    )

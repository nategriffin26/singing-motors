from __future__ import annotations

from dataclasses import dataclass
import math

from ..models import PlaybackEventGroup, PlaybackMotorChange, Segment
from ..playback_program import PlaybackPlan, PlaybackProgram, ProgramSection
from .acoustic_frontend import build_acoustic_frames
from .phoneme_map import phoneme_feature
from .presets import SpeechPreset
from .prosody import build_speech_frames
from .types import SpeechCompileReport, SpeechEngineId, SpeechMotorTarget, SpeechPlaybackPlan, SpeechUtterance


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _lane_freq(spec_min: float, spec_max: float, normalized: float) -> float:
    if spec_max <= spec_min:
        return spec_min
    ratio = _clamp(normalized, 0.0, 1.0)
    return spec_min + ((spec_max - spec_min) * ratio)


def _smooth(previous: float, target: float, factor: float) -> float:
    factor = _clamp(factor, 0.0, 1.0)
    if previous <= 0.0 or target <= 0.0:
        return target
    return previous + ((target - previous) * factor)


def _frame_to_lanes(frame, preset: SpeechPreset, previous: tuple[float, ...]) -> tuple[float, ...]:
    feature = phoneme_feature(frame.phoneme_symbol)
    l1 = 0.0
    if frame.f0_hz > 0.0:
        l1 = _clamp(frame.f0_hz, preset.lanes[0].min_hz, preset.lanes[0].max_hz)
    l2 = 0.0 if feature.pause else _lane_freq(preset.lanes[1].min_hz, preset.lanes[1].max_hz, frame.open_level)
    l3 = 0.0 if feature.pause else _lane_freq(preset.lanes[2].min_hz, preset.lanes[2].max_hz, frame.front_level)
    contrast_mix = max(frame.contrast_level, (frame.front_level + (1.0 - frame.open_level)) * 0.5)
    l4 = 0.0 if feature.pause else _lane_freq(preset.lanes[3].min_hz, preset.lanes[3].max_hz, contrast_mix)
    burst_mix = max(frame.noise_level, frame.burst_level)
    l5 = 0.0 if burst_mix <= 0.02 else _lane_freq(preset.lanes[4].min_hz, preset.lanes[4].max_hz, burst_mix)
    emphasis_mix = max(frame.emphasis, frame.front_level * preset.emphasis_duplication)
    l6 = 0.0
    if not feature.pause:
        l6 = _lane_freq(preset.lanes[5].min_hz, preset.lanes[5].max_hz, emphasis_mix)
        if frame.voiced and l1 > 0.0:
            l6 = max(l6, _clamp(l1 * 1.35, preset.lanes[5].min_hz, preset.lanes[5].max_hz))

    current = [l1, l2, l3, l4, l5, l6]
    if feature.pause:
        return (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    out: list[float] = []
    for idx, target in enumerate(current):
        spec = preset.lanes[idx]
        smoothed = _smooth(previous[idx], target, spec.smoothing)
        if smoothed < spec.min_hz * 0.92 and target <= 0.0:
            smoothed = 0.0
        out.append(round(smoothed, 1) if smoothed > 0.0 else 0.0)
    return tuple(out)


def _build_targets(utterance: SpeechUtterance, *, preset: SpeechPreset) -> tuple[SpeechMotorTarget, ...]:
    frames = build_speech_frames(utterance, preset=preset)
    previous = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    targets: list[SpeechMotorTarget] = []
    for frame in frames:
        lane_freqs = _frame_to_lanes(frame, preset, previous)
        weights = tuple(1.0 if value > 0.0 else 0.0 for value in lane_freqs)
        targets.append(
            SpeechMotorTarget(
                start_s=frame.start_s,
                duration_s=frame.duration_s,
                lane_freqs_hz=lane_freqs,
                lane_weights=weights,
                phoneme_symbol=frame.phoneme_symbol,
            )
        )
        previous = lane_freqs
    return tuple(targets)


def _scale_band_to_lane(value_hz: float, src_min: float, src_max: float, lane_min: float, lane_max: float) -> float:
    if value_hz <= 0.0 or src_max <= src_min:
        return 0.0
    ratio = _clamp((value_hz - src_min) / (src_max - src_min), 0.0, 1.0)
    return lane_min + ((lane_max - lane_min) * ratio)


def _frame_to_acoustic_lanes(frame, preset: SpeechPreset, previous: tuple[float, ...]) -> tuple[float, ...]:
    l1 = 0.0
    if frame.voicing_gate > 0.16 and frame.f0_hz > 0.0:
        l1 = _clamp(frame.f0_hz, preset.lanes[0].min_hz, preset.lanes[0].max_hz)
    f1_hz, f2_hz, f3_hz = frame.formant_hz
    l2 = 0.0 if frame.energy <= 0.04 else _scale_band_to_lane(
        f1_hz, 180.0, 950.0, preset.lanes[1].min_hz, preset.lanes[1].max_hz
    )
    l3 = 0.0 if frame.energy <= 0.04 else _scale_band_to_lane(
        f2_hz, 700.0, 2600.0, preset.lanes[2].min_hz, preset.lanes[2].max_hz
    )
    l4 = 0.0 if frame.energy <= 0.04 else _scale_band_to_lane(
        f3_hz, 1500.0, 3800.0, preset.lanes[3].min_hz, preset.lanes[3].max_hz
    )
    l5 = 0.0
    if frame.high_band_energy > 0.08 or frame.burst_level > 0.08:
        noise_center_hz = frame.noise_center_hz if frame.noise_center_hz > 0.0 else 2400.0
        l5 = _scale_band_to_lane(noise_center_hz, 1800.0, 5000.0, preset.lanes[4].min_hz, preset.lanes[4].max_hz)
    l6 = 0.0
    if frame.energy > 0.06:
        reinforcement = max(l3, l4, _clamp(l1 * 1.42, preset.lanes[5].min_hz, preset.lanes[5].max_hz) if l1 > 0.0 else 0.0)
        if reinforcement > 0.0:
            l6 = _scale_band_to_lane(
                reinforcement,
                preset.lanes[2].min_hz,
                max(preset.lanes[5].max_hz, preset.lanes[3].max_hz),
                preset.lanes[5].min_hz,
                preset.lanes[5].max_hz,
            )
            l6 = max(l6, _lane_freq(preset.lanes[5].min_hz, preset.lanes[5].max_hz, frame.emphasis))

    current = [l1, l2, l3, l4, l5, l6]
    if frame.energy <= 0.02 and frame.high_band_energy <= 0.02:
        return (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    out: list[float] = []
    for idx, target in enumerate(current):
        spec = preset.lanes[idx]
        smoothed = _smooth(previous[idx], target, spec.smoothing)
        if smoothed < spec.min_hz * 0.92 and target <= 0.0:
            smoothed = 0.0
        out.append(round(smoothed, 1) if smoothed > 0.0 else 0.0)
    return tuple(out)


def _build_targets_from_frames(frames, *, preset: SpeechPreset, acoustic: bool) -> tuple[SpeechMotorTarget, ...]:
    previous = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    targets: list[SpeechMotorTarget] = []
    for frame in frames:
        if acoustic:
            lane_freqs = _frame_to_acoustic_lanes(frame, preset, previous)
            weights = tuple(
                round(max(0.0, min(1.0, frame.energy if idx < 4 else max(frame.high_band_energy, frame.emphasis))), 3)
                if value > 0.0
                else 0.0
                for idx, value in enumerate(lane_freqs)
            )
        else:
            lane_freqs = _frame_to_lanes(frame, preset, previous)
            weights = tuple(1.0 if value > 0.0 else 0.0 for value in lane_freqs)
        targets.append(
            SpeechMotorTarget(
                start_s=frame.start_s,
                duration_s=frame.duration_s,
                lane_freqs_hz=lane_freqs,
                lane_weights=weights,
                phoneme_symbol=frame.phoneme_symbol,
            )
        )
        previous = lane_freqs
    return tuple(targets)


def _targets_close(
    left: SpeechMotorTarget,
    right: SpeechMotorTarget,
    *,
    tolerance_hz: float,
) -> bool:
    if left.phoneme_symbol != right.phoneme_symbol:
        return False
    return all(abs(a - b) <= tolerance_hz for a, b in zip(left.lane_freqs_hz, right.lane_freqs_hz))


def _compact_targets(targets: tuple[SpeechMotorTarget, ...], *, tolerance_hz: float = 0.0) -> tuple[SpeechMotorTarget, ...]:
    if not targets:
        return ()
    compacted: list[SpeechMotorTarget] = [targets[0]]
    for target in targets[1:]:
        prev = compacted[-1]
        if _targets_close(prev, target, tolerance_hz=tolerance_hz):
            compacted[-1] = SpeechMotorTarget(
                start_s=prev.start_s,
                duration_s=prev.duration_s + target.duration_s,
                lane_freqs_hz=prev.lane_freqs_hz,
                lane_weights=prev.lane_weights,
                phoneme_symbol=prev.phoneme_symbol,
            )
            continue
        compacted.append(target)
    return tuple(compacted)


def _build_event_stream(
    targets: tuple[SpeechMotorTarget, ...],
) -> tuple[tuple[PlaybackEventGroup, ...], tuple[Segment, ...], tuple[int, ...]]:
    if not targets:
        raise RuntimeError("speech compile produced no targets")

    event_groups: list[PlaybackEventGroup] = []
    segments: list[Segment] = []
    lane_retarget_count = [0] * 6
    previous_freqs = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    last_boundary_us = 0

    for idx, target in enumerate(targets):
        boundary_us = int(round(target.start_s * 1_000_000.0))
        changes: list[PlaybackMotorChange] = []
        for lane_idx, freq_hz in enumerate(target.lane_freqs_hz):
            if not math.isclose(freq_hz, previous_freqs[lane_idx], abs_tol=0.05, rel_tol=1e-9):
                lane_retarget_count[lane_idx] += 1
                changes.append(PlaybackMotorChange(motor_idx=lane_idx, target_hz=freq_hz))
        if changes:
            event_groups.append(
                PlaybackEventGroup(
                    delta_us=max(0, boundary_us - last_boundary_us),
                    changes=tuple(changes),
                )
            )
            last_boundary_us = boundary_us
        duration_us = int(round(target.duration_s * 1_000_000.0))
        if duration_us > 0:
            segments.append(
                Segment(
                    duration_us=duration_us,
                    motor_freq_hz=target.lane_freqs_hz,
                )
            )
        previous_freqs = target.lane_freqs_hz
        if idx == len(targets) - 1 and any(value > 0.0 for value in previous_freqs):
            end_boundary_us = boundary_us + duration_us
            stop_changes = tuple(
                PlaybackMotorChange(motor_idx=lane_idx, target_hz=0.0)
                for lane_idx, value in enumerate(previous_freqs)
                if value > 0.0
            )
            event_groups.append(
                PlaybackEventGroup(
                    delta_us=max(0, end_boundary_us - last_boundary_us),
                    changes=stop_changes,
                )
            )
            last_boundary_us = end_boundary_us

    return tuple(event_groups), tuple(segments), tuple(lane_retarget_count)


def compile_utterance(
    utterance: SpeechUtterance,
    *,
    preset: SpeechPreset,
    engine: SpeechEngineId = "symbolic_v1",
) -> SpeechPlaybackPlan:
    if engine == "acoustic_v2":
        frames, acoustic_warnings = build_acoustic_frames(utterance, preset=preset)
        compacted_targets = _compact_targets(
            _build_targets_from_frames(frames, preset=preset, acoustic=True),
            tolerance_hz=5.0,
        )
    else:
        frames = build_speech_frames(utterance, preset=preset)
        acoustic_warnings = ()
        compacted_targets = _compact_targets(_build_targets_from_frames(frames, preset=preset, acoustic=False))
    event_groups, shadow_segments, lane_retarget_count = _build_event_stream(compacted_targets)
    duration_s = sum(target.duration_s for target in compacted_targets)
    lane_active_ratio = tuple(
        round(
            sum(target.duration_s for target in compacted_targets if target.lane_freqs_hz[idx] > 0.0) / max(duration_s, 1e-9),
            4,
        )
        for idx in range(6)
    )
    burst_count = sum(1 for target in compacted_targets if target.lane_freqs_hz[4] > 0.0)
    max_event_rate_hz = len(event_groups) / max(duration_s, 1e-9)
    warnings: list[str] = []
    if max_event_rate_hz > preset.safe_event_rate_hz:
        warnings.append(
            f"event rate {max_event_rate_hz:.1f} Hz exceeds preset safe envelope {preset.safe_event_rate_hz:.1f} Hz"
        )
    if engine == "acoustic_v2" and burst_count == 0:
        warnings.append("acoustic_v2 generated no high-band bursts; consider a sharper preset for consonants")

    playback_plan = PlaybackPlan(
        plan_id="speech-acoustic-v2" if engine == "acoustic_v2" else "speech-text",
        display_name=f"Speech · {utterance.normalized_text[:32]}",
        event_groups=event_groups,
        shadow_segments=shadow_segments,
        connected_motors=6,
        overflow_mode="steal_quietest",
        motor_change_count=sum(lane_retarget_count),
    )
    playback_program = PlaybackProgram(
        mode_id="speech-acoustic-v2" if engine == "acoustic_v2" else "speech-text",
        display_name="Speech acoustic v2" if engine == "acoustic_v2" else "Speech text",
        sections=(
            ProgramSection(
                section_id="speech-1",
                display_name="Speech phrase",
                playback_plan=playback_plan,
                metadata={
                    "engine_id": engine,
                    "token_count": len(utterance.tokens),
                    "phoneme_count": len(utterance.phonemes),
                },
            ),
        ),
    )
    report = SpeechCompileReport(
        utterance=utterance,
        engine_id=engine,
        frame_count=len(frames),
        target_count=len(compacted_targets),
        event_group_count=len(event_groups),
        segment_count=len(shadow_segments),
        duration_s=duration_s,
        preset_id=preset.preset_id,
        lane_active_ratio=lane_active_ratio,
        lane_retarget_count=lane_retarget_count,
        burst_count=burst_count,
        max_event_rate_hz=max_event_rate_hz,
        warnings=tuple((*utterance.warnings, *acoustic_warnings, *warnings)),
    )
    return SpeechPlaybackPlan(
        utterance=utterance,
        engine_id=engine,
        frames=frames,
        targets=compacted_targets,
        event_groups=event_groups,
        shadow_segments=shadow_segments,
        playback_plan=playback_plan,
        playback_program=playback_program,
        report=report,
    )

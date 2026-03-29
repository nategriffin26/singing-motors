from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PitchBendPoint:
    time_s: float
    semitones: float
    confidence: float = 1.0


@dataclass(frozen=True)
class CandidateNote:
    start_s: float
    end_s: float
    midi_note: int
    velocity: int = 96
    confidence: float = 1.0
    source: str = "unknown"
    bends: tuple[PitchBendPoint, ...] = ()

    @property
    def duration_s(self) -> float:
        return max(0.0, self.end_s - self.start_s)

    def clamped(self) -> "CandidateNote":
        start_s = max(0.0, float(self.start_s))
        end_s = max(start_s, float(self.end_s))
        midi_note = int(max(0, min(127, int(self.midi_note))))
        velocity = int(max(1, min(127, int(self.velocity))))
        confidence = max(0.0, min(1.0, float(self.confidence)))
        bends = tuple(
            PitchBendPoint(
                time_s=min(max(start_s, float(point.time_s)), end_s),
                semitones=float(point.semitones),
                confidence=max(0.0, min(1.0, float(point.confidence))),
            )
            for point in self.bends
        )
        return CandidateNote(
            start_s=start_s,
            end_s=end_s,
            midi_note=midi_note,
            velocity=velocity,
            confidence=confidence,
            source=str(self.source),
            bends=bends,
        )


@dataclass(frozen=True)
class ConversionConfig:
    mode: str = "music"
    max_polyphony: int = 6
    quality: str = "ultra"
    device: str = "auto"
    pitch_bend_range_semitones: float = 2.0
    seed: int = 1337
    use_demucs: bool = True
    mt3_command: str | None = None
    write_report: bool = True
    min_note_duration_s: float = 0.05
    min_confidence: float = 0.3
    quantize_to_beats: bool = True
    velocity_compression: bool = True
    beat_quantize_max_shift_s: float = 0.03
    speech_start_confidence: float = 0.35
    speech_sustain_confidence: float = 0.20
    speech_max_pitch_jump_semitones: float = 1.5
    speech_median_filter_window: int = 5

    def __post_init__(self) -> None:
        if self.mode not in {"music", "speech"}:
            raise ValueError("mode must be one of: music, speech")
        if self.max_polyphony < 1 or self.max_polyphony > 6:
            raise ValueError("max_polyphony must be in range [1, 6]")
        if self.quality not in {"ultra", "high", "balanced"}:
            raise ValueError("quality must be one of: ultra, high, balanced")
        if self.min_note_duration_s < 0:
            raise ValueError("min_note_duration_s must be >= 0")
        if self.min_confidence < 0 or self.min_confidence > 1:
            raise ValueError("min_confidence must be in range [0, 1]")
        if self.beat_quantize_max_shift_s < 0:
            raise ValueError("beat_quantize_max_shift_s must be >= 0")
        if self.speech_start_confidence < 0 or self.speech_start_confidence > 1:
            raise ValueError("speech_start_confidence must be in range [0, 1]")
        if self.speech_sustain_confidence < 0 or self.speech_sustain_confidence > 1:
            raise ValueError("speech_sustain_confidence must be in range [0, 1]")
        if self.speech_max_pitch_jump_semitones <= 0:
            raise ValueError("speech_max_pitch_jump_semitones must be > 0")
        if self.speech_median_filter_window < 1:
            raise ValueError("speech_median_filter_window must be >= 1")


@dataclass(frozen=True)
class ConversionStats:
    input_path: str
    motor_midi_path: str
    expressive_midi_path: str
    report_path: str | None
    max_polyphony_requested: int
    max_polyphony_output: int
    notes_music_candidates: int
    notes_speech_candidates: int
    notes_fused_before_cap: int
    notes_after_cap: int
    dropped_by_polyphony_cap: int
    transcriber_backends: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def to_json_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ConversionResult:
    motor_midi_path: Path
    expressive_midi_path: Path
    report_path: Path | None
    stats: ConversionStats

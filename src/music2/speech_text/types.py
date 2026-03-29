from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from ..models import PlaybackEventGroup, Segment
from ..playback_program import PlaybackPlan, PlaybackProgram

SpeechFrontendId = Literal["auto", "espeak", "rules"]
SpeechEngineId = Literal["symbolic_v1", "acoustic_v2"]


@dataclass(frozen=True)
class SpeechToken:
    text: str
    normalized: str
    kind: Literal["word", "punctuation", "pause"]
    index: int


@dataclass(frozen=True)
class SpeechPhoneme:
    symbol: str
    source_symbol: str
    word_index: int
    start_s: float
    duration_s: float
    stress: int = 0
    voiced: bool = False
    vowel: bool = False
    pause: bool = False
    burst: bool = False

    @property
    def end_s(self) -> float:
        return self.start_s + self.duration_s


@dataclass(frozen=True)
class SpeechSyllable:
    phoneme_indices: tuple[int, ...]
    start_s: float
    end_s: float
    stress: int = 0


@dataclass(frozen=True)
class SpeechUtterance:
    source_text: str
    normalized_text: str
    voice: str
    backend: SpeechFrontendId
    tokens: tuple[SpeechToken, ...]
    phonemes: tuple[SpeechPhoneme, ...]
    syllables: tuple[SpeechSyllable, ...]
    warnings: tuple[str, ...] = ()

    @property
    def duration_s(self) -> float:
        if not self.phonemes:
            return 0.0
        return self.phonemes[-1].end_s


@dataclass(frozen=True)
class SpeechFrame:
    start_s: float
    duration_s: float
    phoneme_symbol: str
    voiced: bool
    vowel: bool
    stress: int
    f0_hz: float
    open_level: float
    front_level: float
    contrast_level: float
    noise_level: float
    burst_level: float
    emphasis: float
    energy: float = 1.0
    periodicity: float = 0.0
    high_band_energy: float = 0.0
    formant_hz: tuple[float, float, float] = (0.0, 0.0, 0.0)
    noise_center_hz: float = 0.0
    voicing_gate: float = 0.0

    @property
    def end_s(self) -> float:
        return self.start_s + self.duration_s


@dataclass(frozen=True)
class SpeechMotorTarget:
    start_s: float
    duration_s: float
    lane_freqs_hz: tuple[float, ...]
    lane_weights: tuple[float, ...]
    phoneme_symbol: str

    @property
    def end_s(self) -> float:
        return self.start_s + self.duration_s


@dataclass(frozen=True)
class SpeechCompileReport:
    utterance: SpeechUtterance
    engine_id: SpeechEngineId
    frame_count: int
    target_count: int
    event_group_count: int
    segment_count: int
    duration_s: float
    preset_id: str
    lane_active_ratio: tuple[float, ...]
    lane_retarget_count: tuple[int, ...]
    burst_count: int
    max_event_rate_hz: float
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class SpeechPlaybackPlan:
    utterance: SpeechUtterance
    engine_id: SpeechEngineId
    frames: tuple[SpeechFrame, ...]
    targets: tuple[SpeechMotorTarget, ...]
    event_groups: tuple[PlaybackEventGroup, ...]
    shadow_segments: tuple[Segment, ...]
    playback_plan: PlaybackPlan
    playback_program: PlaybackProgram
    report: SpeechCompileReport


@dataclass(frozen=True)
class SpeechRenderResult:
    wav_path: Path
    metadata_path: Path
    duration_s: float
    sample_rate: int
    peak: float
    rms: float
    segment_count: int


@dataclass(frozen=True)
class SpeechEvaluationResult:
    target_text: str
    recognized_text: str
    recognizer: str
    available: bool
    word_error_count: int
    word_count: int
    word_accuracy: float
    lane_usage_summary: tuple[float, ...]
    lane_retarget_count: tuple[int, ...]
    max_event_rate_hz: float
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class SpeechCorpusEntry:
    phrase_id: str
    text: str
    tags: tuple[str, ...] = ()
    voice: str = "en-us"
    preset: str = "robot_clear"


@dataclass(frozen=True)
class SpeechCorpusEvaluation:
    entries: tuple[SpeechEvaluationResult, ...]
    available: bool
    recognizer: str
    average_word_accuracy: float
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class SpeechBatchArtifact:
    entry: SpeechCorpusEntry
    render: SpeechRenderResult
    compile_report: SpeechCompileReport
    evaluation: SpeechEvaluationResult | None = None


@dataclass(frozen=True)
class SpeechAnalyzeResult:
    playback: SpeechPlaybackPlan
    render: SpeechRenderResult | None = None
    evaluation: SpeechEvaluationResult | None = None
    extra_notes: tuple[str, ...] = field(default_factory=tuple)

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

SourceKind = Literal["midi", "score", "tab", "chords", "audio", "unknown"]
ArtifactKind = Literal["midi", "score", "audio", "unknown"]
CandidateStatus = Literal["ready", "warning", "error"]


@dataclass(frozen=True)
class SongQuery:
    title: str
    artist: str | None = None
    album: str | None = None
    preferred_source_kind: str = "auto"
    max_candidates: int = 10
    allow_audio_fallback: bool = True
    local_only: bool = False
    manual_urls: tuple[str, ...] = ()
    manual_paths: tuple[str, ...] = ()
    audio_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class SourceHit:
    adapter_key: str
    source_name: str
    source_kind: SourceKind
    title: str
    artist: str | None
    url: str
    download_url: str | None = None
    format_hint: str | None = None
    confidence: float = 0.5
    local_path: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CandidateArtifact:
    source_hit: SourceHit
    local_path: Path | None
    artifact_kind: ArtifactKind
    format_hint: str | None = None
    conversion_steps: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    acquired: bool = False


@dataclass(frozen=True)
class CandidateAnalysis:
    artifact_path: Path
    note_count: int
    max_polyphony: int
    transpose_semitones: int
    clamped_note_count: int
    allocation_dropped_note_count: int
    allocation_stolen_note_count: int
    allocation_truncated_note_count: int
    arrangement_dropped_note_count: int
    dropped_melody_note_count: int
    dropped_bass_note_count: int
    motor_comfort_violation_count: int
    weighted_musical_loss: float
    event_group_count: int
    avg_active_motors: float
    duration_s: float
    exported_motor_safe_midi: Path | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_json_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["artifact_path"] = str(self.artifact_path)
        if self.exported_motor_safe_midi is not None:
            payload["exported_motor_safe_midi"] = str(self.exported_motor_safe_midi)
        return payload


@dataclass(frozen=True)
class RankedCandidate:
    source_hit: SourceHit
    artifact: CandidateArtifact | None
    analysis: CandidateAnalysis | None
    score: float
    score_breakdown: dict[str, float]
    recommendation_reason: str
    status: CandidateStatus = "ready"
    warnings: tuple[str, ...] = ()

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "source_hit": asdict(self.source_hit),
            "artifact": {
                "local_path": str(self.artifact.local_path) if self.artifact and self.artifact.local_path else None,
                "artifact_kind": self.artifact.artifact_kind if self.artifact else None,
                "format_hint": self.artifact.format_hint if self.artifact else None,
                "conversion_steps": list(self.artifact.conversion_steps) if self.artifact else [],
                "warnings": list(self.artifact.warnings) if self.artifact else [],
                "acquired": bool(self.artifact.acquired) if self.artifact else False,
            },
            "analysis": self.analysis.to_json_dict() if self.analysis else None,
            "score": self.score,
            "score_breakdown": dict(self.score_breakdown),
            "recommendation_reason": self.recommendation_reason,
            "status": self.status,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class LookupResult:
    query: SongQuery
    candidates: tuple[RankedCandidate, ...]
    recommended_index: int | None = None
    output_dir: Path | None = None
    warnings: tuple[str, ...] = ()

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "query": asdict(self.query),
            "recommended_index": self.recommended_index,
            "output_dir": str(self.output_dir) if self.output_dir else None,
            "warnings": list(self.warnings),
            "candidates": [candidate.to_json_dict() for candidate in self.candidates],
        }

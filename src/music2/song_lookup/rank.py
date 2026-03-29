from __future__ import annotations

import math

from .normalize import title_artist_match_score
from .types import CandidateAnalysis, CandidateArtifact, RankedCandidate, SongQuery, SourceHit

_SOURCE_KIND_BASE = {
    "midi": 1.0,
    "score": 0.75,
    "tab": 0.55,
    "chords": 0.35,
    "audio": 0.2,
    "unknown": 0.15,
}

_ADAPTER_BONUS = {
    "local_corpus": 0.15,
    "manual": 0.1,
    "bitmidi": 0.05,
    "opensheets": 0.0,
}


def score_candidate(
    *,
    query: SongQuery,
    hit: SourceHit,
    artifact: CandidateArtifact | None,
    analysis: CandidateAnalysis | None,
) -> tuple[float, dict[str, float], str]:
    source_confidence = _SOURCE_KIND_BASE.get(hit.source_kind, 0.15) + _ADAPTER_BONUS.get(hit.adapter_key, 0.0)
    query_match = title_artist_match_score(
        query_title=query.title,
        query_artist=query.artist,
        candidate_title=hit.title,
        candidate_artist=hit.artist,
    )
    preferred_bonus = 0.2 if query.preferred_source_kind not in {"", "auto"} and hit.source_kind == query.preferred_source_kind else 0.0
    mismatch_penalty = -12.0 * max(0.0, 0.6 - query_match)
    if analysis is None:
        score = source_confidence + query_match * 0.8 + preferred_bonus + mismatch_penalty - 5.0
        return (
            score,
            {
                "source_confidence": source_confidence,
                "query_match": query_match,
                "preferred_bonus": preferred_bonus,
                "mismatch_penalty": mismatch_penalty,
            },
            "source found but not analyzable",
        )

    polyphony_over = max(0.0, float(analysis.max_polyphony - 6))
    score = (
        source_confidence
        + query_match * 4.0
        + preferred_bonus
        + mismatch_penalty
        - 2.0 * analysis.weighted_musical_loss
        - 0.8 * analysis.allocation_dropped_note_count
        - 0.25 * analysis.arrangement_dropped_note_count
        - 3.0 * analysis.dropped_melody_note_count
        - 1.5 * analysis.dropped_bass_note_count
        - 0.2 * analysis.motor_comfort_violation_count
        - 0.3 * polyphony_over
    )
    reason = (
        f"match {query_match:.2f}, polyphony {analysis.max_polyphony}, "
        f"dropped {analysis.allocation_dropped_note_count}, loss {analysis.weighted_musical_loss:.2f}"
    )
    return (
        score,
        {
            "source_confidence": round(source_confidence, 4),
            "query_match": round(query_match, 4),
            "preferred_bonus": round(preferred_bonus, 4),
            "mismatch_penalty": round(mismatch_penalty, 4),
            "weighted_musical_loss_penalty": round(-2.0 * analysis.weighted_musical_loss, 4),
            "allocation_drop_penalty": round(-0.8 * analysis.allocation_dropped_note_count, 4),
            "arrangement_drop_penalty": round(-0.25 * analysis.arrangement_dropped_note_count, 4),
            "melody_drop_penalty": round(-3.0 * analysis.dropped_melody_note_count, 4),
            "bass_drop_penalty": round(-1.5 * analysis.dropped_bass_note_count, 4),
            "comfort_penalty": round(-0.2 * analysis.motor_comfort_violation_count, 4),
            "polyphony_penalty": round(-0.3 * polyphony_over, 4),
        },
        reason,
    )


def order_ranked_candidates(candidates: list[RankedCandidate]) -> list[RankedCandidate]:
    return sorted(
        candidates,
        key=lambda candidate: (
            -candidate.score,
            math.inf if candidate.analysis is None else candidate.analysis.weighted_musical_loss,
            candidate.source_hit.title.lower(),
        ),
    )

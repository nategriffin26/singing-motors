from __future__ import annotations

import re
from difflib import SequenceMatcher


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    lowered = value.lower()
    lowered = lowered.replace("&", " and ")
    lowered = re.sub(r"[^a-z0-9]+", " ", lowered)
    return re.sub(r"\s+", " ", lowered).strip()


def token_set(value: str | None) -> set[str]:
    normalized = normalize_text(value)
    return {token for token in normalized.split(" ") if token}


def overlap_score(a: str | None, b: str | None) -> float:
    tokens_a = token_set(a)
    tokens_b = token_set(b)
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / max(1, len(tokens_a | tokens_b))


def similarity_score(a: str | None, b: str | None) -> float:
    norm_a = normalize_text(a)
    norm_b = normalize_text(b)
    if not norm_a or not norm_b:
        return 0.0
    return SequenceMatcher(a=norm_a, b=norm_b).ratio()


def title_artist_match_score(
    *,
    query_title: str,
    query_artist: str | None,
    candidate_title: str,
    candidate_artist: str | None,
) -> float:
    title_score = max(
        similarity_score(query_title, candidate_title),
        overlap_score(query_title, candidate_title),
    )
    if query_artist:
        artist_score = max(
            similarity_score(query_artist, candidate_artist),
            overlap_score(query_artist, candidate_artist),
        )
        return max(0.0, min(1.0, title_score * 0.8 + artist_score * 0.2))
    return max(0.0, min(1.0, title_score))

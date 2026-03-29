from __future__ import annotations

from pathlib import Path

from ..normalize import overlap_score, similarity_score, title_artist_match_score
from ..types import SongQuery, SourceHit


class LocalCorpusAdapter:
    key = "local_corpus"
    source_name = "Local Corpus"

    def __init__(self, *, roots: tuple[Path, ...]) -> None:
        self.roots = roots

    def search(self, query: SongQuery, *, max_results: int) -> list[SourceHit]:
        hits: list[tuple[float, SourceHit]] = []
        for root in self.roots:
            if not root.exists():
                continue
            for path in root.rglob("*"):
                if not path.is_file():
                    continue
                if path.suffix.lower() not in {".mid", ".midi", ".musicxml", ".xml", ".mxl", ".mscz", ".mscx", ".abc", ".ly", ".gp3", ".gp4", ".gp5", ".gpx"}:
                    continue
                title = path.stem.replace("_", " ").replace("-", " ")
                title_overlap = overlap_score(query.title, title)
                title_similarity = similarity_score(query.title, title)
                if title_overlap < 0.34 and title_similarity < 0.55:
                    continue
                match = title_artist_match_score(
                    query_title=query.title,
                    query_artist=query.artist,
                    candidate_title=title,
                    candidate_artist=None,
                )
                if match <= 0.25:
                    continue
                kind = "midi" if path.suffix.lower() in {".mid", ".midi"} else "score"
                hit = SourceHit(
                    adapter_key=self.key,
                    source_name=self.source_name,
                    source_kind=kind,
                    title=title,
                    artist=None,
                    url=str(path.resolve()),
                    local_path=str(path.resolve()),
                    format_hint=path.suffix.lower().lstrip("."),
                    confidence=min(1.0, 0.75 + match * 0.25),
                    metadata={"root": str(root.resolve())},
                )
                hits.append((match, hit))
        hits.sort(key=lambda item: (-item[0], item[1].title.lower(), item[1].url))
        return [hit for _, hit in hits[:max_results]]

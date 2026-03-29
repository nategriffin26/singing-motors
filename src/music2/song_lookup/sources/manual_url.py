from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from ..types import SongQuery, SourceHit


def _kind_from_suffix(suffix: str) -> tuple[str, str | None]:
    lowered = suffix.lower()
    if lowered in {".mid", ".midi"}:
        return "midi", "mid"
    if lowered in {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg"}:
        return "audio", lowered.lstrip(".")
    if lowered in {".musicxml", ".xml", ".mxl", ".mscz", ".mscx", ".abc", ".ly", ".gp3", ".gp4", ".gp5", ".gpx"}:
        return "score", lowered.lstrip(".")
    return "unknown", lowered.lstrip(".") if lowered else None


class ManualUrlAdapter:
    key = "manual"
    source_name = "Manual Input"

    def __init__(self, *, urls: tuple[str, ...] = (), paths: tuple[str, ...] = (), audio_paths: tuple[str, ...] = ()) -> None:
        self.urls = urls
        self.paths = paths
        self.audio_paths = audio_paths

    def search(self, query: SongQuery, *, max_results: int) -> list[SourceHit]:
        hits: list[SourceHit] = []
        for raw_url in self.urls:
            parsed = urlparse(raw_url)
            suffix = Path(parsed.path).suffix
            kind, fmt = _kind_from_suffix(suffix)
            title = Path(parsed.path).stem or query.title
            hits.append(
                SourceHit(
                    adapter_key=self.key,
                    source_name=self.source_name,
                    source_kind=kind,  # type: ignore[arg-type]
                    title=title.replace("-", " ").replace("_", " "),
                    artist=query.artist,
                    url=raw_url,
                    download_url=raw_url if kind in {"midi", "audio"} else None,
                    format_hint=fmt,
                    confidence=0.9,
                )
            )
        for raw_path in (*self.paths, *self.audio_paths):
            path = Path(raw_path).expanduser()
            kind, fmt = _kind_from_suffix(path.suffix)
            hits.append(
                SourceHit(
                    adapter_key=self.key,
                    source_name=self.source_name,
                    source_kind=kind,  # type: ignore[arg-type]
                    title=path.stem.replace("-", " ").replace("_", " "),
                    artist=query.artist,
                    url=str(path.resolve()),
                    local_path=str(path.resolve()),
                    format_hint=fmt,
                    confidence=0.95,
                )
            )
        return hits[:max_results]

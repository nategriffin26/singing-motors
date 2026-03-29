from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.parse import urlparse

from ..artifacts import ensure_dir, safe_slug


class SongLookupCache:
    def __init__(self, root: str | Path = ".cache/song_lookup") -> None:
        self.root = ensure_dir(root)
        self.downloads_dir = ensure_dir(self.root / "downloads")
        self.converted_dir = ensure_dir(self.root / "converted")
        self.reports_dir = ensure_dir(self.root / "reports")
        self.transcribe_dir = ensure_dir(self.root / "transcribe")

    def query_slug(self, title: str, artist: str | None = None) -> str:
        base = safe_slug(title)
        if artist:
            return f"{base}-{safe_slug(artist)}"
        return base

    def query_output_dir(self, title: str, artist: str | None = None) -> Path:
        return ensure_dir(self.root / "queries" / self.query_slug(title, artist))

    def _url_hash(self, url: str) -> str:
        return hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]

    def download_path(self, *, source_name: str, url: str, filename_hint: str | None = None) -> Path:
        parsed = urlparse(url)
        suffix = Path(parsed.path).suffix or Path(filename_hint or "").suffix or ".bin"
        stem = safe_slug(filename_hint or Path(parsed.path).stem or "artifact")
        return self.downloads_dir / f"{safe_slug(source_name)}-{stem}-{self._url_hash(url)}{suffix}"

    def converted_path(self, *, stem: str, suffix: str = ".mid") -> Path:
        return self.converted_dir / f"{safe_slug(stem)}{suffix}"

    def report_path(self, *, stem: str) -> Path:
        return self.reports_dir / f"{safe_slug(stem)}.json"

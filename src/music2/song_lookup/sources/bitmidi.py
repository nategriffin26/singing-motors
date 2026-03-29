from __future__ import annotations

import json
import re
from urllib.parse import quote_plus

from ..types import SongQuery, SourceHit
from .base import UrlFetcher, absolutize

_INIT_STORE_PATTERN = re.compile(r"window\.initStore\s*=\s*(\{.*?\})\s*</script>", re.S)


class BitMidiAdapter:
    key = "bitmidi"
    source_name = "BitMidi"
    base_url = "https://bitmidi.com"

    def __init__(self, *, fetcher: UrlFetcher | None = None) -> None:
        self.fetcher = fetcher or UrlFetcher()

    def _parse_init_store(self, html: str) -> dict:
        match = _INIT_STORE_PATTERN.search(html)
        if not match:
            raise ValueError("window.initStore JSON blob not found in BitMidi HTML")
        return json.loads(match.group(1))

    def search(self, query: SongQuery, *, max_results: int) -> list[SourceHit]:
        search_url = f"{self.base_url}/search?q={quote_plus(query.title)}"
        html = self.fetcher.fetch_text(search_url)
        store = self._parse_init_store(html)
        search_views = store.get("views", {}).get("search", {})
        data_midis = store.get("data", {}).get("midis", {})

        hits: list[SourceHit] = []
        for search_result in search_views.values():
            if not isinstance(search_result, dict):
                continue
            for slug in search_result.get("0", []):
                item = data_midis.get(slug)
                if not item:
                    continue
                hits.append(
                    SourceHit(
                        adapter_key=self.key,
                        source_name=self.source_name,
                        source_kind="midi",
                        title=str(item.get("name") or item.get("slug") or slug),
                        artist=None,
                        url=absolutize(self.base_url, str(item.get("url") or f"/{slug}")),
                        download_url=absolutize(self.base_url, str(item.get("downloadUrl") or "")),
                        format_hint="mid",
                        confidence=0.7,
                        metadata={
                            "slug": slug,
                            "plays": int(item.get("plays", 0) or 0),
                            "views": int(item.get("views", 0) or 0),
                        },
                    )
                )
        deduped: list[SourceHit] = []
        seen_urls: set[str] = set()
        for hit in hits:
            if hit.url in seen_urls:
                continue
            seen_urls.add(hit.url)
            deduped.append(hit)
        return deduped[:max_results]

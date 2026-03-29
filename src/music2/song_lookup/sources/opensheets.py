from __future__ import annotations

import re
from html import unescape
from urllib.parse import quote_plus

from ..types import SongQuery, SourceHit
from .base import UrlFetcher

_CARD_PATTERN = re.compile(
    r'<a href="(?P<url>https://opensheets\.org/sheet-music/[^"]+)">\s*'
    r'(?:<img[^>]+alt="[^"]*"[^>]*>\s*)?'
    r'</a>\s*<div class="card-body"[^>]*>\s*<a href="(?P=url)">\s*'
    r'<h5 class="card-title"[^>]*>(?P<title>[^<]+)</h5>',
    re.I | re.S,
)


class OpenSheetsAdapter:
    key = "opensheets"
    source_name = "OpenSheets"
    base_url = "https://opensheets.org"

    def __init__(self, *, fetcher: UrlFetcher | None = None) -> None:
        self.fetcher = fetcher or UrlFetcher()

    def search(self, query: SongQuery, *, max_results: int) -> list[SourceHit]:
        url = f"{self.base_url}/search?keyword={quote_plus(query.title)}"
        html = self.fetcher.fetch_text(url)
        hits: list[SourceHit] = []
        for match in _CARD_PATTERN.finditer(html):
            hits.append(
                SourceHit(
                    adapter_key=self.key,
                    source_name=self.source_name,
                    source_kind="score",
                    title=unescape(match.group("title").strip()),
                    artist=None,
                    url=match.group("url"),
                    format_hint="score-page",
                    confidence=0.55,
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

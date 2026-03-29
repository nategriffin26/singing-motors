from __future__ import annotations

import urllib.request
from dataclasses import dataclass
from urllib.parse import urljoin

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/123.0.0.0 Safari/537.36"
)


@dataclass(frozen=True)
class FetchResult:
    url: str
    content_type: str | None
    body: bytes

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", "replace")


class UrlFetcher:
    def __init__(self, *, user_agent: str = USER_AGENT, timeout_seconds: float = 20.0) -> None:
        self.user_agent = user_agent
        self.timeout_seconds = timeout_seconds

    def fetch(self, url: str) -> FetchResult:
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": self.user_agent,
                "Accept": "*/*",
            },
        )
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            return FetchResult(
                url=response.geturl(),
                content_type=response.headers.get("Content-Type"),
                body=response.read(),
            )

    def fetch_text(self, url: str) -> str:
        return self.fetch(url).text

    def fetch_bytes(self, url: str) -> bytes:
        return self.fetch(url).body


def absolutize(base_url: str, maybe_relative: str) -> str:
    return urljoin(base_url.rstrip("/") + "/", maybe_relative)

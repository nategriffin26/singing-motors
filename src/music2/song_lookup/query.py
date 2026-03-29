from __future__ import annotations

from pathlib import Path

from .sources import BitMidiAdapter, LocalCorpusAdapter, ManualUrlAdapter, OpenSheetsAdapter
from .sources.base import UrlFetcher
from .types import SongQuery


def build_default_adapters(
    *,
    repo_root: Path,
    query: SongQuery,
    fetcher: UrlFetcher | None = None,
):
    roots = (repo_root / "assets" / "midi", repo_root / "midi_candidates")
    adapters = [
        ManualUrlAdapter(urls=query.manual_urls, paths=query.manual_paths, audio_paths=query.audio_paths),
        LocalCorpusAdapter(roots=roots),
    ]
    if query.local_only:
        return adapters
    fetch = fetcher or UrlFetcher()
    adapters.extend(
        [
            BitMidiAdapter(fetcher=fetch),
            OpenSheetsAdapter(fetcher=fetch),
        ]
    )
    return adapters

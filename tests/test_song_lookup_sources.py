from __future__ import annotations

from pathlib import Path

from music2.song_lookup.sources.bitmidi import BitMidiAdapter
from music2.song_lookup.sources.local_corpus import LocalCorpusAdapter
from music2.song_lookup.sources.opensheets import OpenSheetsAdapter
from music2.song_lookup.types import SongQuery


class _TextFetcher:
    def __init__(self, html: str) -> None:
        self.html = html

    def fetch_text(self, url: str) -> str:
        return self.html


def test_local_corpus_adapter_finds_matching_midi(tmp_path: Path) -> None:
    corpus = tmp_path / "assets" / "midi"
    corpus.mkdir(parents=True)
    good = corpus / "Imperial March - John Williams.mid"
    other = corpus / "Some Other Song.mid"
    good.write_bytes(b"MThd")
    other.write_bytes(b"MThd")

    adapter = LocalCorpusAdapter(roots=(corpus,))
    hits = adapter.search(SongQuery(title="Imperial March", artist="John Williams"), max_results=5)

    assert hits
    assert Path(hits[0].local_path or "").name == good.name
    assert hits[0].source_kind == "midi"


def test_bitmidi_adapter_parses_init_store_results() -> None:
    html = """
    <script>
    window.initStore = {
      "data": {
        "midis": {
          "imperial-mid": {
            "id": 1,
            "slug": "imperial-mid",
            "name": "Imperial March",
            "url": "/imperial-mid",
            "downloadUrl": "/uploads/imperial.mid",
            "plays": 42,
            "views": 100
          }
        }
      },
      "views": {
        "search": {
          "imperial march": {
            "0": ["imperial-mid"],
            "total": 1,
            "pageTotal": 1
          }
        }
      }
    }
    </script>
    """
    adapter = BitMidiAdapter(fetcher=_TextFetcher(html))
    hits = adapter.search(SongQuery(title="Imperial March"), max_results=5)

    assert len(hits) == 1
    assert hits[0].title == "Imperial March"
    assert hits[0].download_url == "https://bitmidi.com/uploads/imperial.mid"


def test_opensheets_adapter_parses_search_cards() -> None:
    html = """
    <div class="card">
      <a href="https://opensheets.org/sheet-music/the-imperial-march/5715.html">
        <img src="thumb.png" alt="Click me to view sheet: The Imperial March" />
      </a>
      <div class="card-body">
        <a href="https://opensheets.org/sheet-music/the-imperial-march/5715.html">
          <h5 class="card-title">The Imperial March</h5>
        </a>
      </div>
    </div>
    """
    adapter = OpenSheetsAdapter(fetcher=_TextFetcher(html))
    hits = adapter.search(SongQuery(title="Imperial March"), max_results=5)

    assert len(hits) == 1
    assert hits[0].title == "The Imperial March"
    assert hits[0].source_kind == "score"

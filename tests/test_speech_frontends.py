from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from music2.speech_text.frontends import utterance_from_phonemes_file, utterance_from_text


def test_rules_frontend_uses_repo_lexicon() -> None:
    utterance = utterance_from_text("hello nate", voice="en-us", backend="rules")
    assert [phoneme.symbol for phoneme in utterance.phonemes] == ["HH", "AH", "L", "OW", "N", "EY", "T"]
    assert utterance.duration_s > 0.0


def test_espeak_frontend_parses_mnemonics(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("music2.speech_text.frontends.shutil.which", lambda name: "/usr/bin/espeak-ng")
    monkeypatch.setattr(
        "music2.speech_text.frontends.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(stdout="h @ l oU\n"),
    )
    utterance = utterance_from_text("hello", voice="en-us", backend="espeak")
    assert [phoneme.symbol for phoneme in utterance.phonemes] == ["HH", "AH", "L", "OW"]


def test_utterance_from_phonemes_file(tmp_path: Path) -> None:
    payload = tmp_path / "utterance.json"
    payload.write_text(
        """
        {
          "text": "ship it",
          "phonemes": [
            {"symbol": "SH", "duration_s": 0.08},
            {"symbol": "IH", "duration_s": 0.07},
            {"symbol": "P", "duration_s": 0.05}
          ]
        }
        """.strip(),
        encoding="utf-8",
    )
    utterance = utterance_from_phonemes_file(payload)
    assert utterance.normalized_text == "ship it"
    assert [phoneme.symbol for phoneme in utterance.phonemes] == ["SH", "IH", "P"]

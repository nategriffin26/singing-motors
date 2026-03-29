from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import shutil
import subprocess

from .phoneme_map import is_vowel_symbol, normalize_phoneme_symbol, phoneme_feature
from .types import SpeechFrontendId, SpeechPhoneme, SpeechSyllable, SpeechToken, SpeechUtterance

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_LEXICON_PATH = _REPO_ROOT / "assets" / "speech_lexicon" / "en_us.json"
_WORD_RE = re.compile(r"[A-Za-z']+|[.,!?;:]")


class SpeechFrontendError(RuntimeError):
    pass


@dataclass(frozen=True)
class _WordPhonemes:
    word: str
    phonemes: tuple[str, ...]


def espeak_available() -> bool:
    return bool(shutil.which("espeak-ng") or shutil.which("espeak"))


def load_lexicon(path: str | Path = _DEFAULT_LEXICON_PATH) -> dict[str, tuple[str, ...]]:
    lex_path = Path(path)
    if not lex_path.exists():
        return {}
    raw = json.loads(lex_path.read_text(encoding="utf-8"))
    return {
        str(key).lower(): tuple(str(item) for item in value)
        for key, value in raw.items()
    }


def tokenize_text(text: str) -> tuple[SpeechToken, ...]:
    tokens: list[SpeechToken] = []
    for idx, match in enumerate(_WORD_RE.finditer(text)):
        value = match.group(0)
        kind = "word" if any(ch.isalpha() for ch in value) else "punctuation"
        tokens.append(
            SpeechToken(
                text=value,
                normalized=value.lower(),
                kind=kind,
                index=idx,
            )
        )
    return tuple(tokens)


def _default_word_to_phonemes(word: str) -> tuple[str, ...]:
    cleaned = re.sub(r"[^a-z']", "", word.lower())
    if not cleaned:
        return ("PAUSE",)
    if len(cleaned) == 1:
        return {
            "a": ("AH",),
            "e": ("IY",),
            "i": ("AY",),
            "o": ("OW",),
            "u": ("Y", "UW"),
            "y": ("W", "AY"),
        }.get(cleaned, ("AH",))

    pairs = [
        ("tion", ("SH", "AH", "N")),
        ("ing", ("IH", "NG")),
        ("sh", ("SH",)),
        ("ch", ("CH",)),
        ("th", ("TH",)),
        ("ph", ("F",)),
        ("ng", ("NG",)),
        ("ee", ("IY",)),
        ("ea", ("IY",)),
        ("oo", ("UW",)),
        ("ow", ("OW",)),
        ("ou", ("AW",)),
        ("ay", ("EY",)),
        ("oy", ("OY",)),
    ]
    out: list[str] = []
    idx = 0
    while idx < len(cleaned):
        matched = False
        for pattern, repl in pairs:
            if cleaned.startswith(pattern, idx):
                out.extend(repl)
                idx += len(pattern)
                matched = True
                break
        if matched:
            continue
        char = cleaned[idx]
        next_char = cleaned[idx + 1] if idx + 1 < len(cleaned) else ""
        if char == "a":
            out.append("AE" if next_char not in {"r", "w"} else "AA")
        elif char == "b":
            out.append("B")
        elif char == "c":
            out.append("S" if next_char in {"e", "i", "y"} else "K")
        elif char == "d":
            out.append("D")
        elif char == "e":
            if idx == len(cleaned) - 1 and len(cleaned) > 2:
                idx += 1
                continue
            out.append("EH")
        elif char == "f":
            out.append("F")
        elif char == "g":
            out.append("JH" if next_char in {"e", "i", "y"} else "G")
        elif char == "h":
            out.append("HH")
        elif char == "i":
            out.append("IH")
        elif char == "j":
            out.append("JH")
        elif char == "k":
            out.append("K")
        elif char == "l":
            out.append("L")
        elif char == "m":
            out.append("M")
        elif char == "n":
            out.append("N")
        elif char == "o":
            out.append("OW")
        elif char == "p":
            out.append("P")
        elif char == "q":
            out.extend(("K", "W"))
        elif char == "r":
            out.append("R")
        elif char == "s":
            out.append("Z" if next_char in {"m", "n"} else "S")
        elif char == "t":
            out.append("T")
        elif char == "u":
            out.append("UW" if idx == 0 else "AH")
        elif char == "v":
            out.append("V")
        elif char == "w":
            out.append("W")
        elif char == "x":
            out.extend(("K", "S"))
        elif char == "y":
            out.append("Y")
        elif char == "z":
            out.append("Z")
        else:
            out.append("AH")
        idx += 1
    return tuple(out or ["AH"])


def _espeak_word_to_phonemes(word: str, voice: str) -> tuple[str, ...]:
    exe = shutil.which("espeak-ng") or shutil.which("espeak")
    if exe is None:
        raise SpeechFrontendError("espeak is not installed")
    command = [exe, "-q", "-x", "--sep= ", "-v", voice, word]
    result = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
    )
    raw_tokens = result.stdout.strip().split()
    parsed: list[str] = []
    for token in raw_tokens:
        normalized = normalize_phoneme_symbol(token)
        if normalized:
            parsed.append(normalized)
    if not parsed:
        raise SpeechFrontendError(f"espeak returned no phonemes for {word!r}")
    return tuple(parsed)


def _word_phonemes(
    word: str,
    *,
    backend: SpeechFrontendId,
    voice: str,
    lexicon: dict[str, tuple[str, ...]],
) -> _WordPhonemes:
    lookup_key = word.lower()
    if lookup_key in lexicon:
        return _WordPhonemes(word=word, phonemes=tuple(normalize_phoneme_symbol(item) for item in lexicon[lookup_key]))
    if backend == "espeak":
        return _WordPhonemes(word=word, phonemes=_espeak_word_to_phonemes(word, voice))
    if backend == "auto" and espeak_available():
        try:
            return _WordPhonemes(word=word, phonemes=_espeak_word_to_phonemes(word, voice))
        except Exception:
            pass
    return _WordPhonemes(word=word, phonemes=_default_word_to_phonemes(word))


def _phoneme_duration_s(symbol: str, *, stress: int, word_gap_s: float, pause_s: float) -> float:
    feature = phoneme_feature(symbol)
    if feature.pause:
        return pause_s
    duration_ms = feature.default_duration_ms
    if feature.vowel and stress > 0:
        duration_ms = int(round(duration_ms * 1.18))
    if feature.noise_level > 0.6:
        duration_ms = int(round(duration_ms * 1.08))
    return max(0.02, duration_ms / 1000.0) + word_gap_s


def _build_syllables(phonemes: list[SpeechPhoneme]) -> tuple[SpeechSyllable, ...]:
    syllables: list[SpeechSyllable] = []
    current: list[int] = []
    current_stress = 0
    for idx, phoneme in enumerate(phonemes):
        if phoneme.pause:
            if current:
                syllables.append(
                    SpeechSyllable(
                        phoneme_indices=tuple(current),
                        start_s=phonemes[current[0]].start_s,
                        end_s=phonemes[current[-1]].end_s,
                        stress=current_stress,
                    )
                )
            current = []
            current_stress = 0
            continue
        current.append(idx)
        current_stress = max(current_stress, phoneme.stress)
        if phoneme.vowel and len(current) > 1:
            syllables.append(
                SpeechSyllable(
                    phoneme_indices=tuple(current),
                    start_s=phonemes[current[0]].start_s,
                    end_s=phonemes[current[-1]].end_s,
                    stress=current_stress,
                )
            )
            current = []
            current_stress = 0
    if current:
        syllables.append(
            SpeechSyllable(
                phoneme_indices=tuple(current),
                start_s=phonemes[current[0]].start_s,
                end_s=phonemes[current[-1]].end_s,
                stress=current_stress,
            )
        )
    return tuple(syllables)


def utterance_from_text(
    text: str,
    *,
    voice: str = "en-us",
    backend: SpeechFrontendId = "auto",
    lexicon_path: str | Path = _DEFAULT_LEXICON_PATH,
    word_gap_ms: int = 20,
    pause_ms: int = 140,
) -> SpeechUtterance:
    normalized_text = " ".join(text.strip().split())
    if not normalized_text:
        raise SpeechFrontendError("text is empty")

    lexicon = load_lexicon(lexicon_path)
    tokens = tokenize_text(normalized_text)
    phonemes: list[SpeechPhoneme] = []
    elapsed_s = 0.0
    warnings: list[str] = []
    word_index = -1
    for token in tokens:
        if token.kind == "punctuation":
            phonemes.append(
                SpeechPhoneme(
                    symbol="PAUSE",
                    source_symbol=token.text,
                    word_index=max(0, word_index),
                    start_s=elapsed_s,
                    duration_s=max(0.03, pause_ms / 1000.0),
                    pause=True,
                )
            )
            elapsed_s = phonemes[-1].end_s
            continue

        word_index += 1
        word = _word_phonemes(token.normalized, backend=backend, voice=voice, lexicon=lexicon)
        word_gap_s = 0.0
        for phoneme_idx, symbol in enumerate(word.phonemes):
            canonical = normalize_phoneme_symbol(symbol)
            feature = phoneme_feature(canonical)
            stress = 1 if feature.vowel and phoneme_idx == max(0, len(word.phonemes) // 2) else 0
            duration_s = _phoneme_duration_s(
                canonical,
                stress=stress,
                word_gap_s=word_gap_s,
                pause_s=pause_ms / 1000.0,
            )
            built = SpeechPhoneme(
                symbol=canonical,
                source_symbol=symbol,
                word_index=word_index,
                start_s=elapsed_s,
                duration_s=duration_s,
                stress=stress,
                voiced=feature.voiced,
                vowel=feature.vowel,
                pause=feature.pause,
                burst=feature.burst_level > 0.5,
            )
            phonemes.append(built)
            elapsed_s = built.end_s
            word_gap_s = word_gap_ms / 1000.0 if phoneme_idx == len(word.phonemes) - 1 else 0.0
        if token.normalized not in lexicon and backend != "espeak" and len(token.normalized) > 7:
            warnings.append(f"heuristic phoneme guess for '{token.normalized}'")

    syllables = _build_syllables(phonemes)
    return SpeechUtterance(
        source_text=text,
        normalized_text=normalized_text.lower(),
        voice=voice,
        backend=backend,
        tokens=tokens,
        phonemes=tuple(phonemes),
        syllables=syllables,
        warnings=tuple(dict.fromkeys(warnings)),
    )


def utterance_from_phonemes_file(path: str | Path, *, voice: str = "en-us") -> SpeechUtterance:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    text = str(payload.get("text", ""))
    voice_name = str(payload.get("voice", voice))
    phoneme_items = payload.get("phonemes", [])
    phonemes: list[SpeechPhoneme] = []
    elapsed_s = 0.0
    for idx, item in enumerate(phoneme_items):
        symbol = normalize_phoneme_symbol(str(item["symbol"]))
        feature = phoneme_feature(symbol)
        duration_s = float(item.get("duration_s", feature.default_duration_ms / 1000.0))
        phonemes.append(
            SpeechPhoneme(
                symbol=symbol,
                source_symbol=str(item.get("source_symbol", symbol)),
                word_index=int(item.get("word_index", idx)),
                start_s=elapsed_s,
                duration_s=duration_s,
                stress=int(item.get("stress", 0)),
                voiced=feature.voiced,
                vowel=feature.vowel,
                pause=feature.pause,
                burst=feature.burst_level > 0.5,
            )
        )
        elapsed_s = phonemes[-1].end_s
    tokens = tokenize_text(text or " ".join(str(item.get("symbol", "")) for item in phoneme_items))
    return SpeechUtterance(
        source_text=text,
        normalized_text=(text or "").strip().lower(),
        voice=voice_name,
        backend="rules",
        tokens=tokens,
        phonemes=tuple(phonemes),
        syllables=_build_syllables(phonemes),
    )

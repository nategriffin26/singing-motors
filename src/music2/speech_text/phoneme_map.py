from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PhonemeFeature:
    symbol: str
    voiced: bool
    vowel: bool
    pause: bool
    open_level: float
    front_level: float
    contrast_level: float
    noise_level: float
    burst_level: float
    default_duration_ms: int


_FEATURES: dict[str, PhonemeFeature] = {
    "PAUSE": PhonemeFeature("PAUSE", False, False, True, 0.0, 0.0, 0.0, 0.0, 0.0, 120),
    "AH": PhonemeFeature("AH", True, True, False, 0.65, 0.45, 0.35, 0.0, 0.0, 90),
    "AE": PhonemeFeature("AE", True, True, False, 0.90, 0.65, 0.40, 0.0, 0.0, 95),
    "AA": PhonemeFeature("AA", True, True, False, 0.88, 0.30, 0.45, 0.0, 0.0, 100),
    "AO": PhonemeFeature("AO", True, True, False, 0.72, 0.24, 0.48, 0.0, 0.0, 100),
    "AW": PhonemeFeature("AW", True, True, False, 0.80, 0.55, 0.52, 0.0, 0.0, 120),
    "AY": PhonemeFeature("AY", True, True, False, 0.78, 0.85, 0.58, 0.0, 0.0, 120),
    "EH": PhonemeFeature("EH", True, True, False, 0.62, 0.78, 0.36, 0.0, 0.0, 85),
    "ER": PhonemeFeature("ER", True, True, False, 0.55, 0.52, 0.38, 0.0, 0.0, 90),
    "EY": PhonemeFeature("EY", True, True, False, 0.48, 0.88, 0.42, 0.0, 0.0, 105),
    "IH": PhonemeFeature("IH", True, True, False, 0.42, 0.82, 0.34, 0.0, 0.0, 75),
    "IY": PhonemeFeature("IY", True, True, False, 0.24, 0.96, 0.40, 0.0, 0.0, 90),
    "OW": PhonemeFeature("OW", True, True, False, 0.38, 0.26, 0.44, 0.0, 0.0, 100),
    "OY": PhonemeFeature("OY", True, True, False, 0.54, 0.60, 0.55, 0.0, 0.0, 115),
    "UH": PhonemeFeature("UH", True, True, False, 0.48, 0.34, 0.34, 0.0, 0.0, 80),
    "UW": PhonemeFeature("UW", True, True, False, 0.22, 0.18, 0.34, 0.0, 0.0, 95),
    "B": PhonemeFeature("B", True, False, False, 0.20, 0.20, 0.72, 0.18, 0.72, 55),
    "CH": PhonemeFeature("CH", False, False, False, 0.18, 0.78, 0.92, 0.88, 0.90, 75),
    "D": PhonemeFeature("D", True, False, False, 0.22, 0.64, 0.80, 0.22, 0.75, 50),
    "DH": PhonemeFeature("DH", True, False, False, 0.24, 0.72, 0.72, 0.34, 0.20, 65),
    "F": PhonemeFeature("F", False, False, False, 0.10, 0.72, 0.82, 0.88, 0.20, 70),
    "G": PhonemeFeature("G", True, False, False, 0.18, 0.25, 0.70, 0.15, 0.68, 55),
    "HH": PhonemeFeature("HH", False, False, False, 0.15, 0.52, 0.55, 0.58, 0.10, 60),
    "JH": PhonemeFeature("JH", True, False, False, 0.18, 0.74, 0.84, 0.72, 0.72, 75),
    "K": PhonemeFeature("K", False, False, False, 0.15, 0.24, 0.76, 0.36, 0.85, 55),
    "L": PhonemeFeature("L", True, False, False, 0.32, 0.82, 0.28, 0.0, 0.0, 65),
    "M": PhonemeFeature("M", True, False, False, 0.44, 0.30, 0.24, 0.0, 0.0, 75),
    "N": PhonemeFeature("N", True, False, False, 0.40, 0.72, 0.28, 0.0, 0.0, 70),
    "NG": PhonemeFeature("NG", True, False, False, 0.34, 0.24, 0.30, 0.0, 0.0, 80),
    "P": PhonemeFeature("P", False, False, False, 0.18, 0.18, 0.72, 0.30, 0.90, 55),
    "R": PhonemeFeature("R", True, False, False, 0.28, 0.62, 0.30, 0.0, 0.0, 70),
    "S": PhonemeFeature("S", False, False, False, 0.08, 0.92, 1.00, 1.00, 0.28, 85),
    "SH": PhonemeFeature("SH", False, False, False, 0.10, 0.82, 0.92, 0.92, 0.24, 90),
    "T": PhonemeFeature("T", False, False, False, 0.12, 0.72, 0.82, 0.26, 0.82, 45),
    "TH": PhonemeFeature("TH", False, False, False, 0.10, 0.70, 0.78, 0.84, 0.18, 70),
    "V": PhonemeFeature("V", True, False, False, 0.12, 0.72, 0.76, 0.78, 0.16, 70),
    "W": PhonemeFeature("W", True, False, False, 0.22, 0.14, 0.22, 0.0, 0.0, 65),
    "Y": PhonemeFeature("Y", True, False, False, 0.18, 0.92, 0.20, 0.0, 0.0, 60),
    "Z": PhonemeFeature("Z", True, False, False, 0.10, 0.88, 0.94, 0.88, 0.22, 80),
    "ZH": PhonemeFeature("ZH", True, False, False, 0.10, 0.80, 0.88, 0.82, 0.18, 85),
}

_ESPEAK_TO_CANONICAL = {
    "@": "AH",
    "3:": "ER",
    "aI": "AY",
    "aU": "AW",
    "eI": "EY",
    "oU": "OW",
    "OI": "OY",
    "i:": "IY",
    "u:": "UW",
    "tS": "CH",
    "dZ": "JH",
    "T": "TH",
    "D": "DH",
    "S": "SH",
    "Z": "ZH",
    "N": "NG",
    "j": "Y",
    "r": "R",
    "l": "L",
    "m": "M",
    "n": "N",
    "h": "HH",
    "w": "W",
    "b": "B",
    "d": "D",
    "f": "F",
    "g": "G",
    "k": "K",
    "p": "P",
    "s": "S",
    "t": "T",
    "v": "V",
    "z": "Z",
    "A:": "AA",
    "Q": "AA",
    "O:": "AO",
    "e": "EH",
    "I": "IH",
    "U": "UH",
    "V": "AH",
    "{": "AE",
}


def normalize_phoneme_symbol(symbol: str) -> str:
    cleaned = symbol.strip()
    if not cleaned:
        return "PAUSE"
    cleaned = cleaned.replace("ˈ", "").replace("ˌ", "").replace("'", "")
    cleaned = cleaned.rstrip("0123456789")
    if cleaned in _FEATURES:
        return cleaned
    if cleaned in _ESPEAK_TO_CANONICAL:
        return _ESPEAK_TO_CANONICAL[cleaned]
    upper = cleaned.upper()
    if upper in _FEATURES:
        return upper
    return "AH"


def phoneme_feature(symbol: str) -> PhonemeFeature:
    return _FEATURES[normalize_phoneme_symbol(symbol)]


def is_vowel_symbol(symbol: str) -> bool:
    return phoneme_feature(symbol).vowel

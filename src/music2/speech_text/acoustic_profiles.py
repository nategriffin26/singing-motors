from __future__ import annotations

from dataclasses import dataclass

from .phoneme_map import phoneme_feature


@dataclass(frozen=True)
class AcousticPhonemeProfile:
    f1_hz: float
    f2_hz: float
    f3_hz: float
    noise_center_hz: float
    energy: float
    periodicity: float
    high_band_energy: float


_PROFILES: dict[str, AcousticPhonemeProfile] = {
    "PAUSE": AcousticPhonemeProfile(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    "AH": AcousticPhonemeProfile(700.0, 1220.0, 2600.0, 1800.0, 0.92, 0.94, 0.02),
    "AE": AcousticPhonemeProfile(820.0, 1760.0, 2480.0, 2100.0, 0.96, 0.95, 0.03),
    "AA": AcousticPhonemeProfile(730.0, 1100.0, 2450.0, 1800.0, 0.95, 0.95, 0.02),
    "AO": AcousticPhonemeProfile(560.0, 930.0, 2420.0, 1700.0, 0.90, 0.92, 0.02),
    "AW": AcousticPhonemeProfile(680.0, 1320.0, 2520.0, 2100.0, 0.94, 0.94, 0.03),
    "AY": AcousticPhonemeProfile(640.0, 1700.0, 2550.0, 2200.0, 0.94, 0.94, 0.03),
    "EH": AcousticPhonemeProfile(530.0, 1840.0, 2480.0, 2200.0, 0.88, 0.94, 0.03),
    "ER": AcousticPhonemeProfile(500.0, 1350.0, 1700.0, 1700.0, 0.84, 0.93, 0.02),
    "EY": AcousticPhonemeProfile(420.0, 2080.0, 2650.0, 2300.0, 0.88, 0.94, 0.03),
    "IH": AcousticPhonemeProfile(390.0, 1990.0, 2550.0, 2300.0, 0.82, 0.93, 0.03),
    "IY": AcousticPhonemeProfile(300.0, 2270.0, 2900.0, 2500.0, 0.82, 0.95, 0.04),
    "OW": AcousticPhonemeProfile(390.0, 860.0, 2440.0, 1750.0, 0.84, 0.95, 0.02),
    "OY": AcousticPhonemeProfile(480.0, 1160.0, 2550.0, 2100.0, 0.88, 0.94, 0.03),
    "UH": AcousticPhonemeProfile(440.0, 1020.0, 2250.0, 1700.0, 0.78, 0.92, 0.02),
    "UW": AcousticPhonemeProfile(320.0, 870.0, 2240.0, 1650.0, 0.80, 0.94, 0.02),
    "B": AcousticPhonemeProfile(300.0, 900.0, 2300.0, 1600.0, 0.62, 0.72, 0.12),
    "CH": AcousticPhonemeProfile(420.0, 1800.0, 3000.0, 3600.0, 0.52, 0.12, 0.92),
    "D": AcousticPhonemeProfile(350.0, 1650.0, 2550.0, 2000.0, 0.58, 0.68, 0.14),
    "DH": AcousticPhonemeProfile(420.0, 1450.0, 2400.0, 2800.0, 0.66, 0.76, 0.28),
    "F": AcousticPhonemeProfile(250.0, 1250.0, 2200.0, 3300.0, 0.42, 0.06, 0.96),
    "G": AcousticPhonemeProfile(300.0, 1100.0, 2400.0, 1700.0, 0.62, 0.74, 0.12),
    "HH": AcousticPhonemeProfile(380.0, 1450.0, 2400.0, 2400.0, 0.46, 0.08, 0.58),
    "JH": AcousticPhonemeProfile(420.0, 1750.0, 2850.0, 3400.0, 0.58, 0.34, 0.82),
    "K": AcousticPhonemeProfile(260.0, 1250.0, 2500.0, 2400.0, 0.48, 0.08, 0.44),
    "L": AcousticPhonemeProfile(360.0, 1500.0, 2600.0, 1800.0, 0.72, 0.84, 0.04),
    "M": AcousticPhonemeProfile(360.0, 1100.0, 2100.0, 1500.0, 0.74, 0.88, 0.02),
    "N": AcousticPhonemeProfile(320.0, 1650.0, 2500.0, 1700.0, 0.72, 0.86, 0.02),
    "NG": AcousticPhonemeProfile(290.0, 1450.0, 2400.0, 1500.0, 0.70, 0.86, 0.02),
    "P": AcousticPhonemeProfile(280.0, 800.0, 2200.0, 1800.0, 0.42, 0.04, 0.22),
    "R": AcousticPhonemeProfile(320.0, 1380.0, 1820.0, 1800.0, 0.68, 0.86, 0.03),
    "S": AcousticPhonemeProfile(250.0, 1900.0, 3100.0, 4200.0, 0.50, 0.08, 1.00),
    "SH": AcousticPhonemeProfile(260.0, 1650.0, 2850.0, 3000.0, 0.52, 0.08, 0.92),
    "T": AcousticPhonemeProfile(300.0, 1700.0, 2650.0, 2500.0, 0.40, 0.04, 0.26),
    "TH": AcousticPhonemeProfile(260.0, 1550.0, 2500.0, 2900.0, 0.44, 0.06, 0.82),
    "V": AcousticPhonemeProfile(260.0, 1300.0, 2300.0, 3000.0, 0.54, 0.42, 0.76),
    "W": AcousticPhonemeProfile(330.0, 900.0, 2200.0, 1600.0, 0.68, 0.86, 0.02),
    "Y": AcousticPhonemeProfile(300.0, 2100.0, 2750.0, 2200.0, 0.66, 0.88, 0.02),
    "Z": AcousticPhonemeProfile(250.0, 1900.0, 3100.0, 3900.0, 0.56, 0.40, 0.92),
    "ZH": AcousticPhonemeProfile(260.0, 1650.0, 2850.0, 2850.0, 0.56, 0.38, 0.86),
}


def acoustic_profile(symbol: str) -> AcousticPhonemeProfile:
    feature = phoneme_feature(symbol)
    profile = _PROFILES.get(feature.symbol)
    if profile is not None:
        return profile
    return AcousticPhonemeProfile(
        f1_hz=220.0 + (feature.open_level * 700.0),
        f2_hz=750.0 + (feature.front_level * 1600.0),
        f3_hz=1900.0 + (feature.contrast_level * 1100.0),
        noise_center_hz=1800.0 + (feature.noise_level * 2200.0),
        energy=0.72 if feature.voiced else 0.46,
        periodicity=0.88 if feature.voiced else 0.06,
        high_band_energy=feature.noise_level,
    )

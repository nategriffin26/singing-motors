from __future__ import annotations

from array import array
from io import BytesIO
import math
from pathlib import Path
import shutil
import subprocess
import tempfile
import wave

from .acoustic_profiles import acoustic_profile
from .phoneme_map import phoneme_feature
from .presets import SpeechPreset
from .types import SpeechFrame, SpeechUtterance


def _blend(a: float, b: float, mix: float) -> float:
    mix = max(0.0, min(1.0, mix))
    return a + ((b - a) * mix)


def _maybe_import_numpy():
    try:
        import numpy as np  # type: ignore
    except Exception:
        return None
    return np


def _iter_frame_layout(duration_s: float, frame_s: float) -> list[tuple[float, float]]:
    layouts: list[tuple[float, float]] = []
    start_s = 0.0
    while start_s < duration_s - 1e-9:
        seg_s = min(frame_s, duration_s - start_s)
        layouts.append((start_s, seg_s))
        start_s += seg_s
    if not layouts:
        layouts.append((0.0, frame_s))
    return layouts


def _phoneme_index_at_time(utterance: SpeechUtterance, center_s: float) -> int:
    for idx, phoneme in enumerate(utterance.phonemes):
        if phoneme.start_s <= center_s < phoneme.end_s:
            return idx
    return max(0, len(utterance.phonemes) - 1)


def _canonical_frame(
    utterance: SpeechUtterance,
    *,
    phoneme_idx: int,
    start_s: float,
    duration_s: float,
    preset: SpeechPreset,
) -> SpeechFrame:
    phoneme = utterance.phonemes[phoneme_idx]
    feature = phoneme_feature(phoneme.symbol)
    profile = acoustic_profile(phoneme.symbol)
    center_s = start_s + (duration_s * 0.5)
    total_duration_s = max(0.001, utterance.duration_s)
    progress = min(1.0, max(0.0, center_s / total_duration_s))
    local_progress = 0.0
    if phoneme.duration_s > 1e-9:
        local_progress = min(1.0, max(0.0, (center_s - phoneme.start_s) / phoneme.duration_s))

    f1_hz = profile.f1_hz
    f2_hz = profile.f2_hz
    f3_hz = profile.f3_hz
    noise_center_hz = profile.noise_center_hz

    transition_s = max(0.01, preset.acoustic_transition_ms / 1000.0)
    if phoneme_idx > 0:
        prev = utterance.phonemes[phoneme_idx - 1]
        if not prev.pause:
            prev_profile = acoustic_profile(prev.symbol)
            prev_mix = max(0.0, 1.0 - ((center_s - phoneme.start_s) / transition_s))
            if prev_mix > 0.0:
                f1_hz = _blend(prev_profile.f1_hz, f1_hz, 1.0 - (prev_mix * 0.55))
                f2_hz = _blend(prev_profile.f2_hz, f2_hz, 1.0 - (prev_mix * 0.60))
                f3_hz = _blend(prev_profile.f3_hz, f3_hz, 1.0 - (prev_mix * 0.50))
                noise_center_hz = _blend(prev_profile.noise_center_hz, noise_center_hz, 1.0 - (prev_mix * 0.45))
    if phoneme_idx + 1 < len(utterance.phonemes):
        nxt = utterance.phonemes[phoneme_idx + 1]
        if not nxt.pause:
            next_profile = acoustic_profile(nxt.symbol)
            next_mix = max(0.0, 1.0 - ((phoneme.end_s - center_s) / transition_s))
            if next_mix > 0.0:
                f1_hz = _blend(f1_hz, next_profile.f1_hz, next_mix * 0.58)
                f2_hz = _blend(f2_hz, next_profile.f2_hz, next_mix * 0.62)
                f3_hz = _blend(f3_hz, next_profile.f3_hz, next_mix * 0.50)
                noise_center_hz = _blend(noise_center_hz, next_profile.noise_center_hz, next_mix * 0.48)

    pitch_curve = math.sin(progress * math.pi) * 0.40 + ((1.0 - progress) * 0.22)
    phrase_declination = (0.5 - progress) * 0.18
    vibrato = math.sin((center_s + 0.009) * math.tau * max(0.5, preset.vibrato_hz)) * preset.vibrato_depth_hz
    f0_hz = 0.0
    if feature.voiced and not feature.pause:
        f0_hz = max(
            preset.lanes[0].min_hz,
            preset.base_f0_hz
            + (pitch_curve * preset.pitch_span_hz)
            + (phrase_declination * preset.pitch_span_hz)
            + vibrato
            + (phoneme.stress * 5.0),
        )

    burst_boost = 0.25 if phoneme.burst and local_progress < 0.3 else 0.0
    high_band_energy = min(1.0, profile.high_band_energy + burst_boost)
    energy = min(1.0, profile.energy + (0.12 if phoneme.stress > 0 else 0.0) + (burst_boost * 0.40))
    periodicity = max(0.0, min(1.0, profile.periodicity - (high_band_energy * 0.28)))
    voicing_gate = 0.0 if feature.pause else periodicity

    if feature.pause:
        return SpeechFrame(
            start_s=start_s,
            duration_s=duration_s,
            phoneme_symbol=phoneme.symbol,
            voiced=False,
            vowel=False,
            stress=phoneme.stress,
            f0_hz=0.0,
            open_level=0.0,
            front_level=0.0,
            contrast_level=0.0,
            noise_level=0.0,
            burst_level=0.0,
            emphasis=0.0,
            energy=0.0,
            periodicity=0.0,
            high_band_energy=0.0,
            formant_hz=(0.0, 0.0, 0.0),
            noise_center_hz=0.0,
            voicing_gate=0.0,
        )

    return SpeechFrame(
        start_s=start_s,
        duration_s=duration_s,
        phoneme_symbol=phoneme.symbol,
        voiced=feature.voiced,
        vowel=feature.vowel,
        stress=phoneme.stress,
        f0_hz=f0_hz,
        open_level=feature.open_level,
        front_level=feature.front_level,
        contrast_level=feature.contrast_level,
        noise_level=feature.noise_level,
        burst_level=feature.burst_level if phoneme.burst and local_progress < 0.35 else feature.burst_level * 0.35,
        emphasis=min(1.0, 0.32 + (0.18 if phoneme.stress > 0 else 0.0) + (burst_boost * 0.45)),
        energy=energy,
        periodicity=periodicity,
        high_band_energy=high_band_energy,
        formant_hz=(round(f1_hz, 1), round(f2_hz, 1), round(f3_hz, 1)),
        noise_center_hz=round(noise_center_hz, 1),
        voicing_gate=voicing_gate,
    )


def _load_oracle_waveform(text: str, voice: str) -> bytes | None:
    exe = shutil.which("espeak-ng") or shutil.which("espeak")
    if exe is None:
        return None
    result = subprocess.run(
        [exe, "-q", "--stdout", "-v", voice, text],
        check=True,
        capture_output=True,
    )
    stdout_bytes = bytes(result.stdout)
    if stdout_bytes:
        return stdout_bytes
    with tempfile.NamedTemporaryFile(prefix="music2-espeak-", suffix=".wav", delete=False) as handle:
        temp_path = Path(handle.name)
    try:
        subprocess.run(
            [exe, "-q", "-w", str(temp_path), "-v", voice, text],
            check=True,
            capture_output=True,
        )
        if not temp_path.exists():
            return None
        fallback_bytes = temp_path.read_bytes()
        return fallback_bytes if fallback_bytes else None
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except Exception:
            pass


def _estimate_f0_hz(np, frame, sample_rate: int, *, minimum_hz: float = 70.0, maximum_hz: float = 320.0) -> tuple[float, float]:
    if frame.size < 8:
        return 0.0, 0.0
    centered = frame - np.mean(frame)
    energy = float(np.sqrt(np.mean(centered * centered)))
    if energy < 0.01:
        return 0.0, 0.0
    corr = np.correlate(centered, centered, mode="full")[frame.size - 1 :]
    min_lag = max(1, int(sample_rate / maximum_hz))
    max_lag = min(corr.size - 1, int(sample_rate / minimum_hz))
    if max_lag <= min_lag:
        return 0.0, 0.0
    window = corr[min_lag : max_lag + 1]
    peak_idx = int(np.argmax(window))
    peak = float(window[peak_idx])
    zero = float(corr[0]) if corr[0] > 1e-9 else 1.0
    periodicity = max(0.0, min(1.0, peak / zero))
    if periodicity < 0.20:
        return 0.0, periodicity
    lag = min_lag + peak_idx
    return float(sample_rate / lag), periodicity


def _band_peak_hz(np, freqs, mags, low_hz: float, high_hz: float) -> float:
    mask = (freqs >= low_hz) & (freqs <= high_hz)
    if not mask.any():
        return 0.0
    band_freqs = freqs[mask]
    band_mags = mags[mask]
    peak_idx = int(np.argmax(band_mags))
    return float(band_freqs[peak_idx])


def _analyze_oracle_frames(wav_bytes: bytes, layouts: list[tuple[float, float]]) -> list[dict[str, float]] | None:
    np = _maybe_import_numpy()
    if np is None:
        return None
    with wave.open(BytesIO(wav_bytes), "rb") as handle:
        sample_rate = handle.getframerate()
        sample_width = handle.getsampwidth()
        channel_count = handle.getnchannels()
        raw = handle.readframes(handle.getnframes())
    if sample_width != 2:
        return None
    samples = np.frombuffer(raw, dtype="<i2").astype(np.float32)
    if channel_count > 1:
        samples = samples.reshape((-1, channel_count)).mean(axis=1)
    samples /= 32768.0
    frames: list[dict[str, float]] = []
    for start_s, duration_s in layouts:
        start = int(round(start_s * sample_rate))
        length = max(32, int(round(max(duration_s, 0.012) * sample_rate)))
        end = min(samples.shape[0], start + length)
        frame = samples[start:end]
        if frame.size < 32:
            frame = np.pad(frame, (0, max(0, 32 - frame.size)))
        window = np.hanning(frame.size)
        windowed = frame * window
        spectrum = np.abs(np.fft.rfft(windowed))
        freqs = np.fft.rfftfreq(windowed.size, 1.0 / sample_rate)
        total_mag = float(np.sum(spectrum)) or 1.0
        high_mask = freqs >= 2200.0
        high_band_energy = float(np.sum(spectrum[high_mask]) / total_mag) if high_mask.any() else 0.0
        if high_mask.any() and float(np.sum(spectrum[high_mask])) > 1e-9:
            noise_center_hz = float(np.average(freqs[high_mask], weights=spectrum[high_mask]))
        else:
            noise_center_hz = 0.0
        f0_hz, periodicity = _estimate_f0_hz(np, frame, sample_rate)
        frames.append(
            {
                "f0_hz": f0_hz,
                "periodicity": periodicity,
                "energy": float(np.sqrt(np.mean(frame * frame))),
                "f1_hz": _band_peak_hz(np, freqs, spectrum, 180.0, 1100.0),
                "f2_hz": _band_peak_hz(np, freqs, spectrum, 700.0, 2600.0),
                "f3_hz": _band_peak_hz(np, freqs, spectrum, 1500.0, 4200.0),
                "high_band_energy": high_band_energy,
                "noise_center_hz": noise_center_hz,
            }
        )
    return frames


def build_acoustic_frames(utterance: SpeechUtterance, *, preset: SpeechPreset) -> tuple[tuple[SpeechFrame, ...], tuple[str, ...]]:
    if not utterance.phonemes:
        return (), ()
    frame_s = max(0.01, preset.frame_ms / 1000.0)
    layouts = _iter_frame_layout(max(frame_s, utterance.duration_s), frame_s)
    canonical_frames = [
        _canonical_frame(
            utterance,
            phoneme_idx=_phoneme_index_at_time(utterance, start_s + (duration_s * 0.5)),
            start_s=start_s,
            duration_s=duration_s,
            preset=preset,
        )
        for start_s, duration_s in layouts
    ]

    warnings: list[str] = []
    should_try_oracle = utterance.backend != "rules"
    oracle_frames: list[dict[str, float]] | None = None
    if should_try_oracle:
        try:
            wav_bytes = _load_oracle_waveform(utterance.normalized_text, utterance.voice)
        except Exception:
            wav_bytes = None
            warnings.append("acoustic_v2 oracle synthesis failed; using canonical contours")
        if wav_bytes is None:
            warnings.append("acoustic_v2 using canonical contours because espeak oracle output is unavailable")
        else:
            try:
                oracle_frames = _analyze_oracle_frames(wav_bytes, layouts)
            except Exception:
                oracle_frames = None
                warnings.append("acoustic_v2 oracle analysis failed; using canonical contours")
            if oracle_frames is None:
                warnings.append("acoustic_v2 oracle analysis unavailable; using canonical contours")

    if oracle_frames is None:
        return tuple(canonical_frames), tuple(dict.fromkeys(warnings))

    blend = max(0.0, min(1.0, preset.acoustic_oracle_blend))
    frames: list[SpeechFrame] = []
    for idx, base in enumerate(canonical_frames):
        oracle = oracle_frames[idx]
        f1_hz = oracle["f1_hz"] if oracle["f1_hz"] > 0.0 else base.formant_hz[0]
        f2_hz = oracle["f2_hz"] if oracle["f2_hz"] > 0.0 else base.formant_hz[1]
        f3_hz = oracle["f3_hz"] if oracle["f3_hz"] > 0.0 else base.formant_hz[2]
        periodicity = _blend(base.periodicity, oracle["periodicity"], blend)
        high_band_energy = _blend(base.high_band_energy, min(1.0, oracle["high_band_energy"] * 2.2), blend)
        energy = _blend(base.energy, min(1.0, oracle["energy"] * 5.5), blend)
        f0_hz = base.f0_hz
        if base.voiced and oracle["f0_hz"] > 0.0:
            f0_hz = _blend(base.f0_hz or oracle["f0_hz"], oracle["f0_hz"], blend)
        frames.append(
            SpeechFrame(
                start_s=base.start_s,
                duration_s=base.duration_s,
                phoneme_symbol=base.phoneme_symbol,
                voiced=base.voiced,
                vowel=base.vowel,
                stress=base.stress,
                f0_hz=round(f0_hz, 1),
                open_level=base.open_level,
                front_level=base.front_level,
                contrast_level=base.contrast_level,
                noise_level=base.noise_level,
                burst_level=base.burst_level,
                emphasis=base.emphasis,
                energy=max(0.0, min(1.0, energy)),
                periodicity=max(0.0, min(1.0, periodicity)),
                high_band_energy=max(0.0, min(1.0, high_band_energy)),
                formant_hz=(
                    round(_blend(base.formant_hz[0], f1_hz, blend), 1),
                    round(_blend(base.formant_hz[1], f2_hz, blend), 1),
                    round(_blend(base.formant_hz[2], f3_hz, blend), 1),
                ),
                noise_center_hz=round(_blend(base.noise_center_hz, oracle["noise_center_hz"] or base.noise_center_hz, blend), 1),
                voicing_gate=max(0.0, min(1.0, _blend(base.voicing_gate, periodicity, blend))),
            )
        )
    return tuple(frames), tuple(dict.fromkeys(warnings))

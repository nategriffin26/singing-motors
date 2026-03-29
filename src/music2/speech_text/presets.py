from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tomllib


_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SPEECH_CONFIG_PATH = _REPO_ROOT / "config.speech.toml"
DEFAULT_PRESET_DIR = _REPO_ROOT / "speech_presets"
DEFAULT_CORPUS_PATH = _REPO_ROOT / "assets" / "speech_corpus" / "default.json"


@dataclass(frozen=True)
class LaneSpec:
    min_hz: float
    max_hz: float
    smoothing: float


@dataclass(frozen=True)
class SpeechPreset:
    preset_id: str
    display_name: str
    base_f0_hz: float
    pitch_span_hz: float
    frame_ms: int
    word_gap_ms: int
    pause_ms: int
    burst_ms: int
    safe_event_rate_hz: float
    vibrato_hz: float
    vibrato_depth_hz: float
    emphasis_duplication: float
    acoustic_oracle_blend: float
    acoustic_transition_ms: int
    acoustic_release_ms: int
    speech_assist_control_interval_us: int
    speech_assist_release_accel_hz_per_s: float
    lanes: tuple[LaneSpec, ...]


@dataclass(frozen=True)
class SpeechConfig:
    default_backend: str = "auto"
    default_voice: str = "en-us"
    default_preset: str = "robot_clear"
    default_engine: str = "acoustic_v2"
    frame_ms: int = 20
    safe_event_rate_hz: float = 45.0
    corpus_path: Path = DEFAULT_CORPUS_PATH


def load_speech_config(path: str | Path = DEFAULT_SPEECH_CONFIG_PATH) -> SpeechConfig:
    cfg_path = Path(path)
    if not cfg_path.exists():
        return SpeechConfig()
    with cfg_path.open("rb") as handle:
        raw = tomllib.load(handle)
    speech = raw.get("speech", {})
    eval_cfg = raw.get("evaluation", {})
    corpus_value = eval_cfg.get("corpus_path", str(DEFAULT_CORPUS_PATH))
    return SpeechConfig(
        default_backend=str(speech.get("default_backend", "auto")),
        default_voice=str(speech.get("default_voice", "en-us")),
        default_preset=str(speech.get("default_preset", "robot_clear")),
        default_engine=str(speech.get("default_engine", "acoustic_v2")),
        frame_ms=int(speech.get("frame_ms", 20)),
        safe_event_rate_hz=float(speech.get("safe_event_rate_hz", 45.0)),
        corpus_path=Path(corpus_value),
    )


def available_preset_ids(preset_dir: Path = DEFAULT_PRESET_DIR) -> tuple[str, ...]:
    if not preset_dir.exists():
        return ()
    return tuple(sorted(path.stem for path in preset_dir.glob("*.toml")))


def load_speech_preset(preset_id: str, preset_dir: Path = DEFAULT_PRESET_DIR) -> SpeechPreset:
    preset_path = preset_dir / f"{preset_id}.toml"
    if not preset_path.exists():
        raise FileNotFoundError(f"speech preset not found: {preset_path}")
    with preset_path.open("rb") as handle:
        raw = tomllib.load(handle)
    preset_raw = raw.get("preset", {})
    lanes_raw = raw.get("lanes", [])
    lanes = tuple(
        LaneSpec(
            min_hz=float(item["min_hz"]),
            max_hz=float(item["max_hz"]),
            smoothing=float(item.get("smoothing", 0.35)),
        )
        for item in lanes_raw
    )
    if len(lanes) != 6:
        raise ValueError(f"speech preset {preset_id} must define exactly 6 lanes")
    return SpeechPreset(
        preset_id=preset_id,
        display_name=str(preset_raw.get("display_name", preset_id.replace("_", " ").title())),
        base_f0_hz=float(preset_raw.get("base_f0_hz", 120.0)),
        pitch_span_hz=float(preset_raw.get("pitch_span_hz", 24.0)),
        frame_ms=int(preset_raw.get("frame_ms", 20)),
        word_gap_ms=int(preset_raw.get("word_gap_ms", 60)),
        pause_ms=int(preset_raw.get("pause_ms", 140)),
        burst_ms=int(preset_raw.get("burst_ms", 40)),
        safe_event_rate_hz=float(preset_raw.get("safe_event_rate_hz", 45.0)),
        vibrato_hz=float(preset_raw.get("vibrato_hz", 3.0)),
        vibrato_depth_hz=float(preset_raw.get("vibrato_depth_hz", 4.0)),
        emphasis_duplication=float(preset_raw.get("emphasis_duplication", 0.45)),
        acoustic_oracle_blend=float(preset_raw.get("acoustic_oracle_blend", 0.55)),
        acoustic_transition_ms=int(preset_raw.get("acoustic_transition_ms", 42)),
        acoustic_release_ms=int(preset_raw.get("acoustic_release_ms", 60)),
        speech_assist_control_interval_us=int(preset_raw.get("speech_assist_control_interval_us", 500)),
        speech_assist_release_accel_hz_per_s=float(
            preset_raw.get("speech_assist_release_accel_hz_per_s", 3200.0)
        ),
        lanes=lanes,
    )

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import cast
import tomllib

from .instrument_profile import DEFAULT_INSTRUMENT_PROFILE_PATH, resolve_instrument_profile_path
from .models import IdleMode, LookaheadStrategy, OverflowMode
from .viewer_color_mode import COLOR_MODE_IDS, ColorModeId, DEFAULT_COLOR_MODE, coerce_color_mode_id
from .viewer_theme import DEFAULT_THEME, ThemeId, coerce_theme_id
from .warmups import WARMUP_IDS

_log = logging.getLogger(__name__)

# Firmware safety ceiling for reliable pulse timing on ESP32.
# Staying under this value avoids overdriving the software pulse scheduler,
# which otherwise increases pulse edge drops and audible skipped steps.
_FIRMWARE_MAX_FREQ_HZ = 800.0

# Nate has validated reliable playback down to 15 Hz on the current hardware.
_RECOMMENDED_MIN_FREQ_HZ = 15.0


@dataclass(frozen=True)
class HostConfig:
    port: str = "/dev/cu.usbserial-0001"
    baudrate: int = 921600
    timeout_s: float = 0.20
    write_timeout_s: float = 0.20
    retries: int = 3

    connected_motors: int = 6
    instrument_profile_path: str = str(DEFAULT_INSTRUMENT_PROFILE_PATH)

    idle_mode: IdleMode = "duplicate"
    overflow_mode: OverflowMode = "steal_quietest"
    min_freq_hz: float = 30.0
    max_freq_hz: float = 800.0
    auto_transpose: bool = True
    auto_home: bool = True
    transpose_override: int | None = None
    sticky_gap_ms: int = 50
    double_melody: bool = True
    lookahead_ms: int = 1000
    lookahead_strategy: LookaheadStrategy = "p90"
    lookahead_min_ms: int = 250
    lookahead_percentile: int = 90
    lookahead_min_segments: int = 24
    home_steps_per_rev: int = 800
    home_hz: float = 160.0
    home_start_hz: float = 120.0
    home_accel_hz_per_s: float = 240.0
    pre_song_warmups: tuple[str, ...] = ()
    warmup_motor_order: tuple[int, ...] = ()
    warmup_speed_multipliers: tuple[tuple[str, float], ...] = ()
    warmup_max_accel_hz_per_s: float = 180.0
    warmup_require_home_before_sequence: bool = True
    startup_countdown_s: int = 10
    flip_direction_on_note_change: bool = False
    suppress_tight_direction_flips: bool = True
    direction_flip_safety_margin_ms: float = 50.0
    direction_flip_cooldown_ms: float = 150.0
    playback_run_accel_hz_per_s: float = 8000.0
    playback_launch_start_hz: float = 60.0
    playback_launch_accel_hz_per_s: float = 5000.0
    playback_launch_crossover_hz: float = 180.0
    scheduled_start_guard_ms: float = 150.0

    ui_host: str = "127.0.0.1"
    ui_port: int = 8765
    ui_static_dir: str = "ui/dashboard/dist"
    ui_theme: ThemeId = DEFAULT_THEME
    ui_color_mode: ColorModeId = DEFAULT_COLOR_MODE
    ui_color_modes: tuple[ColorModeId, ...] = COLOR_MODE_IDS
    ui_show_controls: bool = True
    ui_sync_offset_ms: float = 0.0
    verbose: bool = True

    def __post_init__(self) -> None:
        if self.connected_motors < 1 or self.connected_motors > 8:
            raise ValueError("connected_motors must be in range [1, 8]")
        if not str(self.instrument_profile_path).strip():
            raise ValueError("instrument_profile_path cannot be empty")
        if self.home_steps_per_rev < 1 or self.home_steps_per_rev > 0xFFFF:
            raise ValueError("home_steps_per_rev must be in range [1, 65535]")
        if self.home_hz <= 0:
            raise ValueError("home_hz must be > 0")
        if self.home_start_hz <= 0:
            raise ValueError("home_start_hz must be > 0")
        if self.home_start_hz > self.home_hz:
            raise ValueError("home_start_hz must be <= home_hz")
        if self.home_accel_hz_per_s < 0:
            raise ValueError("home_accel_hz_per_s must be >= 0")
        valid_warmups = set(WARMUP_IDS)
        for warmup in self.pre_song_warmups:
            if warmup not in valid_warmups:
                raise ValueError(f"invalid warmup routine: {warmup}")
        if self.warmup_motor_order:
            active = min(6, self.connected_motors)
            if len(self.warmup_motor_order) != active:
                raise ValueError(f"warmup_motor_order must contain exactly {active} indices")
            if len(set(self.warmup_motor_order)) != len(self.warmup_motor_order):
                raise ValueError("warmup_motor_order must not contain duplicates")
            for motor_idx in self.warmup_motor_order:
                if motor_idx < 0 or motor_idx >= self.connected_motors:
                    raise ValueError(
                        f"warmup_motor_order index out of range [0, {self.connected_motors - 1}]: {motor_idx}"
                    )
        for warmup, factor in self.warmup_speed_multipliers:
            if warmup not in valid_warmups:
                raise ValueError(f"invalid warmup speed multiplier key: {warmup}")
            if factor <= 0.0:
                raise ValueError(f"warmup speed multiplier must be > 0: {warmup}={factor}")
        if self.warmup_max_accel_hz_per_s <= 0.0:
            raise ValueError("warmup_max_accel_hz_per_s must be > 0")
        if self.startup_countdown_s < 0:
            raise ValueError("startup_countdown_s must be >= 0")
        if self.direction_flip_safety_margin_ms < 0.0:
            raise ValueError("direction_flip_safety_margin_ms must be >= 0")
        if self.direction_flip_cooldown_ms < 0.0:
            raise ValueError("direction_flip_cooldown_ms must be >= 0")
        if self.playback_run_accel_hz_per_s <= 0.0:
            raise ValueError("playback_run_accel_hz_per_s must be > 0")
        if self.playback_launch_start_hz <= 0.0:
            raise ValueError("playback_launch_start_hz must be > 0")
        if self.playback_launch_accel_hz_per_s <= 0.0:
            raise ValueError("playback_launch_accel_hz_per_s must be > 0")
        if self.playback_launch_crossover_hz < self.playback_launch_start_hz:
            raise ValueError("playback_launch_crossover_hz must be >= playback_launch_start_hz")
        if self.playback_launch_crossover_hz > self.max_freq_hz:
            raise ValueError("playback_launch_crossover_hz must be <= max_freq_hz")
        if self.scheduled_start_guard_ms < 10.0:
            raise ValueError("scheduled_start_guard_ms must be >= 10")
        if self.lookahead_strategy not in {"average", "p90", "p95", "percentile"}:
            raise ValueError("lookahead_strategy must be one of: average, p90, p95, percentile")
        if self.lookahead_min_ms < 1:
            raise ValueError("lookahead_min_ms must be >= 1")
        if self.lookahead_percentile < 50 or self.lookahead_percentile > 99:
            raise ValueError("lookahead_percentile must be in range [50, 99]")
        if self.lookahead_min_segments < 1:
            raise ValueError("lookahead_min_segments must be >= 1")
        if not self.ui_color_modes:
            raise ValueError("ui_color_modes must contain at least one color mode")
        for color_mode in self.ui_color_modes:
            coerce_color_mode_id(str(color_mode))
        if self.ui_color_mode not in self.ui_color_modes:
            raise ValueError("ui_color_mode must be included in ui_color_modes")


def _as_bool(value: object, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    raise ValueError(f"invalid bool value: {value!r}")


def _as_str_tuple(value: object, *, default: tuple[str, ...]) -> tuple[str, ...]:
    if value is None:
        return default
    if isinstance(value, (list, tuple)):
        out: list[str] = []
        for item in value:
            if not isinstance(item, str):
                raise ValueError(f"invalid string list value: {item!r}")
            normalized = item.strip()
            if normalized:
                out.append(normalized)
        return tuple(out)
    raise ValueError(f"invalid string list value: {value!r}")


def _as_int_tuple(value: object, *, default: tuple[int, ...]) -> tuple[int, ...]:
    if value is None:
        return default
    if isinstance(value, (list, tuple)):
        out: list[int] = []
        for item in value:
            out.append(int(item))
        return tuple(out)
    raise ValueError(f"invalid int list value: {value!r}")


def _as_float_map(
    value: object,
    *,
    default: tuple[tuple[str, float], ...],
) -> tuple[tuple[str, float], ...]:
    if value is None:
        return default
    if isinstance(value, dict):
        pairs: list[tuple[str, float]] = []
        for key, raw_factor in value.items():
            if not isinstance(key, str):
                raise ValueError(f"invalid map key: {key!r}")
            normalized = key.strip()
            if not normalized:
                continue
            pairs.append((normalized, float(raw_factor)))
        return tuple(sorted(pairs, key=lambda pair: pair[0]))
    raise ValueError(f"invalid float map value: {value!r}")


def _as_color_mode_tuple(
    value: object,
    *,
    default: tuple[ColorModeId, ...],
) -> tuple[ColorModeId, ...]:
    if value is None:
        return default
    if isinstance(value, (list, tuple)):
        deduped: list[ColorModeId] = []
        seen: set[ColorModeId] = set()
        for item in value:
            mode = coerce_color_mode_id(str(item))
            if mode in seen:
                continue
            seen.add(mode)
            deduped.append(mode)
        return tuple(deduped) if deduped else default
    raise ValueError(f"invalid color mode list value: {value!r}")


def load_config(path: str | Path = "config.toml") -> HostConfig:
    cfg_path = Path(path)
    if not cfg_path.exists():
        return HostConfig()

    with cfg_path.open("rb") as f:
        raw = tomllib.load(f)

    serial_cfg = raw.get("serial", {})
    hardware_cfg = raw.get("hardware", {})
    instrument_cfg = raw.get("instrument", {})
    pipeline_cfg = raw.get("pipeline", {})
    homing_cfg = raw.get("homing", {})
    warmups_cfg = raw.get("warmups", {})
    playback_cfg = raw.get("playback", {})
    ui_cfg = raw.get("ui", {})
    removed_pipeline_keys = (
        "segment_floor_us",
        "segment_floor_pulse_budget",
        "max_active_playback_motors",
        "max_aggregate_step_rate",
        "reattack_bridge_us",
    )
    present_removed_keys = [key for key in removed_pipeline_keys if key in pipeline_cfg]
    if present_removed_keys:
        removed_list = ", ".join(present_removed_keys)
        raise ValueError(
            "obsolete [pipeline] playback-mitigation keys are no longer supported after the "
            f"event-group playback cutover: {removed_list}"
        )

    transpose_raw = pipeline_cfg.get("transpose_override", HostConfig.transpose_override)
    transpose_override = None if transpose_raw is None else int(transpose_raw)

    idle_mode = str(pipeline_cfg.get("idle_mode", HostConfig.idle_mode))
    if idle_mode not in {"idle", "duplicate"}:
        raise ValueError(f"invalid idle_mode: {idle_mode}")
    overflow_mode = str(pipeline_cfg.get("overflow_mode", HostConfig.overflow_mode))
    if overflow_mode not in {"steal_quietest", "drop_newest", "strict"}:
        raise ValueError(f"invalid overflow_mode: {overflow_mode}")
    lookahead_strategy = str(pipeline_cfg.get("lookahead_strategy", HostConfig.lookahead_strategy))
    if lookahead_strategy not in {"average", "p90", "p95", "percentile"}:
        raise ValueError(f"invalid lookahead_strategy: {lookahead_strategy}")

    connected_motors = int(hardware_cfg.get("connected_motors", HostConfig.connected_motors))
    if connected_motors < 1 or connected_motors > 8:
        raise ValueError("connected_motors must be in range [1, 8]")
    instrument_profile_path = str(
        resolve_instrument_profile_path(
            instrument_cfg.get("profile", HostConfig.instrument_profile_path),
            base_dir=cfg_path.parent,
        )
    )
    home_hz = float(homing_cfg.get("home_hz", HostConfig.home_hz))
    if "home_start_hz" in homing_cfg:
        home_start_hz = float(homing_cfg["home_start_hz"])
    else:
        home_start_hz = min(HostConfig.home_start_hz, home_hz)

    min_freq_hz = float(pipeline_cfg.get("min_freq_hz", HostConfig.min_freq_hz))
    max_freq_hz = float(pipeline_cfg.get("max_freq_hz", HostConfig.max_freq_hz))

    if max_freq_hz > _FIRMWARE_MAX_FREQ_HZ:
        _log.warning(
            "max_freq_hz=%.0f exceeds firmware safety ceiling (%.0f Hz); "
            "clamping to %.0f Hz to preserve step timing margin.",
            max_freq_hz,
            _FIRMWARE_MAX_FREQ_HZ,
            _FIRMWARE_MAX_FREQ_HZ,
        )
        max_freq_hz = _FIRMWARE_MAX_FREQ_HZ

    if min_freq_hz < _RECOMMENDED_MIN_FREQ_HZ:
        _log.warning(
            "min_freq_hz=%.0f is below the validated floor (%.0f Hz).  "
            "Playback below this range may not be mechanically reliable on the current setup.",
            min_freq_hz,
            _RECOMMENDED_MIN_FREQ_HZ,
        )
    pre_song_warmups = _as_str_tuple(
        warmups_cfg.get("sequence", HostConfig.pre_song_warmups),
        default=HostConfig.pre_song_warmups,
    )
    warmup_motor_order = _as_int_tuple(
        warmups_cfg.get("motor_order"),
        default=HostConfig.warmup_motor_order,
    )
    warmup_speed_multipliers = _as_float_map(
        warmups_cfg.get("speed_multipliers"),
        default=HostConfig.warmup_speed_multipliers,
    )
    warmup_max_accel_hz_per_s = float(
        warmups_cfg.get("max_accel_hz_per_s", HostConfig.warmup_max_accel_hz_per_s)
    )
    warmup_require_home_before_sequence = _as_bool(
        warmups_cfg.get(
            "require_home_before_sequence",
            HostConfig.warmup_require_home_before_sequence,
        ),
        default=HostConfig.warmup_require_home_before_sequence,
    )
    ui_color_mode_raw = ui_cfg.get("color_mode")
    if ui_color_mode_raw is None:
        ui_color_mode_raw = ui_cfg.get("default_color_mode")
    if ui_color_mode_raw is None:
        ui_color_mode_raw = ui_cfg.get("default_coloring")
    ui_color_mode = coerce_color_mode_id(
        str(ui_color_mode_raw if ui_color_mode_raw is not None else HostConfig.ui_color_mode)
    )
    ui_color_modes_raw = ui_cfg.get("color_modes")
    if ui_color_modes_raw is None:
        ui_color_modes_raw = ui_cfg.get("available_color_modes")
    ui_color_modes = _as_color_mode_tuple(
        ui_color_modes_raw,
        default=HostConfig.ui_color_modes,
    )
    ui_show_controls_raw = ui_cfg.get("show_controls")
    if ui_show_controls_raw is None:
        ui_show_controls_raw = ui_cfg.get("show_details")
    if ui_color_mode not in ui_color_modes:
        ui_color_modes = (ui_color_mode, *ui_color_modes)

    return HostConfig(
        port=str(serial_cfg.get("port", HostConfig.port)),
        baudrate=int(serial_cfg.get("baudrate", HostConfig.baudrate)),
        timeout_s=float(serial_cfg.get("timeout_s", HostConfig.timeout_s)),
        write_timeout_s=float(serial_cfg.get("write_timeout_s", HostConfig.write_timeout_s)),
        retries=int(serial_cfg.get("retries", HostConfig.retries)),
        connected_motors=connected_motors,
        instrument_profile_path=instrument_profile_path,
        idle_mode=idle_mode,
        overflow_mode=cast(OverflowMode, overflow_mode),
        min_freq_hz=min_freq_hz,
        max_freq_hz=max_freq_hz,
        auto_transpose=_as_bool(pipeline_cfg.get("auto_transpose", HostConfig.auto_transpose), default=HostConfig.auto_transpose),
        auto_home=_as_bool(homing_cfg.get("auto_home", HostConfig.auto_home), default=HostConfig.auto_home),
        transpose_override=transpose_override,
        sticky_gap_ms=int(pipeline_cfg.get("sticky_gap_ms", HostConfig.sticky_gap_ms)),
        double_melody=_as_bool(
            pipeline_cfg.get("double_melody", HostConfig.double_melody),
            default=HostConfig.double_melody,
        ),
        lookahead_ms=int(pipeline_cfg.get("lookahead_ms", HostConfig.lookahead_ms)),
        lookahead_strategy=cast(LookaheadStrategy, lookahead_strategy),
        lookahead_min_ms=int(pipeline_cfg.get("lookahead_min_ms", HostConfig.lookahead_min_ms)),
        lookahead_percentile=int(pipeline_cfg.get("lookahead_percentile", HostConfig.lookahead_percentile)),
        lookahead_min_segments=int(
            pipeline_cfg.get("lookahead_min_segments", HostConfig.lookahead_min_segments)
        ),
        home_steps_per_rev=int(homing_cfg.get("steps_per_rev", HostConfig.home_steps_per_rev)),
        home_hz=home_hz,
        home_start_hz=home_start_hz,
        home_accel_hz_per_s=float(homing_cfg.get("home_accel_hz_per_s", HostConfig.home_accel_hz_per_s)),
        pre_song_warmups=pre_song_warmups,
        warmup_motor_order=warmup_motor_order,
        warmup_speed_multipliers=warmup_speed_multipliers,
        warmup_max_accel_hz_per_s=warmup_max_accel_hz_per_s,
        warmup_require_home_before_sequence=warmup_require_home_before_sequence,
        startup_countdown_s=int(playback_cfg.get("startup_countdown_s", HostConfig.startup_countdown_s)),
        flip_direction_on_note_change=_as_bool(
            playback_cfg.get(
                "flip_direction_on_note_change",
                HostConfig.flip_direction_on_note_change,
            ),
            default=HostConfig.flip_direction_on_note_change,
        ),
        suppress_tight_direction_flips=_as_bool(
            playback_cfg.get(
                "suppress_tight_direction_flips",
                HostConfig.suppress_tight_direction_flips,
            ),
            default=HostConfig.suppress_tight_direction_flips,
        ),
        direction_flip_safety_margin_ms=float(
            playback_cfg.get(
                "direction_flip_safety_margin_ms",
                HostConfig.direction_flip_safety_margin_ms,
            )
        ),
        direction_flip_cooldown_ms=float(
            playback_cfg.get(
                "direction_flip_cooldown_ms",
                HostConfig.direction_flip_cooldown_ms,
            )
        ),
        playback_run_accel_hz_per_s=float(
            playback_cfg.get("run_accel_hz_per_s", HostConfig.playback_run_accel_hz_per_s)
        ),
        playback_launch_start_hz=float(
            playback_cfg.get("launch_start_hz", HostConfig.playback_launch_start_hz)
        ),
        playback_launch_accel_hz_per_s=float(
            playback_cfg.get("launch_accel_hz_per_s", HostConfig.playback_launch_accel_hz_per_s)
        ),
        playback_launch_crossover_hz=float(
            playback_cfg.get("launch_crossover_hz", HostConfig.playback_launch_crossover_hz)
        ),
        scheduled_start_guard_ms=float(
            playback_cfg.get("scheduled_start_guard_ms", HostConfig.scheduled_start_guard_ms)
        ),
        ui_host=str(ui_cfg.get("host", HostConfig.ui_host)),
        ui_port=int(ui_cfg.get("port", HostConfig.ui_port)),
        ui_static_dir=str(ui_cfg.get("static_dir", HostConfig.ui_static_dir)),
        ui_theme=coerce_theme_id(str(ui_cfg.get("theme", HostConfig.ui_theme))),
        ui_color_mode=ui_color_mode,
        ui_color_modes=ui_color_modes,
        ui_show_controls=_as_bool(ui_show_controls_raw, default=HostConfig.ui_show_controls),
        ui_sync_offset_ms=float(ui_cfg.get("sync_offset_ms", HostConfig.ui_sync_offset_ms)),
        verbose=_as_bool(ui_cfg.get("verbose", HostConfig.verbose), default=HostConfig.verbose),
    )

from __future__ import annotations

from pathlib import Path

import pytest

from music2.config import load_config
from music2.instrument_profile import DEFAULT_INSTRUMENT_PROFILE_PATH
from music2.viewer_color_mode import COLOR_MODE_IDS


def test_load_config_defaults_to_microstep_4_homing(tmp_path: Path) -> None:
    cfg = load_config(tmp_path / "does-not-exist.toml")
    assert cfg.home_steps_per_rev == 800
    assert cfg.home_hz == 160.0
    assert cfg.home_start_hz == 120.0
    assert cfg.home_accel_hz_per_s == 240.0
    assert cfg.auto_home is True
    assert cfg.pre_song_warmups == ()
    assert cfg.warmup_motor_order == ()
    assert cfg.warmup_speed_multipliers == ()
    assert cfg.warmup_max_accel_hz_per_s == 180.0
    assert cfg.warmup_require_home_before_sequence is True
    assert cfg.startup_countdown_s == 10
    assert cfg.flip_direction_on_note_change is False
    assert cfg.suppress_tight_direction_flips is True
    assert cfg.direction_flip_safety_margin_ms == 50.0
    assert cfg.direction_flip_cooldown_ms == 150.0
    assert cfg.playback_run_accel_hz_per_s == 8000.0
    assert cfg.playback_launch_start_hz == 60.0
    assert cfg.playback_launch_accel_hz_per_s == 5000.0
    assert cfg.playback_launch_crossover_hz == 180.0
    assert cfg.double_melody is True
    assert cfg.instrument_profile_path == str(DEFAULT_INSTRUMENT_PROFILE_PATH)


def test_load_config_reads_instrument_profile_relative_to_config(tmp_path: Path) -> None:
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    profile_path = profiles_dir / "custom.toml"
    profile_path.write_text(
        """
[instrument]
name = "tmp_profile"
profile_version = 1
motor_count = 1

[[instrument.motors]]
motor_idx = 0
label = "solo"
min_hz = 30.0
max_hz = 300.0
""".strip()
        + "\n",
        encoding="utf-8",
    )
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        """
[instrument]
profile = "profiles/custom.toml"
""".strip()
        + "\n",
        encoding="utf-8",
    )

    cfg = load_config(cfg_path)

    assert cfg.instrument_profile_path == str(profile_path.resolve())


def test_load_config_reads_ui_theme(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        """
[ui]
theme = "retro"
""".strip()
        + "\n",
        encoding="utf-8",
    )

    cfg = load_config(cfg_path)

    assert cfg.ui_theme == "retro"
    assert cfg.ui_color_mode == "monochrome_accent"
    assert cfg.ui_color_modes == COLOR_MODE_IDS
    assert cfg.ui_show_controls is True
    assert cfg.ui_sync_offset_ms == 0.0


def test_load_config_reads_playback_startup_countdown(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        """
[playback]
startup_countdown_s = 4
""".strip()
        + "\n",
        encoding="utf-8",
    )

    cfg = load_config(cfg_path)

    assert cfg.startup_countdown_s == 4


def test_load_config_reads_playback_direction_flip_flag(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        """
[playback]
flip_direction_on_note_change = true
""".strip()
        + "\n",
        encoding="utf-8",
    )

    cfg = load_config(cfg_path)

    assert cfg.flip_direction_on_note_change is True


def test_load_config_reads_playback_flip_safety_policy(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        """
[playback]
suppress_tight_direction_flips = false
direction_flip_safety_margin_ms = 12.5
direction_flip_cooldown_ms = 87.5
""".strip()
        + "\n",
        encoding="utf-8",
    )

    cfg = load_config(cfg_path)

    assert cfg.suppress_tight_direction_flips is False
    assert cfg.direction_flip_safety_margin_ms == 12.5
    assert cfg.direction_flip_cooldown_ms == 87.5


def test_load_config_reads_playback_tuning_profile(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        """
[playback]
run_accel_hz_per_s = 3200.0
launch_start_hz = 55.0
launch_accel_hz_per_s = 1800.0
launch_crossover_hz = 150.0
""".strip()
        + "\n",
        encoding="utf-8",
    )

    cfg = load_config(cfg_path)

    assert cfg.playback_run_accel_hz_per_s == 3200.0
    assert cfg.playback_launch_start_hz == 55.0
    assert cfg.playback_launch_accel_hz_per_s == 1800.0
    assert cfg.playback_launch_crossover_hz == 150.0


def test_load_config_reads_pipeline_double_melody_flag(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        """
[pipeline]
double_melody = false
""".strip()
        + "\n",
        encoding="utf-8",
    )

    cfg = load_config(cfg_path)

    assert cfg.double_melody is False


def test_load_config_rejects_negative_playback_startup_countdown(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        """
[playback]
startup_countdown_s = -1
""".strip()
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="startup_countdown_s must be >= 0"):
        load_config(cfg_path)


def test_load_config_reads_ui_sync_offset_ms(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        """
[ui]
theme = "neon"
sync_offset_ms = -145.5
""".strip()
        + "\n",
        encoding="utf-8",
    )

    cfg = load_config(cfg_path)

    assert cfg.ui_theme == "neon"
    assert cfg.ui_color_mode == "monochrome_accent"
    assert cfg.ui_color_modes == COLOR_MODE_IDS
    assert cfg.ui_show_controls is True
    assert cfg.ui_sync_offset_ms == -145.5


def test_load_config_rejects_invalid_ui_theme(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        """
[ui]
theme = "synthwave"
""".strip()
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="invalid ui theme"):
        load_config(cfg_path)


def test_load_config_reads_ui_color_mode_and_modes(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        """
[ui]
theme = "neon"
color_mode = "frequency_bands"
color_modes = ["frequency_bands", "channel", "frequency_bands", "motor_slot"]
""".strip()
        + "\n",
        encoding="utf-8",
    )

    cfg = load_config(cfg_path)

    assert cfg.ui_color_mode == "frequency_bands"
    assert cfg.ui_color_modes == ("frequency_bands", "channel", "motor_slot")


def test_load_config_reads_ui_color_mode_from_default_color_mode_alias(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        """
[ui]
default_color_mode = "motor_slot"
""".strip()
        + "\n",
        encoding="utf-8",
    )

    cfg = load_config(cfg_path)
    assert cfg.ui_color_mode == "motor_slot"


def test_load_config_reads_ui_color_mode_from_default_coloring_alias(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        """
[ui]
default_coloring = "octave_bands"
""".strip()
        + "\n",
        encoding="utf-8",
    )

    cfg = load_config(cfg_path)
    assert cfg.ui_color_mode == "octave_bands"


def test_load_config_includes_selected_color_mode_when_not_listed(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        """
[ui]
color_mode = "octave_bands"
color_modes = ["channel", "frequency_bands"]
""".strip()
        + "\n",
        encoding="utf-8",
    )

    cfg = load_config(cfg_path)

    assert cfg.ui_color_mode == "octave_bands"
    assert cfg.ui_color_modes == ("octave_bands", "channel", "frequency_bands")


def test_load_config_empty_color_modes_falls_back_to_defaults(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        """
[ui]
color_modes = []
""".strip()
        + "\n",
        encoding="utf-8",
    )

    cfg = load_config(cfg_path)
    assert cfg.ui_color_modes == COLOR_MODE_IDS


def test_load_config_rejects_invalid_ui_color_mode(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        """
[ui]
color_mode = "rainbow"
""".strip()
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="invalid ui color mode"):
        load_config(cfg_path)


def test_load_config_rejects_invalid_ui_color_modes_entry(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        """
[ui]
color_modes = ["channel", "rainbow"]
""".strip()
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="invalid ui color mode"):
        load_config(cfg_path)


def test_load_config_reads_ui_show_controls(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        """
[ui]
show_controls = false
""".strip()
        + "\n",
        encoding="utf-8",
    )

    cfg = load_config(cfg_path)
    assert cfg.ui_show_controls is False


def test_load_config_reads_ui_show_details_alias(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        """
[ui]
show_details = false
""".strip()
        + "\n",
        encoding="utf-8",
    )

    cfg = load_config(cfg_path)
    assert cfg.ui_show_controls is False


def test_load_config_reads_homing_settings(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        """
[homing]
steps_per_rev = 6400
home_start_hz = 72.0
home_hz = 96.5
home_accel_hz_per_s = 360
auto_home = false
""".strip()
        + "\n",
        encoding="utf-8",
    )

    cfg = load_config(cfg_path)

    assert cfg.home_steps_per_rev == 6400
    assert cfg.home_hz == 96.5
    assert cfg.home_start_hz == 72.0
    assert cfg.home_accel_hz_per_s == 360.0
    assert cfg.auto_home is False


def test_load_config_reads_warmup_sequence_order(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        """
[warmups]
sequence = ["slot_machine_lock_in", "phase_alignment", "domino_ripple"]
""".strip()
        + "\n",
        encoding="utf-8",
    )

    cfg = load_config(cfg_path)

    assert cfg.pre_song_warmups == (
        "slot_machine_lock_in",
        "phase_alignment",
        "domino_ripple",
    )


def test_load_config_rejects_unknown_warmup_sequence_value(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        """
[warmups]
sequence = ["slot_machine_lock_in", "laser_show"]
""".strip()
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="invalid warmup routine"):
        load_config(cfg_path)


def test_load_config_reads_warmup_speed_multipliers(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        """
[warmups]
sequence = ["slot_machine_lock_in", "phase_alignment"]
max_accel_hz_per_s = 150

[warmups.speed_multipliers]
slot_machine_lock_in = 1.25
phase_alignment = 0.85
""".strip()
        + "\n",
        encoding="utf-8",
    )

    cfg = load_config(cfg_path)

    assert dict(cfg.warmup_speed_multipliers) == {
        "phase_alignment": 0.85,
        "slot_machine_lock_in": 1.25,
    }
    assert cfg.warmup_max_accel_hz_per_s == 150.0


def test_load_config_reads_warmup_home_requirement_flag(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        """
[warmups]
sequence = ["slot_machine_lock_in"]
require_home_before_sequence = false
""".strip()
        + "\n",
        encoding="utf-8",
    )

    cfg = load_config(cfg_path)

    assert cfg.warmup_require_home_before_sequence is False


def test_load_config_reads_warmup_motor_order(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        """
[hardware]
connected_motors = 6

[warmups]
sequence = ["slot_machine_lock_in"]
motor_order = [4, 2, 1, 3, 0, 5]
""".strip()
        + "\n",
        encoding="utf-8",
    )

    cfg = load_config(cfg_path)
    assert cfg.warmup_motor_order == (4, 2, 1, 3, 0, 5)


def test_load_config_rejects_invalid_warmup_motor_order(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        """
[hardware]
connected_motors = 6

[warmups]
sequence = ["slot_machine_lock_in"]
motor_order = [4, 2, 1, 3, 0, 0]
""".strip()
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="warmup_motor_order"):
        load_config(cfg_path)


def test_load_config_rejects_invalid_warmup_speed_multiplier_key(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        """
[warmups]
sequence = ["slot_machine_lock_in"]

[warmups.speed_multipliers]
laser_show = 1.2
""".strip()
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="invalid warmup speed multiplier key"):
        load_config(cfg_path)


def test_load_config_rejects_non_positive_warmup_speed_multiplier(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        """
[warmups]
sequence = ["slot_machine_lock_in"]

[warmups.speed_multipliers]
slot_machine_lock_in = 0
""".strip()
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="warmup speed multiplier must be > 0"):
        load_config(cfg_path)


def test_load_config_rejects_non_positive_warmup_accel(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        """
[warmups]
sequence = ["slot_machine_lock_in"]
max_accel_hz_per_s = 0
""".strip()
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="warmup_max_accel_hz_per_s"):
        load_config(cfg_path)


def test_load_config_rejects_invalid_homing_values(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        """
[homing]
steps_per_rev = 0
home_hz = 80
""".strip()
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="home_steps_per_rev"):
        load_config(cfg_path)


def test_load_config_rejects_home_start_above_home_hz(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        """
[homing]
steps_per_rev = 800
home_start_hz = 220
home_hz = 180
""".strip()
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="home_start_hz"):
        load_config(cfg_path)


def test_load_config_reads_pipeline_fields(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        """
[pipeline]
lookahead_strategy = "p95"
lookahead_min_ms = 300
lookahead_percentile = 95
lookahead_min_segments = 31
""".strip()
        + "\n",
        encoding="utf-8",
    )

    cfg = load_config(cfg_path)
    assert cfg.lookahead_strategy == "p95"
    assert cfg.lookahead_min_ms == 300
    assert cfg.lookahead_percentile == 95
    assert cfg.lookahead_min_segments == 31


def test_load_config_rejects_removed_pipeline_mitigation_keys(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        """
[pipeline]
segment_floor_us = 42
segment_floor_pulse_budget = 0.5
max_active_playback_motors = 4
max_aggregate_step_rate = 6000
reattack_bridge_us = 15000
""".strip()
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="obsolete \\[pipeline\\] playback-mitigation keys"):
        load_config(cfg_path)


def test_load_config_accepts_percentile_lookahead_strategy(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        """
[pipeline]
lookahead_strategy = "percentile"
lookahead_percentile = 87
""".strip()
        + "\n",
        encoding="utf-8",
    )

    cfg = load_config(cfg_path)
    assert cfg.lookahead_strategy == "percentile"
    assert cfg.lookahead_percentile == 87


def test_load_config_rejects_invalid_lookahead_min_segments(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        """
[pipeline]
lookahead_min_segments = 0
""".strip()
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="lookahead_min_segments"):
        load_config(cfg_path)


def test_load_config_clamps_max_freq_to_firmware_limit(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        """
[pipeline]
max_freq_hz = 5000
""".strip()
        + "\n",
        encoding="utf-8",
    )

    cfg = load_config(cfg_path)
    # Safety ceiling is 800 Hz; config loader must clamp.
    assert cfg.max_freq_hz <= 800.0


def test_load_config_defaults_video_settings(tmp_path: Path) -> None:
    cfg = load_config(tmp_path / "does-not-exist.toml")
    assert cfg.video_render_mode == "half_block"
    assert cfg.video_color_mode == "original"


def test_load_config_reads_video_section(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[video]\nrender_mode = "classic"\ncolor_mode = "theme"\n'
    )
    cfg = load_config(config_path)
    assert cfg.video_render_mode == "classic"
    assert cfg.video_color_mode == "theme"


def test_load_config_rejects_invalid_video_render_mode(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text('[video]\nrender_mode = "invalid"\n')
    with pytest.raises(ValueError, match="video_render_mode"):
        load_config(config_path)


def test_load_config_rejects_invalid_video_color_mode(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text('[video]\ncolor_mode = "invalid"\n')
    with pytest.raises(ValueError, match="video_color_mode"):
        load_config(config_path)


def test_load_config_warns_on_low_min_freq(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        """
[pipeline]
min_freq_hz = 10
""".strip()
        + "\n",
        encoding="utf-8",
    )

    import logging

    with caplog.at_level(logging.WARNING, logger="music2.config"):
        cfg = load_config(cfg_path)

    assert cfg.min_freq_hz == 10.0  # not hard-clamped, just warned
    assert any("below the validated floor" in record.message for record in caplog.records)

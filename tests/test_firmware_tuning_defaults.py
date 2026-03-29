from __future__ import annotations

import re
from pathlib import Path


def test_default_playback_accel_ceiling_is_conservative() -> None:
    header_path = Path(__file__).resolve().parents[1] / "firmware" / "esp32" / "src" / "pulse_engine.h"
    content = header_path.read_text(encoding="utf-8")

    match = re.search(
        r"#define\s+MUSIC2_PULSE_ENGINE_MAX_ACCEL_DHZ_PER_S\s+\((\d+)u\)",
        content,
    )
    assert match is not None, "expected default accel ceiling define in pulse_engine.h"
    assert int(match.group(1)) == 100000


def test_default_playback_safe_max_frequency_matches_edge_drop_headroom() -> None:
    main_path = Path(__file__).resolve().parents[1] / "firmware" / "esp32" / "src" / "main.c"
    content = main_path.read_text(encoding="utf-8")

    match = re.search(
        r"#define\s+MUSIC2_SAFE_MAX_FREQ_DHZ\s+\((\d+)u\)",
        content,
    )
    assert match is not None, "expected safe max frequency define in main.c"
    assert int(match.group(1)) == 8000


def test_default_min_half_period_guard_is_100us() -> None:
    header_path = Path(__file__).resolve().parents[1] / "firmware" / "esp32" / "src" / "pulse_engine.h"
    content = header_path.read_text(encoding="utf-8")

    match = re.search(
        r"#define\s+MUSIC2_PULSE_ENGINE_MIN_HALF_PERIOD_US\s+\((\d+)u\)",
        content,
    )
    assert match is not None, "expected min half-period guard define in pulse_engine.h"
    assert int(match.group(1)) == 100

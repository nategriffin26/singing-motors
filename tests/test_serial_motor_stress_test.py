from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "serial_motor_stress_test.py"
_SPEC = importlib.util.spec_from_file_location("serial_motor_stress_test", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


def test_default_motor_order_follows_config_order() -> None:
    order = _MODULE._default_motor_order(
        connected_motors=6,
        config_order=(4, 2, 1, 3, 0, 5),
    )
    assert order == (4, 2, 1, 3, 0, 5)


def test_default_motor_order_appends_missing_indices() -> None:
    order = _MODULE._default_motor_order(
        connected_motors=8,
        config_order=(4, 2, 1, 3, 0, 5),
    )
    assert order == (4, 2, 1, 3, 0, 5, 6, 7)


def test_resolve_motor_order_override_rejects_out_of_range() -> None:
    with pytest.raises(ValueError, match="out of range"):
        _MODULE._resolve_motor_order(
            connected_motors=6,
            config_order=(4, 2, 1, 3, 0, 5),
            override="4,2,8",
        )


def test_build_stress_phases_aligns_total_steps_to_home_position() -> None:
    phases, align_added_steps = _MODULE._build_stress_phases(
        min_hz=30.0,
        max_hz=650.0,
        slow_accel_hz_per_s=220.0,
        slow_decel_hz_per_s=220.0,
        fast_accel_hz_per_s=2800.0,
        fast_decel_hz_per_s=2800.0,
        hold_ms=0,
        steps_per_rev=800,
    )

    assert len(phases) == 4
    assert phases[0].peak_hz == pytest.approx(30.0)
    assert phases[1].peak_hz == pytest.approx(650.0)
    assert phases[2].peak_hz == pytest.approx(30.0)
    assert phases[3].peak_hz == pytest.approx(650.0)
    assert all(phase.target_steps > 0 for phase in phases)

    total_steps = sum(phase.target_steps for phase in phases)
    assert total_steps % 800 == 0
    assert align_added_steps >= 0


def test_build_stress_phases_collapses_duplicate_peaks() -> None:
    phases, _ = _MODULE._build_stress_phases(
        min_hz=120.0,
        max_hz=120.0,
        slow_accel_hz_per_s=220.0,
        slow_decel_hz_per_s=220.0,
        fast_accel_hz_per_s=2800.0,
        fast_decel_hz_per_s=2800.0,
        hold_ms=0,
        steps_per_rev=800,
    )

    assert len(phases) == 2
    assert phases[0].peak_hz == pytest.approx(120.0)
    assert phases[1].peak_hz == pytest.approx(120.0)

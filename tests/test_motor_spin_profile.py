from __future__ import annotations

from music2.motor_spin_profile import SpinProfileConfig, build_spin_test_segments, per_motor_step_counts


def test_spin_profile_totals_are_exact_for_each_motor() -> None:
    cfg = SpinProfileConfig()
    segments = build_spin_test_segments(cfg)
    counts = per_motor_step_counts(segments, cfg.motor_indices)
    for motor_idx in cfg.motor_indices:
        assert counts[motor_idx] == cfg.steps_per_motor


def test_spin_profile_runs_only_one_motor_at_a_time_in_sequence() -> None:
    cfg = SpinProfileConfig()
    segments = build_spin_test_segments(cfg)

    transitions: list[int] = []
    prev_active: int | None = None
    for segment in segments:
        active = [idx for idx, hz in enumerate(segment.motor_freq_hz) if hz > 0.0]
        assert len(active) <= 1
        current = active[0] if active else None
        if current is not None and current != prev_active:
            transitions.append(current)
        prev_active = current

    assert transitions == list(cfg.motor_indices)

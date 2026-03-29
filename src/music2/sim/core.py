from __future__ import annotations

from dataclasses import asdict
from typing import Any

from ..instrument_profile import InstrumentProfile
from ..playback_program import PlaybackPlan
from .backends import SimplifiedMotorBackend
from .trace_models import PlannedEventTrace, SimulatedMotorState


def simulate_playback_plan(
    *,
    playback_plan: PlaybackPlan,
    instrument_profile: InstrumentProfile,
) -> dict[str, Any]:
    backend = SimplifiedMotorBackend()
    plan_time_us = 0
    planned: list[PlannedEventTrace] = []
    simulated: list[SimulatedMotorState] = []
    current_hz = [0.0 for _ in range(playback_plan.connected_motors)]
    risk_hits = 0
    flip_count = 0

    for event_index, group in enumerate(playback_plan.event_groups):
        plan_time_us += max(0, group.delta_us)
        for change in group.changes:
            planned.append(
                PlannedEventTrace(
                    event_index=event_index,
                    plan_time_us=plan_time_us,
                    delta_us=group.delta_us,
                    motor_idx=change.motor_idx,
                    target_hz=change.target_hz,
                    flip_before_restart=change.flip_before_restart,
                )
            )
            current_hz[change.motor_idx] = change.target_hz
            if change.flip_before_restart:
                flip_count += 1
            motor = instrument_profile.ordered_motors[change.motor_idx]
            classification = backend.classify(motor, change.target_hz)
            if classification.entered_risk_band:
                risk_hits += 1
            simulated.append(
                SimulatedMotorState(
                    event_index=event_index,
                    plan_time_us=plan_time_us,
                    motor_idx=change.motor_idx,
                    current_hz=change.target_hz,
                    active=change.target_hz > 0.0,
                    entered_risk_band=classification.entered_risk_band,
                    risk_tags=classification.risk_tags,
                )
            )

    return {
        "plan_traces": [asdict(item) for item in planned],
        "simulated_states": [asdict(item) for item in simulated],
        "summary": {
            "event_group_count": playback_plan.event_group_count,
            "duration_total_us": playback_plan.duration_total_us,
            "motor_change_count": playback_plan.motor_change_count,
            "risk_hit_count": risk_hits,
            "flip_count": flip_count,
            "max_active_motors": max((sum(1 for hz in current_hz if hz > 0.0),), default=0),
        },
    }

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class PlannedEventTrace:
    event_index: int
    plan_time_us: int
    delta_us: int
    motor_idx: int
    target_hz: float
    flip_before_restart: bool = False


@dataclass(frozen=True)
class SimulatedMotorState:
    event_index: int
    plan_time_us: int
    motor_idx: int
    current_hz: float
    active: bool
    entered_risk_band: bool = False
    risk_tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class RecordedStatusSample:
    captured_monotonic_s: float
    sent_segments: int
    total_segments: int
    queue_depth: int
    queue_capacity: int
    credits: int
    active_motors: int
    playhead_us: int
    playing: bool
    stream_open: bool
    stream_end_received: bool


@dataclass(frozen=True)
class RecordedMetricsSample:
    captured_monotonic_s: float
    sent_segments: int
    total_segments: int
    underrun_count: int
    queue_high_water: int
    scheduling_late_max_us: int
    crc_parse_errors: int
    rx_parse_errors: int
    timer_empty_events: int
    timer_restart_count: int
    event_groups_started: int
    scheduler_guard_hits: int
    control_late_max_us: int
    control_overrun_count: int
    wave_period_update_count: int
    motor_start_count: int
    motor_stop_count: int
    flip_restart_count: int
    launch_guard_count: int
    engine_fault_count: int
    engine_fault_mask: int


@dataclass(frozen=True)
class ReplayBundle:
    replay_id: str
    source_bundle_id: str
    source_bundle_type: str
    plan_traces: tuple[PlannedEventTrace, ...] = ()
    simulated_states: tuple[SimulatedMotorState, ...] = ()
    status_samples: tuple[RecordedStatusSample, ...] = ()
    metrics_samples: tuple[RecordedMetricsSample, ...] = ()
    summary: dict[str, int | float | str] = field(default_factory=dict)

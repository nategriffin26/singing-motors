from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

IdleMode = Literal["idle", "duplicate"]
OverflowMode = Literal["steal_quietest", "drop_newest", "strict"]
LookaheadStrategy = Literal["average", "p90", "p95", "percentile"]


@dataclass(frozen=True)
class NoteEvent:
    start_s: float
    end_s: float
    source_note: int
    transposed_note: int
    frequency_hz: float
    velocity: int
    channel: int
    source_track: int = 0
    source_track_name: str | None = None


@dataclass(frozen=True)
class MidiAnalysisReport:
    notes: list[NoteEvent]
    duration_s: float
    note_count: int
    max_polyphony: int
    transpose_semitones: int
    clamped_note_count: int
    min_source_note: int | None
    max_source_note: int | None


@dataclass(frozen=True)
class Segment:
    duration_us: int
    motor_freq_hz: tuple[float, ...]
    direction_flip_mask: int = 0

    def __post_init__(self) -> None:
        if self.direction_flip_mask < 0 or self.direction_flip_mask > 0xFF:
            raise ValueError("direction_flip_mask must be in range [0, 255]")


@dataclass(frozen=True)
class PlaybackMotorChange:
    motor_idx: int
    target_hz: float
    flip_before_restart: bool = False

    def __post_init__(self) -> None:
        if self.motor_idx < 0 or self.motor_idx > 7:
            raise ValueError("motor_idx must be in range [0, 7]")
        if self.target_hz < 0.0:
            raise ValueError("target_hz must be >= 0")


@dataclass(frozen=True)
class PlaybackEventGroup:
    delta_us: int
    changes: tuple[PlaybackMotorChange, ...]

    def __post_init__(self) -> None:
        if self.delta_us < 0:
            raise ValueError("delta_us must be >= 0")
        if not self.changes:
            raise ValueError("changes cannot be empty")
        seen: set[int] = set()
        for change in self.changes:
            if change.motor_idx in seen:
                raise ValueError("changes cannot contain duplicate motor_idx values")
            seen.add(change.motor_idx)


@dataclass(frozen=True)
class CompileOptions:
    connected_motors: int = 6
    idle_mode: IdleMode = "duplicate"
    overflow_mode: OverflowMode = "steal_quietest"
    sticky_gap_s: float = 0.05
    melody_doubling_enabled: bool = False
    flip_direction_on_note_change: bool = False
    suppress_tight_direction_flips: bool = True
    direction_flip_safety_margin_ms: float = 50.0
    direction_flip_cooldown_ms: float = 150.0
    playback_run_accel_hz_per_s: float = 8000.0
    playback_launch_start_hz: float = 60.0
    playback_launch_accel_hz_per_s: float = 5000.0
    playback_launch_crossover_hz: float = 180.0

    def __post_init__(self) -> None:
        if self.connected_motors < 1 or self.connected_motors > 8:
            raise ValueError("connected_motors must be in range [1, 8]")
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


@dataclass(frozen=True)
class CompileReport:
    segments: list[Segment]
    assignments: list[int]
    duplicated_slots: int
    event_groups: list[PlaybackEventGroup] = field(default_factory=list)
    connected_motors: int = 0
    overflow_mode: OverflowMode = "steal_quietest"
    effective_end_s: list[float] = field(default_factory=list)
    stolen_note_count: int = 0
    dropped_note_count: int = 0
    truncated_note_count: int = 0
    zero_length_note_count: int = 0
    adjacent_segments_merged: int = 0
    short_segments_absorbed: int = 0
    silence_gaps_bridged: int = 0
    direction_flip_requested_count: int = 0
    direction_flip_applied_count: int = 0
    direction_flip_suppressed_count: int = 0
    direction_flip_cooldown_suppressed_count: int = 0
    motor_change_count: int = 0
    tight_boundary_warning_count: int = 0
    load_limited_segment_count: int = 0
    load_limited_note_count: int = 0
    playback_plan: PlaybackPlan | None = None


@dataclass(frozen=True)
class ArrangementReport:
    considered_note_count: int = 0
    preserved_note_count: int = 0
    dropped_note_count: int = 0
    truncated_note_count: int = 0
    melody_note_count: int = 0
    preserved_melody_note_count: int = 0
    dropped_melody_note_count: int = 0
    bass_note_count: int = 0
    preserved_bass_note_count: int = 0
    dropped_bass_note_count: int = 0
    inner_note_count: int = 0
    dropped_inner_note_count: int = 0
    octave_retargeted_note_count: int = 0
    coalesced_transition_count: int = 0
    requested_reversal_count: int = 0
    applied_reversal_count: int = 0
    avoided_reversal_count: int = 0
    tight_reversal_window_count: int = 0
    motor_preferred_band_violation_count: int = 0
    motor_resonance_band_hit_count: int = 0
    motor_avoid_band_hit_count: int = 0
    motor_comfort_violation_count: int = 0
    weighted_musical_loss: float = 0.0


@dataclass(frozen=True)
class StreamStatus:
    playing: bool
    stream_open: bool
    stream_end_received: bool
    motor_count: int
    queue_depth: int
    queue_capacity: int
    credits: int
    active_motors: int
    playhead_us: int
    device_time_us: int = 0
    scheduled_start_device_us: int = 0


@dataclass(frozen=True)
class PlaybackStartAnchor:
    scheduled_start_device_us: int
    scheduled_start_host_mono: float
    scheduled_start_unix_ms: int
    host_to_device_offset_us: int
    sync_rtt_us: int
    strategy: str


@dataclass(frozen=True)
class PlaybackMetrics:
    underrun_count: int
    queue_high_water: int
    scheduling_late_max_us: int
    crc_parse_errors: int
    queue_depth: int
    credits: int
    rx_parse_errors: int = 0
    timer_empty_events: int = 0
    timer_restart_count: int = 0
    event_groups_started: int = 0
    scheduler_guard_hits: int = 0
    control_late_max_us: int = 0
    control_overrun_count: int = 0
    wave_period_update_count: int = 0
    motor_start_count: int = 0
    motor_stop_count: int = 0
    flip_restart_count: int = 0
    launch_guard_count: int = 0
    engine_fault_count: int = 0
    engine_fault_mask: int = 0
    engine_fault_attach_count: int = 0
    engine_fault_detach_count: int = 0
    engine_fault_period_count: int = 0
    engine_fault_force_count: int = 0
    engine_fault_timer_count: int = 0
    engine_fault_invalid_change_count: int = 0
    engine_fault_last_reason: int = 0
    engine_fault_last_motor: int = 0
    inferred_pulse_total: int = 0
    measured_pulse_total: int = 0
    measured_pulse_drift_total: int = 0
    measured_pulse_active_mask: int = 0
    exact_position_lost_mask: int = 0
    playback_position_unreliable_mask: int = 0
    playback_signed_position_drift_total: int = 0

    # Legacy accessors keep older UI/tests/scripts from breaking while the
    # active song-playback path finishes moving to continuous-engine terminology.
    @property
    def segments_started(self) -> int:
        return self.event_groups_started

    @property
    def pulse_late_max_us(self) -> int:
        return self.control_late_max_us

    @property
    def pulse_edge_drop_count(self) -> int:
        return self.control_overrun_count

    @property
    def refill_late_max_us(self) -> int:
        return self.control_late_max_us

    @property
    def refill_starvation_count(self) -> int:
        return self.control_overrun_count

    @property
    def playback_slew_clamp_count(self) -> int:
        return self.launch_guard_count

    @property
    def rmt_tx_submit_count(self) -> int:
        return 0

    @property
    def rmt_tx_done_count(self) -> int:
        return 0

    @property
    def rmt_tx_recover_count(self) -> int:
        return 0

    @property
    def rmt_tx_stuck_count(self) -> int:
        return 0

    @property
    def position_lost_mask(self) -> int:
        return self.exact_position_lost_mask

    @property
    def pulse_timebase_rebase_count(self) -> int:
        return 0

    @property
    def pulse_timebase_rebase_lost_us(self) -> int:
        return 0

    @property
    def pulse_target_update_count(self) -> int:
        return 0

    @property
    def pulse_ramp_change_count(self) -> int:
        return 0

    @property
    def pulse_stop_after_ramp_count(self) -> int:
        return 0

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from .config import HostConfig
from .models import PlaybackMetrics, StreamStatus
from .playback_program import PlaybackPlan
from .playback_runner import PlaybackExecutionResult, PlaybackRunner
from .runtime_observers import CallbackPlaybackObserver
from .serial_client import StreamProgress


@dataclass(frozen=True)
class TelemetryCapture:
    execution: PlaybackExecutionResult
    status_trace: tuple[dict[str, Any], ...]
    metrics_trace: tuple[dict[str, Any], ...]
    started_at_monotonic: float
    completed_at_monotonic: float


def _status_row(progress: StreamProgress, status: StreamStatus) -> dict[str, Any]:
    return {
        "sent_segments": progress.sent_segments,
        "total_segments": progress.total_segments,
        "queue_depth": status.queue_depth,
        "queue_capacity": status.queue_capacity,
        "credits": status.credits,
        "active_motors": status.active_motors,
        "playhead_us": status.playhead_us,
        "playing": status.playing,
        "stream_open": status.stream_open,
        "stream_end_received": status.stream_end_received,
    }


def _metrics_row(progress: StreamProgress, metrics: PlaybackMetrics) -> dict[str, Any]:
    return {
        "sent_segments": progress.sent_segments,
        "total_segments": progress.total_segments,
        "queue_depth": metrics.queue_depth,
        "credits": metrics.credits,
        "underrun_count": metrics.underrun_count,
        "queue_high_water": metrics.queue_high_water,
        "scheduling_late_max_us": metrics.scheduling_late_max_us,
        "crc_parse_errors": metrics.crc_parse_errors,
        "rx_parse_errors": metrics.rx_parse_errors,
        "timer_empty_events": metrics.timer_empty_events,
        "timer_restart_count": metrics.timer_restart_count,
        "event_groups_started": metrics.event_groups_started,
        "scheduler_guard_hits": metrics.scheduler_guard_hits,
        "control_late_max_us": metrics.control_late_max_us,
        "control_overrun_count": metrics.control_overrun_count,
        "wave_period_update_count": metrics.wave_period_update_count,
        "motor_start_count": metrics.motor_start_count,
        "motor_stop_count": metrics.motor_stop_count,
        "flip_restart_count": metrics.flip_restart_count,
        "launch_guard_count": metrics.launch_guard_count,
        "engine_fault_count": metrics.engine_fault_count,
        "engine_fault_mask": metrics.engine_fault_mask,
    }


def execute_playback_plan_capture(
    *,
    cfg: HostConfig,
    playback_plan: PlaybackPlan,
    min_note: int,
    max_note: int,
    transpose: int,
    auto_home_enabled: bool | None = None,
) -> TelemetryCapture:
    runner = PlaybackRunner(
        port=cfg.port,
        baudrate=cfg.baudrate,
        timeout_s=cfg.timeout_s,
        write_timeout_s=cfg.write_timeout_s,
        retries=cfg.retries,
    )
    status_trace: list[dict[str, Any]] = []
    metrics_trace: list[dict[str, Any]] = []
    started_at = time.monotonic()
    with runner.session() as session:
        session.validate(
            connected_motors=cfg.connected_motors,
            requires_direction_flip=cfg.flip_direction_on_note_change,
        )
        session.setup(
            motors=cfg.connected_motors,
            idle_mode=cfg.idle_mode,
            min_note=min_note,
            max_note=max_note,
            transpose=transpose,
            playback_run_accel_hz_per_s=cfg.playback_run_accel_hz_per_s,
            playback_launch_start_hz=cfg.playback_launch_start_hz,
            playback_launch_accel_hz_per_s=cfg.playback_launch_accel_hz_per_s,
            playback_launch_crossover_hz=cfg.playback_launch_crossover_hz,
        )

        def _on_telemetry(progress: StreamProgress, status: StreamStatus, latest_metrics: PlaybackMetrics | None) -> None:
            now = time.monotonic()
            status_row = _status_row(progress, status)
            status_row["captured_monotonic_s"] = round(now - started_at, 6)
            status_trace.append(status_row)
            if latest_metrics is not None:
                metrics_row = _metrics_row(progress, latest_metrics)
                metrics_row["captured_monotonic_s"] = round(now - started_at, 6)
                metrics_trace.append(metrics_row)

        observer = CallbackPlaybackObserver(on_telemetry_cb=_on_telemetry)
        execution = session.execute_plan(
            playback_plan=playback_plan,
            lookahead_ms=cfg.lookahead_ms,
            lookahead_strategy=cfg.lookahead_strategy,
            lookahead_min_ms=cfg.lookahead_min_ms,
            lookahead_percentile=cfg.lookahead_percentile,
            lookahead_min_segments=cfg.lookahead_min_segments,
            metrics_poll_interval_s=0.10,
            status_poll_interval_s=0.02,
            scheduled_start_guard_ms=cfg.scheduled_start_guard_ms,
            clock_sync_samples=8,
            startup_countdown_s=0,
            run_countdown=lambda _seconds: None,
            auto_home_enabled=cfg.auto_home if auto_home_enabled is None else auto_home_enabled,
            run_auto_home=lambda client: client.home(
                steps_per_rev=cfg.home_steps_per_rev,
                home_hz=cfg.home_hz,
                start_hz=cfg.home_start_hz,
                accel_hz_per_s=cfg.home_accel_hz_per_s,
            ),
            warmup_step_motion_routines=[],
            warmup_require_home_before_sequence=False,
            warmup_requires_directional_exact_motion=False,
            observer=observer,
        )
    completed_at = time.monotonic()
    return TelemetryCapture(
        execution=execution,
        status_trace=tuple(status_trace),
        metrics_trace=tuple(metrics_trace),
        started_at_monotonic=started_at,
        completed_at_monotonic=completed_at,
    )

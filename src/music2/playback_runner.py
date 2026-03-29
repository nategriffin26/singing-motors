from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import time
from typing import Callable, Iterator

from .models import IdleMode, LookaheadStrategy, PlaybackMetrics, PlaybackStartAnchor
from .playback_program import PlaybackPlan
from .protocol import (
    EXACT_MOTION_FEATURE_DIRECTIONAL_STEP_MOTION,
    FEATURE_FLAG_CONTINUOUS_PLAYBACK_ENGINE,
    FEATURE_FLAG_DIRECTION_FLIP,
    FEATURE_FLAG_HOME,
    FEATURE_FLAG_PLAYBACK_SETUP_PROFILE,
    FEATURE_FLAG_STEP_MOTION,
    FEATURE_FLAG_TIMED_STREAMING,
    StepMotionMotorParams,
)
from .runtime_observers import PlaybackObserver
from .serial_client import SerialClient, SerialClientError, StreamProgress


@dataclass(frozen=True)
class PlaybackDeviceCapabilities:
    protocol_version: int
    feature_flags: int
    exact_motion_flags: int
    queue_capacity: int
    scheduler_tick_us: int
    device_motor_count: int
    playback_motor_count: int

    @property
    def home_supported(self) -> bool:
        return bool(self.feature_flags & FEATURE_FLAG_HOME)

    @property
    def direction_flip_supported(self) -> bool:
        return bool(self.feature_flags & FEATURE_FLAG_DIRECTION_FLIP)

    @property
    def continuous_playback_supported(self) -> bool:
        return bool(self.feature_flags & FEATURE_FLAG_CONTINUOUS_PLAYBACK_ENGINE)

    @property
    def playback_setup_profile_supported(self) -> bool:
        return bool(self.feature_flags & FEATURE_FLAG_PLAYBACK_SETUP_PROFILE)

    @property
    def event_streaming_supported(self) -> bool:
        return self.protocol_version >= 2 and bool(self.feature_flags & FEATURE_FLAG_TIMED_STREAMING)

    @property
    def scheduled_start_supported(self) -> bool:
        return self.protocol_version >= 3

    @property
    def step_motion_supported(self) -> bool:
        return bool(self.feature_flags & FEATURE_FLAG_STEP_MOTION)

    @property
    def exact_direction_step_motion_supported(self) -> bool:
        return bool(self.exact_motion_flags & EXACT_MOTION_FEATURE_DIRECTIONAL_STEP_MOTION)


@dataclass(frozen=True)
class PlaybackExecutionResult:
    metrics: PlaybackMetrics
    queue_capacity: int
    capabilities: PlaybackDeviceCapabilities
    last_progress: StreamProgress | None
    auto_home_error: SerialClientError | None = None
    auto_home_skipped_reason: str | None = None


def auto_home_skip_reason(metrics: PlaybackMetrics) -> str | None:
    if metrics.exact_position_lost_mask != 0:
        return f"exact position tracking unreliable (mask 0x{metrics.exact_position_lost_mask:02X})"
    if metrics.playback_position_unreliable_mask != 0:
        return f"playback position tracking unreliable (mask 0x{metrics.playback_position_unreliable_mask:02X})"
    return None


def attempt_interrupt_auto_home(
    *,
    client: SerialClient,
    capabilities: PlaybackDeviceCapabilities,
    auto_home_enabled: bool,
    run_auto_home: Callable[[SerialClient], None],
) -> tuple[str | None, SerialClientError | None]:
    if not capabilities.home_supported:
        return "firmware lacks HOME support", None
    if not auto_home_enabled:
        return "disabled in config", None
    try:
        metrics = client.metrics()
    except SerialClientError as exc:
        return f"unable to verify position tracking after interrupt ({exc})", None
    skip_reason = auto_home_skip_reason(metrics)
    if skip_reason is not None:
        return skip_reason, None
    try:
        run_auto_home(client)
    except SerialClientError as exc:
        return None, exc
    return None, None


def capabilities_from_hello(hello: dict[str, int]) -> PlaybackDeviceCapabilities:
    queue_capacity = max(1, int(hello.get("queue_capacity", 128)))
    device_motor_count = max(1, int(hello.get("motor_count", 8)))
    playback_motor_count = max(1, int(hello.get("playback_motor_count", device_motor_count)))
    return PlaybackDeviceCapabilities(
        protocol_version=int(hello.get("protocol_version", 0)),
        feature_flags=int(hello.get("feature_flags", 0)),
        exact_motion_flags=int(hello.get("exact_motion_flags", 0)),
        queue_capacity=queue_capacity,
        scheduler_tick_us=int(hello.get("scheduler_tick_us", 25)),
        device_motor_count=device_motor_count,
        playback_motor_count=playback_motor_count,
    )


class PlaybackTransportSession:
    def __init__(self, client: SerialClient, capabilities: PlaybackDeviceCapabilities) -> None:
        self._client = client
        self.capabilities = capabilities

    @property
    def client(self) -> SerialClient:
        return self._client

    def validate(
        self,
        *,
        connected_motors: int,
        requires_direction_flip: bool,
    ) -> None:
        if connected_motors > self.capabilities.device_motor_count:
            raise RuntimeError(
                f"config requests {connected_motors} motors but device reports {self.capabilities.device_motor_count}"
            )
        if connected_motors > self.capabilities.playback_motor_count:
            raise RuntimeError(
                f"song playback requests {connected_motors} motors but firmware continuous-playback path reports "
                f"{self.capabilities.playback_motor_count}"
            )
        if not self.capabilities.event_streaming_supported:
            raise RuntimeError(
                "song playback now requires protocol v2 event streaming; update firmware before running playback"
            )
        if not self.capabilities.continuous_playback_supported:
            raise RuntimeError(
                "song playback now requires the continuous playback engine capability; update firmware before running playback"
            )
        if requires_direction_flip and not self.capabilities.direction_flip_supported:
            raise RuntimeError(
                "config enables flip_direction_on_note_change but firmware does not advertise "
                "direction-flip playback support"
            )
        if not self.capabilities.playback_setup_profile_supported:
            raise RuntimeError(
                "firmware does not advertise playback setup-profile support; update firmware before running playback"
            )

    def setup(
        self,
        *,
        motors: int,
        idle_mode: IdleMode,
        min_note: int,
        max_note: int,
        transpose: int,
        playback_run_accel_hz_per_s: float,
        playback_launch_start_hz: float,
        playback_launch_accel_hz_per_s: float,
        playback_launch_crossover_hz: float,
        speech_assist_control_interval_us: int | None = None,
        speech_assist_release_accel_hz_per_s: float | None = None,
    ) -> None:
        self._client.setup(
            motors=motors,
            idle_mode=idle_mode,
            min_note=min_note,
            max_note=max_note,
            transpose=transpose,
            playback_run_accel_hz_per_s=playback_run_accel_hz_per_s,
            playback_launch_start_hz=playback_launch_start_hz,
            playback_launch_accel_hz_per_s=playback_launch_accel_hz_per_s,
            playback_launch_crossover_hz=playback_launch_crossover_hz,
            speech_assist_control_interval_us=speech_assist_control_interval_us,
            speech_assist_release_accel_hz_per_s=speech_assist_release_accel_hz_per_s,
        )

    def _stream_plan(
        self,
        playback_plan: PlaybackPlan,
        *,
        lookahead_ms: int,
        lookahead_strategy: LookaheadStrategy,
        lookahead_min_ms: int,
        lookahead_percentile: int,
        lookahead_min_segments: int,
        metrics_poll_interval_s: float,
        status_poll_interval_s: float,
        progress_cb: Callable[[StreamProgress], None] | None = None,
        telemetry_cb: Callable[[StreamProgress, object, object], None] | None = None,
        start_anchor_cb: Callable[[PlaybackStartAnchor], None] | None = None,
        scheduled_start_guard_ms: float = 150.0,
        clock_sync_samples: int = 8,
    ) -> None:
        stream_playback_plan = getattr(self._client, "stream_playback_plan", None)
        if callable(stream_playback_plan):
            stream_playback_plan(
                playback_plan,
                lookahead_ms=lookahead_ms,
                lookahead_strategy=lookahead_strategy,
                lookahead_min_ms=lookahead_min_ms,
                lookahead_percentile=lookahead_percentile,
                lookahead_min_segments=lookahead_min_segments,
                progress_cb=progress_cb,
                telemetry_cb=telemetry_cb,
                start_anchor_cb=start_anchor_cb,
                metrics_poll_interval_s=metrics_poll_interval_s,
                status_poll_interval_s=status_poll_interval_s,
                scheduled_start_guard_ms=scheduled_start_guard_ms,
                clock_sync_samples=clock_sync_samples,
            )
            return
        self._client.stream_song_and_play(
            list(playback_plan.event_groups),
            lookahead_ms=lookahead_ms,
            lookahead_strategy=lookahead_strategy,
            lookahead_min_ms=lookahead_min_ms,
            lookahead_percentile=lookahead_percentile,
            lookahead_min_segments=lookahead_min_segments,
            progress_cb=progress_cb,
            telemetry_cb=telemetry_cb,
            start_anchor_cb=start_anchor_cb,
            metrics_poll_interval_s=metrics_poll_interval_s,
            status_poll_interval_s=status_poll_interval_s,
            scheduled_start_guard_ms=scheduled_start_guard_ms,
            clock_sync_samples=clock_sync_samples,
        )

    def execute_plan(
        self,
        *,
        playback_plan: PlaybackPlan,
        lookahead_ms: int,
        lookahead_strategy: LookaheadStrategy,
        lookahead_min_ms: int,
        lookahead_percentile: int,
        lookahead_min_segments: int,
        metrics_poll_interval_s: float,
        status_poll_interval_s: float,
        scheduled_start_guard_ms: float,
        clock_sync_samples: int,
        startup_countdown_s: int,
        run_countdown: Callable[[int], None],
        auto_home_enabled: bool,
        run_auto_home: Callable[[SerialClient], None],
        warmup_step_motion_routines: list[list[StepMotionMotorParams]],
        warmup_require_home_before_sequence: bool,
        warmup_requires_directional_exact_motion: bool,
        observer: PlaybackObserver | None = None,
    ) -> PlaybackExecutionResult:
        observer = observer
        if observer is not None:
            observer.on_phase("ready")

        if startup_countdown_s > 0:
            run_countdown(startup_countdown_s)

        if warmup_step_motion_routines:
            if not self.capabilities.step_motion_supported:
                raise RuntimeError(
                    "configured warmups now require STEP_MOTION exact playback support; update firmware before running"
                )
            if warmup_requires_directional_exact_motion and not self.capabilities.exact_direction_step_motion_supported:
                raise RuntimeError(
                    "configured warmups require directional exact-motion support; update firmware before running"
                )
            if warmup_require_home_before_sequence:
                if not self.capabilities.home_supported:
                    raise RuntimeError(
                        "configured warmups require HOME support to guarantee 12:00 alignment before the sequence"
                    )
                run_auto_home(self._client)
            if observer is not None:
                observer.on_phase("warmup")
                observer.on_playhead_reset()
            for warmup_step_motion in warmup_step_motion_routines:
                self._client.step_motion(warmup_step_motion)
                time.sleep(0.02)
            if observer is not None:
                observer.on_playhead_reset()

        if observer is not None:
            observer.on_phase("playing")

        last_progress: StreamProgress | None = None

        def on_progress(progress: StreamProgress) -> None:
            nonlocal last_progress
            last_progress = progress
            if observer is not None:
                observer.on_progress(progress)

        def on_telemetry(progress: StreamProgress, status, metrics) -> None:
            if observer is not None:
                observer.on_telemetry(progress, status, metrics)

        def on_start_anchor(anchor: PlaybackStartAnchor) -> None:
            if observer is not None:
                observer.on_start_anchor(anchor)

        try:
            self._stream_plan(
                playback_plan,
                lookahead_ms=lookahead_ms,
                lookahead_strategy=lookahead_strategy,
                lookahead_min_ms=lookahead_min_ms,
                lookahead_percentile=lookahead_percentile,
                lookahead_min_segments=lookahead_min_segments,
                progress_cb=on_progress,
                telemetry_cb=on_telemetry if observer is not None else None,
                start_anchor_cb=on_start_anchor if observer is not None else None,
                metrics_poll_interval_s=metrics_poll_interval_s,
                status_poll_interval_s=status_poll_interval_s,
                scheduled_start_guard_ms=scheduled_start_guard_ms,
                clock_sync_samples=clock_sync_samples,
            )
        except KeyboardInterrupt:
            stop_succeeded = False
            try:
                self._client.stop()
                stop_succeeded = True
            except SerialClientError:
                pass
            if stop_succeeded:
                attempt_interrupt_auto_home(
                    client=self._client,
                    capabilities=self.capabilities,
                    auto_home_enabled=auto_home_enabled,
                    run_auto_home=run_auto_home,
                )
            raise

        try:
            self._client.stop()
        except SerialClientError:
            pass

        metrics = self._client.metrics()

        auto_home_error: SerialClientError | None = None
        auto_home_skipped_reason: str | None = None
        if self.capabilities.home_supported and auto_home_enabled:
            auto_home_skipped_reason = auto_home_skip_reason(metrics)
            if auto_home_skipped_reason is None:
                try:
                    run_auto_home(self._client)
                except SerialClientError as exc:
                    auto_home_error = exc
            else:
                auto_home_error = None

        if observer is not None:
            observer.on_complete(metrics, last_progress)

        return PlaybackExecutionResult(
            metrics=metrics,
            queue_capacity=self.capabilities.queue_capacity,
            capabilities=self.capabilities,
            last_progress=last_progress,
            auto_home_error=auto_home_error,
            auto_home_skipped_reason=auto_home_skipped_reason,
        )


class PlaybackRunner:
    def __init__(
        self,
        *,
        port: str,
        baudrate: int,
        timeout_s: float,
        write_timeout_s: float,
        retries: int,
        client_cls: type[SerialClient] = SerialClient,
    ) -> None:
        self._port = port
        self._baudrate = baudrate
        self._timeout_s = timeout_s
        self._write_timeout_s = write_timeout_s
        self._retries = retries
        self._client_cls = client_cls

    @contextmanager
    def session(self) -> Iterator[PlaybackTransportSession]:
        with self._client_cls(
            port=self._port,
            baudrate=self._baudrate,
            timeout_s=self._timeout_s,
            write_timeout_s=self._write_timeout_s,
            retries=self._retries,
        ) as client:
            yield PlaybackTransportSession(client, capabilities_from_hello(client.hello()))

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal, Protocol

from .config import HostConfig
from .models import CompileReport, MidiAnalysisReport, PlaybackMetrics, PlaybackStartAnchor, StreamStatus
from .playback_program import PlaybackProgram
from .serial_client import StreamProgress
from .ui import PlaybackSyncEngine, TelemetryHub, UIServer
from .viewer_theme import THEME_IDS


class PlaybackObserver(Protocol):
    def on_phase(self, phase: str) -> None: ...

    def on_playhead_reset(self) -> None: ...

    def on_start_anchor(self, anchor: PlaybackStartAnchor) -> None: ...

    def on_progress(self, progress: StreamProgress) -> None: ...

    def on_telemetry(
        self,
        progress: StreamProgress,
        status: StreamStatus,
        metrics: PlaybackMetrics | None,
    ) -> None: ...

    def on_complete(
        self,
        metrics: PlaybackMetrics,
        last_progress: StreamProgress | None,
    ) -> None: ...

    def close(self) -> None: ...


@dataclass
class CallbackPlaybackObserver:
    on_phase_cb: Callable[[str], None] | None = None
    on_playhead_reset_cb: Callable[[], None] | None = None
    on_start_anchor_cb: Callable[[PlaybackStartAnchor], None] | None = None
    on_progress_cb: Callable[[StreamProgress], None] | None = None
    on_telemetry_cb: Callable[[StreamProgress, StreamStatus, PlaybackMetrics | None], None] | None = None
    on_complete_cb: Callable[[PlaybackMetrics, StreamProgress | None], None] | None = None
    close_cb: Callable[[], None] | None = None

    def on_phase(self, phase: str) -> None:
        if self.on_phase_cb is not None:
            self.on_phase_cb(phase)

    def on_playhead_reset(self) -> None:
        if self.on_playhead_reset_cb is not None:
            self.on_playhead_reset_cb()

    def on_start_anchor(self, anchor: PlaybackStartAnchor) -> None:
        if self.on_start_anchor_cb is not None:
            self.on_start_anchor_cb(anchor)

    def on_progress(self, progress: StreamProgress) -> None:
        if self.on_progress_cb is not None:
            self.on_progress_cb(progress)

    def on_telemetry(
        self,
        progress: StreamProgress,
        status: StreamStatus,
        metrics: PlaybackMetrics | None,
    ) -> None:
        if self.on_telemetry_cb is not None:
            self.on_telemetry_cb(progress, status, metrics)

    def on_complete(
        self,
        metrics: PlaybackMetrics,
        last_progress: StreamProgress | None,
    ) -> None:
        if self.on_complete_cb is not None:
            self.on_complete_cb(metrics, last_progress)

    def close(self) -> None:
        if self.close_cb is not None:
            self.close_cb()


class CompositePlaybackObserver:
    def __init__(self, *observers: PlaybackObserver | None) -> None:
        self._observers = [observer for observer in observers if observer is not None]

    def on_phase(self, phase: str) -> None:
        for observer in self._observers:
            observer.on_phase(phase)

    def on_playhead_reset(self) -> None:
        for observer in self._observers:
            observer.on_playhead_reset()

    def on_start_anchor(self, anchor: PlaybackStartAnchor) -> None:
        for observer in self._observers:
            observer.on_start_anchor(anchor)

    def on_progress(self, progress: StreamProgress) -> None:
        for observer in self._observers:
            observer.on_progress(progress)

    def on_telemetry(
        self,
        progress: StreamProgress,
        status: StreamStatus,
        metrics: PlaybackMetrics | None,
    ) -> None:
        for observer in self._observers:
            observer.on_telemetry(progress, status, metrics)

    def on_complete(
        self,
        metrics: PlaybackMetrics,
        last_progress: StreamProgress | None,
    ) -> None:
        for observer in self._observers:
            observer.on_complete(metrics, last_progress)

    def close(self) -> None:
        for observer in reversed(self._observers):
            observer.close()


class DashboardObserver:
    def __init__(
        self,
        *,
        hub: TelemetryHub,
        sync: PlaybackSyncEngine,
        server: UIServer,
        render_mode: Literal["live", "prerender_30fps"],
    ) -> None:
        self._hub = hub
        self._sync = sync
        self._server = server
        self._render_mode = render_mode
        self._live_frames_enabled = render_mode == "live"
        self._session = sync.viewer_session(render_mode=render_mode, fps=30, timeline_ready=render_mode == "prerender_30fps")

    @classmethod
    def start(
        cls,
        *,
        cfg: HostConfig,
        analysis: MidiAnalysisReport,
        compiled: CompileReport,
        playback_program: PlaybackProgram,
        midi_path: Path,
        queue_capacity: int,
        scheduler_tick_us: int,
        ws_poll_interval_s: float,
        ui_render_mode: str,
        prerender_progress: Callable[[int, int], None] | None = None,
    ) -> "DashboardObserver":
        if not getattr(cfg, "ui_static_dir", None):
            raise RuntimeError("UI static directory is not configured")

        static_dir = Path(cfg.ui_static_dir).expanduser().resolve()
        if not static_dir.exists():
            raise RuntimeError(
                "UI assets not found at "
                f"{static_dir}. Build them with: cd ui/dashboard && npm install && npm run build"
            )

        hub = TelemetryHub()
        sync = PlaybackSyncEngine(
            analysis=analysis,
            compiled=compiled,
            playback_program=playback_program,
            midi_path=midi_path,
            queue_capacity=queue_capacity,
            scheduler_tick_us=scheduler_tick_us,
        )

        session_render_mode: Literal["live", "prerender_30fps"] = "live"
        timeline_ready = False
        if ui_render_mode == "prerender-30":
            hub.set_timeline(sync.viewer_timeline(fps=30, progress_callback=prerender_progress))
            timeline_ready = True
            session_render_mode = "prerender_30fps"

        session = sync.viewer_session(
            render_mode=session_render_mode,
            fps=30,
            timeline_ready=timeline_ready,
            theme_default=cfg.ui_theme,
            themes_available=list(THEME_IDS),
            color_mode_default=cfg.ui_color_mode,
            color_modes_available=list(cfg.ui_color_modes),
            show_controls=cfg.ui_show_controls,
            sync_offset_ms=cfg.ui_sync_offset_ms,
        )
        hub.set_session(session)
        server = UIServer(
            hub=hub,
            host=cfg.ui_host,
            port=cfg.ui_port,
            static_dir=static_dir,
            ws_poll_interval_s=ws_poll_interval_s,
        )
        server.start()
        observer = cls(
            hub=hub,
            sync=sync,
            server=server,
            render_mode=session_render_mode,
        )
        observer._session = session
        return observer

    @property
    def origin(self) -> str:
        return self._server.origin

    @property
    def render_mode(self) -> Literal["live", "prerender_30fps"]:
        return self._render_mode

    def prime(
        self,
        *,
        status: StreamStatus,
        metrics: PlaybackMetrics | None,
        total_segments: int,
    ) -> None:
        if not self._live_frames_enabled:
            return
        progress = StreamProgress(
            sent_segments=0,
            total_segments=total_segments,
            queue_depth=status.queue_depth,
            credits=status.credits,
            active_motors=status.active_motors,
            playhead_us=status.playhead_us,
        )
        self.on_telemetry(progress, status, metrics)

    def on_phase(self, phase: str) -> None:
        if phase in {"warmup", "ready"}:
            self._hub.set_playhead_us(0)

    def on_playhead_reset(self) -> None:
        self._hub.set_playhead_us(0)

    def on_start_anchor(self, anchor: PlaybackStartAnchor) -> None:
        self._session = {
            **self._session,
            "sync_strategy": anchor.strategy,
            "scheduled_start_unix_ms": anchor.scheduled_start_unix_ms,
            "scheduled_start_device_us": anchor.scheduled_start_device_us,
            "drift_rebase_threshold_ms": 40.0,
        }
        self._hub.set_session(self._session)

    def on_progress(self, progress: StreamProgress) -> None:
        _ = progress

    def on_telemetry(
        self,
        progress: StreamProgress,
        status: StreamStatus,
        metrics: PlaybackMetrics | None,
    ) -> None:
        self._hub.set_playhead_us(status.playhead_us)
        if not self._live_frames_enabled:
            return
        self._hub.publish_frame(
            self._sync.frame(
                playhead_us=status.playhead_us,
                sent_segments=progress.sent_segments,
                total_segments=progress.total_segments,
                status=status,
                metrics=metrics,
            )
        )

    def on_complete(
        self,
        metrics: PlaybackMetrics,
        last_progress: StreamProgress | None,
    ) -> None:
        _ = metrics
        _ = last_progress

    def publish_final(
        self,
        *,
        status: StreamStatus,
        metrics: PlaybackMetrics,
        last_progress: StreamProgress | None,
    ) -> None:
        if not self._live_frames_enabled or last_progress is None:
            return
        self.on_telemetry(last_progress, status, metrics)

    def close(self) -> None:
        self._server.stop()

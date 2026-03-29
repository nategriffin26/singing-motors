from __future__ import annotations

import pytest

from music2.models import PlaybackMetrics
from music2.playback_program import PlaybackPlan
from music2.playback_runner import PlaybackDeviceCapabilities, PlaybackTransportSession
from music2.protocol import FEATURE_FLAG_HOME


def test_execute_plan_interrupt_skips_auto_home_when_tracking_is_unreliable() -> None:
    calls: list[str] = []

    class _FakeClient:
        def stream_song_and_play(self, *_args, **_kwargs) -> None:
            calls.append("stream_song_and_play")
            raise KeyboardInterrupt

        def stop(self) -> None:
            calls.append("stop")

        def metrics(self) -> PlaybackMetrics:
            calls.append("metrics")
            return PlaybackMetrics(
                underrun_count=0,
                queue_high_water=0,
                scheduling_late_max_us=0,
                crc_parse_errors=0,
                queue_depth=0,
                credits=128,
                exact_position_lost_mask=0x01,
            )

        def home(self) -> None:
            calls.append("home")

    session = PlaybackTransportSession(
        _FakeClient(),
        PlaybackDeviceCapabilities(
            protocol_version=2,
            feature_flags=FEATURE_FLAG_HOME,
            exact_motion_flags=0,
            queue_capacity=128,
            scheduler_tick_us=10,
            device_motor_count=8,
            playback_motor_count=6,
        ),
    )
    playback_plan = PlaybackPlan(
        plan_id="test",
        display_name="test",
        event_groups=(),
        shadow_segments=(),
        connected_motors=6,
        overflow_mode="steal_quietest",
        motor_change_count=0,
    )

    with pytest.raises(KeyboardInterrupt):
        session.execute_plan(
            playback_plan=playback_plan,
            lookahead_ms=10,
            lookahead_strategy="fixed",
            lookahead_min_ms=10,
            lookahead_percentile=90,
            lookahead_min_segments=1,
            metrics_poll_interval_s=0.1,
            status_poll_interval_s=0.1,
            scheduled_start_guard_ms=150.0,
            clock_sync_samples=8,
            startup_countdown_s=0,
            run_countdown=lambda _seconds: None,
            auto_home_enabled=True,
            run_auto_home=lambda client: client.home(),
            warmup_step_motion_routines=[],
            warmup_require_home_before_sequence=False,
            warmup_requires_directional_exact_motion=False,
            observer=None,
        )

    assert calls == ["stream_song_and_play", "stop", "metrics"]

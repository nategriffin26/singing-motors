from __future__ import annotations

from fastapi.testclient import TestClient

from music2.ui.hub import TelemetryHub
from music2.ui.server import create_app


def _sample_session() -> dict[str, object]:
    return {
        "song": {
            "file_name": "demo.mid",
            "duration_us": 500_000,
            "duration_s": 0.5,
            "note_count": 1,
            "max_polyphony": 1,
            "transpose_semitones": 0,
        },
        "note_range": {"min_note": 60, "max_note": 64},
        "connected_motors": 8,
        "lanes": 5,
        "allocation": {
            "policy": "steal_quietest",
            "stolen_notes": 0,
            "dropped_notes": 0,
            "playable_notes": 1,
            "retained_ratio": 1.0,
        },
        "window": {"history_us": 350_000, "lookahead_us": 3_000_000},
        "render_mode": "prerender_30fps",
        "fps": 30,
        "timeline_version": "v1",
        "timeline_ready": True,
        "timeline_url": "/api/viewer/timeline",
        "theme_default": "neon",
        "themes_available": ["neon", "retro", "minimal"],
        "color_mode_default": "monochrome_accent",
        "color_modes_available": ["monochrome_accent", "channel", "octave_bands"],
        "show_controls": True,
        "sync_offset_ms": 0.0,
        "generated_at_unix_ms": 1,
    }


def _sample_timeline() -> dict[str, object]:
    return {
        "version": "v1",
        "fps": 30,
        "duration_us": 500_000,
        "frame_count": 2,
        "note_range": {"min_note": 60, "max_note": 64},
        "bars_static": [
            {
                "id": 1,
                "pitch": 60,
                "start_us": 90_000,
                "end_us": 170_000,
                "velocity": 100,
                "frequency_hz": 261.6,
                "channel": 1,
                "motor_slot": 0,
            }
        ],
        "frames": [
            {
                "playhead_us": 0,
                "window_start_us": 0,
                "window_end_us": 500_000,
                "active_note_ids": [],
                "visible_bar_ids": [1],
                "beat_markers_us": [0, 250_000, 500_000],
            },
            {
                "playhead_us": 33_333,
                "window_start_us": 0,
                "window_end_us": 500_000,
                "active_note_ids": [1],
                "visible_bar_ids": [1],
                "beat_markers_us": [0, 250_000, 500_000],
            },
        ],
        "style_hints": {"quality": "performance", "allow_glow": False, "dpr_cap": 1.25},
        "generated_at_unix_ms": 1,
    }


def _sample_frame(playhead_us: int = 123_000) -> dict[str, object]:
    return {
        "type": "frame",
        "seq": 0,
        "playhead_us": playhead_us,
        "window_start_us": 0,
        "window_end_us": 500_000,
        "duration_us": 500_000,
        "bars": [
            {
                "id": 1,
                "pitch": 60,
                "start_us": 90_000,
                "end_us": 170_000,
                "velocity": 100,
                "frequency_hz": 261.6,
                "channel": 1,
                "motor_slot": 0,
                "active": True,
            }
        ],
        "active_note_ids": [1],
        "beat_markers_us": [0, 250_000, 500_000],
        "state": {
            "playing": True,
            "stream_open": True,
            "stream_end_received": False,
        },
    }


def test_api_health_session_and_viewer_session(tmp_path) -> None:
    static_dir = tmp_path / "dist"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<html>ok</html>", encoding="utf-8")

    hub = TelemetryHub()
    hub.set_session(_sample_session())
    hub.set_timeline(_sample_timeline())
    hub.publish_frame(_sample_frame())

    app = create_app(hub, static_dir=static_dir)
    client = TestClient(app)

    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert health.json()["session_active"] is True

    session = client.get("/api/session")
    assert session.status_code == 200
    session_payload = session.json()
    assert session_payload["session"]["song"]["file_name"] == "demo.mid"
    assert session_payload["frame"]["playhead_us"] == 123_000
    assert session_payload["snapshot"]["playhead_us"] == 123_000

    viewer_session = client.get("/api/viewer/session")
    assert viewer_session.status_code == 200
    assert viewer_session.json()["song"]["file_name"] == "demo.mid"
    assert viewer_session.json()["color_mode_default"] == "monochrome_accent"
    assert viewer_session.json()["show_controls"] is True

    timeline = client.get("/api/viewer/timeline")
    assert timeline.status_code == 200
    timeline_payload = timeline.json()
    assert timeline_payload["version"] == "v1"
    assert timeline_payload["frame_count"] == 2
    assert timeline_payload["frames"][1]["active_note_ids"] == [1]


def test_ws_viewer_receives_hello_and_frames(tmp_path) -> None:
    static_dir = tmp_path / "dist"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<html>ok</html>", encoding="utf-8")

    hub = TelemetryHub()
    hub.set_session(_sample_session())
    app = create_app(hub, static_dir=static_dir)
    client = TestClient(app)

    with client.websocket_connect("/ws/viewer") as websocket:
        hello = websocket.receive_json()
        assert hello["type"] == "hello"
        assert hello["protocol"] == "viewer.v1"
        assert hello["session"]["song"]["file_name"] == "demo.mid"

        hub.publish_frame(_sample_frame(playhead_us=250_000))
        update = websocket.receive_json()
        assert update["type"] == "frame"
        assert update["playhead_us"] == 250_000
        assert update["seq"] >= 1


def test_ws_telemetry_alias_also_streams_frames(tmp_path) -> None:
    static_dir = tmp_path / "dist"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<html>ok</html>", encoding="utf-8")

    hub = TelemetryHub()
    app = create_app(hub, static_dir=static_dir)
    client = TestClient(app)

    with client.websocket_connect("/ws/telemetry") as websocket:
        hello = websocket.receive_json()
        assert hello["type"] == "hello"
        hub.publish_frame(_sample_frame(playhead_us=111_000))
        frame = websocket.receive_json()
        assert frame["type"] == "frame"
        assert frame["playhead_us"] == 111_000


def test_api_viewer_timeline_not_ready_returns_503(tmp_path) -> None:
    static_dir = tmp_path / "dist"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<html>ok</html>", encoding="utf-8")

    hub = TelemetryHub()
    app = create_app(hub, static_dir=static_dir)
    client = TestClient(app)

    response = client.get("/api/viewer/timeline")
    assert response.status_code == 503

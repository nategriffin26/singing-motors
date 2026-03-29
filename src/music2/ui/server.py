from __future__ import annotations

import asyncio
from pathlib import Path
import threading
import time

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from .hub import TelemetryHub
from .types import ViewerFrame, ViewerHello

_DEFAULT_WS_POLL_INTERVAL_S = 0.02
_DEFAULT_WS_HEARTBEAT_INTERVAL_S = 1.0


def create_app(
    hub: TelemetryHub,
    *,
    static_dir: Path,
    ws_poll_interval_s: float = _DEFAULT_WS_POLL_INTERVAL_S,
    ws_heartbeat_interval_s: float = _DEFAULT_WS_HEARTBEAT_INTERVAL_S,
) -> FastAPI:
    app = FastAPI(title="music2 UI", version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(GZipMiddleware, minimum_size=512)

    @app.get("/api/health")
    async def api_health() -> dict[str, int | bool | str]:
        return hub.health()

    @app.get("/api/session")
    async def api_session() -> dict[str, object | None]:
        bundle = hub.session_bundle()
        return {"session": bundle["session"], "frame": bundle["frame"], "snapshot": bundle["frame"]}

    @app.get("/api/viewer/session")
    async def api_viewer_session() -> object | None:
        return hub.session_bundle()["session"]

    @app.get("/api/viewer/playhead")
    async def api_viewer_playhead() -> dict[str, int]:
        return {"playhead_us": hub.get_playhead_us()}

    @app.get("/api/viewer/timeline")
    async def api_viewer_timeline() -> object:
        timeline = hub.session_bundle()["timeline"]
        if timeline is None:
            raise HTTPException(status_code=503, detail="viewer timeline is not ready")
        return timeline

    async def _ws_stream(websocket: WebSocket) -> None:
        await websocket.accept()
        hub.register_ws_client()
        try:
            bundle = hub.session_bundle()
            hello: ViewerHello = {
                "type": "hello",
                "protocol": "viewer.v1",
                "server_time_unix_ms": int(time.time() * 1000),
                "session": bundle["session"],
                "frame": bundle["frame"],
            }
            await websocket.send_json(hello)

            last_seq = bundle["frame"]["seq"] if bundle["frame"] else 0
            last_send_at = time.monotonic()
            while True:
                await asyncio.sleep(ws_poll_interval_s)
                latest = hub.session_bundle()["frame"]
                if latest is None or latest["seq"] == last_seq:
                    # Keep connection warm through mobile/Tailscale links.
                    if (time.monotonic() - last_send_at) >= ws_heartbeat_interval_s:
                        await websocket.send_json(
                            {
                                "type": "heartbeat",
                                "unix_ms": int(time.time() * 1000),
                            }
                        )
                        last_send_at = time.monotonic()
                    continue
                frame: ViewerFrame = latest
                await websocket.send_json(frame)
                last_seq = frame["seq"]
                last_send_at = time.monotonic()
        except WebSocketDisconnect:
            return
        finally:
            hub.unregister_ws_client()

    @app.websocket("/ws/telemetry")
    async def ws_telemetry(websocket: WebSocket) -> None:
        await _ws_stream(websocket)

    @app.websocket("/ws/viewer")
    async def ws_viewer(websocket: WebSocket) -> None:
        await _ws_stream(websocket)

    if static_dir.exists():
        app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="dashboard")
    else:

        @app.get("/")
        async def missing_assets() -> PlainTextResponse:
            return PlainTextResponse(
                "UI assets not found. Build frontend with: cd ui/dashboard && npm install && npm run build",
                status_code=503,
            )

    return app


class UIServer:
    def __init__(
        self,
        *,
        hub: TelemetryHub,
        host: str,
        port: int,
        static_dir: Path,
        ws_poll_interval_s: float = _DEFAULT_WS_POLL_INTERVAL_S,
    ) -> None:
        self._hub = hub
        self._host = host
        self._port = port
        self._static_dir = static_dir
        self._ws_poll_interval_s = max(0.005, ws_poll_interval_s)
        self._thread: threading.Thread | None = None
        self._server: uvicorn.Server | None = None

    @property
    def origin(self) -> str:
        return f"http://{self._host}:{self._port}"

    def start(self, *, startup_timeout_s: float = 5.0) -> None:
        if self._thread is not None and self._thread.is_alive():
            return

        app = create_app(
            self._hub,
            static_dir=self._static_dir,
            ws_poll_interval_s=self._ws_poll_interval_s,
        )
        config = uvicorn.Config(
            app,
            host=self._host,
            port=self._port,
            log_level="warning",
            access_log=False,
            ws_ping_interval=30.0,
            ws_ping_timeout=120.0,
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, name="music2-ui", daemon=True)
        self._thread.start()

        deadline = time.monotonic() + startup_timeout_s
        while time.monotonic() < deadline:
            if self._server.started:
                return
            if not self._thread.is_alive():
                break
            time.sleep(0.05)

        raise RuntimeError(f"failed to start UI server at {self.origin}")

    def stop(self, *, timeout_s: float = 5.0) -> None:
        if self._server is None:
            return
        self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=timeout_s)
        self._server = None
        self._thread = None

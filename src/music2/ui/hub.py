from __future__ import annotations

import copy
import threading
from typing import TypedDict

from .types import ViewerFrame, ViewerSession, ViewerTimeline


class SessionBundle(TypedDict):
    session: ViewerSession | None
    frame: ViewerFrame | None
    timeline: ViewerTimeline | None


class TelemetryHub:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._session: ViewerSession | None = None
        self._frame: ViewerFrame | None = None
        self._timeline: ViewerTimeline | None = None
        self._playhead_us: int = 0
        self._seq = 0
        self._ws_clients = 0

    def set_session(self, session: ViewerSession) -> None:
        with self._lock:
            self._session = copy.deepcopy(session)

    def set_playhead_us(self, playhead_us: int) -> None:
        with self._lock:
            self._playhead_us = max(0, int(playhead_us))

    def get_playhead_us(self) -> int:
        with self._lock:
            return self._playhead_us

    def clear_session(self) -> None:
        with self._lock:
            self._session = None
            self._frame = None
            self._timeline = None
            self._playhead_us = 0
            self._seq = 0

    def set_timeline(self, timeline: ViewerTimeline) -> None:
        with self._lock:
            self._timeline = copy.deepcopy(timeline)

    def publish_frame(self, frame: ViewerFrame) -> ViewerFrame:
        with self._lock:
            self._seq += 1
            payload = copy.deepcopy(frame)
            payload["type"] = "frame"
            payload["seq"] = self._seq
            self._frame = payload
            return copy.deepcopy(payload)

    # Backward-compatible alias for prior caller names.
    def publish_snapshot(self, snapshot: ViewerFrame) -> ViewerFrame:
        return self.publish_frame(snapshot)

    def session_bundle(self) -> SessionBundle:
        with self._lock:
            return SessionBundle(
                session=copy.deepcopy(self._session),
                frame=copy.deepcopy(self._frame),
                timeline=copy.deepcopy(self._timeline),
            )

    def register_ws_client(self) -> None:
        with self._lock:
            self._ws_clients += 1

    def unregister_ws_client(self) -> None:
        with self._lock:
            self._ws_clients = max(0, self._ws_clients - 1)

    def health(self) -> dict[str, int | bool | str]:
        with self._lock:
            return {
                "status": "ok",
                "session_active": self._session is not None,
                "ws_clients": self._ws_clients,
                "last_seq": self._seq,
            }

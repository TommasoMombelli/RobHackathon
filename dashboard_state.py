"""Shared in-process state for the Streamlit drone dashboard.

main.py is NOT modified. Instead:
  - Frames during mission  : poll frame.png (main.navigate_to_helmet saves it each step)
  - Frames in idle preview : call main.drone.get_latest_frame() from a background thread
  - Event log              : StdoutTee intercepts all print() calls from main's functions

Usage in app.py:
    from dashboard_state import STATE, StdoutTee
    import sys; sys.stdout = StdoutTee(STATE, sys.stdout)
    STATE.publish_frame_bgr(bgr_array)   # preview thread
    STATE.publish_frame_png("frame.png") # frame-watcher thread
    snap = STATE.snapshot()
    jpeg, seq = STATE.get_frame()
"""

import collections
import os
import sys
import threading
import time

import cv2
import numpy as np


# ------------------------------------------------------------------ stdout tee

class StdoutTee:
    """Tee sys.stdout to the terminal AND the STATE event log.

    Install once at startup:
        sys.stdout = StdoutTee(STATE, sys.stdout)
    All print() calls from main.py's functions then appear both in the
    terminal and in the dashboard event log.
    """

    def __init__(self, state: "DashboardState", original):
        self._state = state
        self._original = original
        self._buf = ""

    def write(self, text: str):
        self._original.write(text)
        self._buf += text
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            line = line.strip()
            if line:
                low = line.lower()
                if any(w in low for w in ("error", "fail", "crash", "✖")):
                    level = "error"
                elif any(w in low for w in ("⚠", "warn", "lost", "gave up")):
                    level = "warn"
                else:
                    level = "info"
                self._state.log(line, level)

    def flush(self):
        self._original.flush()

    def __getattr__(self, name):
        return getattr(self._original, name)


# ------------------------------------------------------------------ state

class DashboardState:
    def __init__(self):
        self._lock = threading.Lock()

        # --- frame channel ---
        self._frame_jpeg: bytes | None = None
        self._frame_seq: int = 0
        self._last_frame_ts: float | None = None
        self.fps: float = 0.0

        # --- mission control ---
        self.running: bool = False
        self.mode: str = "idle"       # idle | preview | running | LANDED | ERROR
        self.nav_thread: threading.Thread | None = None

        # --- event log ---
        self._events: collections.deque = collections.deque(maxlen=200)
        self._event_seq: int = 0

        # --- snapshot ring buffer ---
        self._snapshots: collections.deque = collections.deque(maxlen=10)

    # ---------------------------------------------------------------- frames

    def publish_frame_bgr(self, bgr: np.ndarray) -> None:
        """Encode a BGR numpy array to JPEG and store it."""
        ok, buf = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, 72])
        if not ok:
            return
        self._store_jpeg(buf.tobytes())

    def publish_frame_png(self, path: str) -> bool:
        """Read an image file from disk and store it. Returns True on success."""
        try:
            bgr = cv2.imread(path)
            if bgr is None:
                return False
            self.publish_frame_bgr(bgr)
            return True
        except Exception:
            return False

    def _store_jpeg(self, jpeg: bytes) -> None:
        now = time.time()
        with self._lock:
            self._frame_jpeg = jpeg
            self._frame_seq += 1
            if self._last_frame_ts is not None:
                dt = now - self._last_frame_ts
                if dt > 0:
                    self.fps = 0.7 * self.fps + 0.3 * (1.0 / dt)
            self._last_frame_ts = now
            self._snapshots.append(jpeg)

    def get_frame(self) -> tuple[bytes | None, int]:
        """Return (jpeg_bytes, seq). seq increments on each new frame."""
        with self._lock:
            return self._frame_jpeg, self._frame_seq

    def is_stale(self, timeout: float = 3.0) -> bool:
        """True if no frame has arrived within `timeout` seconds."""
        with self._lock:
            if self._last_frame_ts is None:
                return True
            return (time.time() - self._last_frame_ts) > timeout

    # ---------------------------------------------------------------- state

    def update(self, **kwargs) -> None:
        with self._lock:
            for k, v in kwargs.items():
                if hasattr(self, k):
                    setattr(self, k, v)

    def snapshot(self) -> dict:
        with self._lock:
            stale = (self._last_frame_ts is None or
                     (time.time() - self._last_frame_ts) > 3.0)
            return {
                "mode": self.mode,
                "running": self.running,
                "fps": round(self.fps, 1),
                "event_seq": self._event_seq,
                "stale": stale,
            }

    # ---------------------------------------------------------------- snapshot

    def save_snapshot(self, path: str) -> bool:
        """Write the current frame JPEG to `path`."""
        with self._lock:
            jpeg = self._frame_jpeg
        if jpeg is None:
            return False
        with open(path, "wb") as f:
            f.write(jpeg)
        return True

    def get_latest_snapshot_bytes(self) -> bytes | None:
        with self._lock:
            return self._snapshots[-1] if self._snapshots else None

    # ---------------------------------------------------------------- log

    def log(self, msg: str, level: str = "info") -> None:
        with self._lock:
            self._event_seq += 1
            self._events.append((time.time(), level, msg))

    def recent_log_lines(self, n: int = 40) -> list[str]:
        with self._lock:
            entries = list(self._events)[-n:]
        lines = []
        for ts, level, msg in entries:
            t = time.strftime("%H:%M:%S", time.localtime(ts))
            prefix = {"info": "·", "warn": "⚠", "error": "✖"}.get(level, "·")
            lines.append(f"{t} {prefix} {msg}")
        return lines


STATE = DashboardState()

"""Transfer speed and time-left estimates."""

from __future__ import annotations

import json
import time
from collections import deque
from typing import Callable


class Aborted(Exception):
    """The user asked for an immediate pause during a download or an upload."""


class Meter:
    """Speed over a sliding window, so that a short stall or burst does not make the estimate jump."""

    def __init__(self, total: int | None = None, clock: Callable[[], float] = time.monotonic, window: float = 10.0):
        self.total = total
        self.clock = clock
        self.window = window
        self.started = clock()
        self.done = 0
        self._samples: deque[tuple[float, int]] = deque([(self.started, 0)])

    def update(self, done: int, total: int | None = None) -> None:
        now = self.clock()
        if total is not None:
            self.total = total
        self.done = done
        self._samples.append((now, done))
        while len(self._samples) > 2 and now - self._samples[0][0] > self.window:
            self._samples.popleft()

    @property
    def speed(self) -> float | None:
        (t0, b0), (t1, b1) = self._samples[0], self._samples[-1]
        return (b1 - b0) / (t1 - t0) if t1 - t0 >= 1 else None

    @property
    def average(self) -> float | None:
        elapsed = self.clock() - self.started
        return self.done / elapsed if elapsed >= 1 and self.done else None

    @property
    def eta(self) -> float | None:
        speed = self.speed
        if not self.total or not speed:
            return None
        return max(0.0, (self.total - self.done) / speed)


def fmt_size(num: float | None) -> str:
    if not num:
        return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if num < 1024:
            return f"{num:.0f} {unit}" if unit in ("B", "KB") else f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} TB"


def fmt_speed(bytes_per_second: float | None) -> str:
    if not bytes_per_second:
        return "?"
    # Internet plans are sold in Mbit/s, so show that next to the file speed.
    return f"{fmt_size(bytes_per_second)}/s ({bytes_per_second * 8 / 1e6:.1f} Mbit/s)"


def fmt_eta(seconds: float | None) -> str:
    if seconds is None:
        return "time left unknown"
    if seconds < 60:
        return "less than 1 min left"
    minutes = round(seconds / 60)
    if minutes < 60:
        return f"about {minutes} min left"
    hours, minutes = divmod(minutes, 60)
    return f"about {hours} h {minutes} min left"


def fmt_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f} s"
    minutes = round(seconds / 60)
    if minutes < 60:
        return f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes} min"


# A transfer that has not reported for this long belongs to a worker that stopped.
STALE_TRANSFER_SECONDS = 60


def current_transfer(db) -> dict | None:
    """The download or upload that runs now, as the worker last reported it."""
    raw = db.get_setting("transfer") or ""
    if not raw:
        return None
    transfer = json.loads(raw)
    if time.time() - transfer.get("updated_at", 0) > STALE_TRANSFER_SECONDS:
        return None
    return transfer

"""Stop the computer from going to sleep while the worker runs."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001


class KeepAwake:
    """Windows and macOS only. On Linux, start the worker with `systemd-inhibit ytia run`."""

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None

    def __enter__(self) -> "KeepAwake":
        if os.name == "nt":
            import ctypes

            ctypes.windll.kernel32.SetThreadExecutionState(_ES_CONTINUOUS | _ES_SYSTEM_REQUIRED)
        elif sys.platform == "darwin" and shutil.which("caffeinate"):
            self._proc = subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])
        return self

    def __exit__(self, *exc: object) -> None:
        if os.name == "nt":
            import ctypes

            ctypes.windll.kernel32.SetThreadExecutionState(_ES_CONTINUOUS)
        elif self._proc is not None:
            self._proc.terminate()

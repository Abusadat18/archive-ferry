"""A file lock that allows only one worker. The OS releases it if the process dies."""

from __future__ import annotations

import os
from pathlib import Path
from typing import IO


class WorkerLock:
    def __init__(self, path: Path):
        self.path = path
        self._fh: IO | None = None

    def acquire(self) -> bool:
        fh = open(self.path, "a+")
        try:
            if os.name == "nt":
                import msvcrt

                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            return False
        self._fh = fh
        return True

    def release(self) -> None:
        if self._fh is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._fh, fcntl.LOCK_UN)
        finally:
            self._fh.close()
            self._fh = None

    @classmethod
    def is_held(cls, path: Path) -> bool:
        lock = cls(path)
        if lock.acquire():
            lock.release()
            return False
        return True

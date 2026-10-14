"""File locations and user settings."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .db import Database

DEFAULTS: dict[str, str] = {
    "format": "bv*+ba/b",
    "merge_format": "mp4",
    "subtitles": "true",
    "cookies_file": "",
    "cookies_from_browser": "",
    "js_runtime": "",
    "rate_limit": "",
    "pause_between": "10",
    "max_attempts": "3",
    "max_unverified": "10",
    "min_free_gb": "5",
    "keep_files": "false",
    "collection": "opensource_movies",
}

HELP: dict[str, str] = {
    "format": "yt-dlp format selector.",
    "merge_format": "Container for merged video and audio: mp4, mkv or webm.",
    "subtitles": "Download and upload subtitles.",
    "cookies_file": "Path to a cookies.txt file for YouTube.",
    "cookies_from_browser": "Use this browser's YouTube login when YouTube blocks downloads. Firefox works best.",
    "js_runtime": "JavaScript runtime for yt-dlp: deno (default), node, bun or quickjs.",
    "rate_limit": "Maximum download speed, for example 5M. Empty means no limit.",
    "pause_between": "Seconds to wait between two videos.",
    "max_attempts": "Attempts for each video before the worker skips it.",
    "max_unverified": "Uploads that can wait for Internet Archive confirmation before the next download starts.",
    "min_free_gb": "The worker pauses when the free disk space is less than this.",
    "keep_files": "Keep the video files on this computer after the upload. Default: delete them.",
    "collection": "New accounts can upload only to opensource_movies.",
}

_BOOL_KEYS = {"subtitles", "keep_files"}
_INT_KEYS = {"pause_between", "max_attempts", "max_unverified", "min_free_gb"}
_TRUE = {"true", "yes", "on", "1"}
_FALSE = {"false", "no", "off", "0"}


@dataclass(frozen=True)
class Paths:
    home: Path

    @classmethod
    def from_home(cls, home: Path | None) -> "Paths":
        home = (home or default_home()).expanduser().resolve()
        (home / "downloads").mkdir(parents=True, exist_ok=True)
        return cls(home)

    @property
    def db(self) -> Path:
        # The project was called ytia before; keep using a database made by that version.
        legacy = self.home / "ytia.db"
        return legacy if legacy.exists() else self.home / "archive-ferry.db"

    @property
    def downloads(self) -> Path:
        return self.home / "downloads"

    @property
    def lock(self) -> Path:
        return self.home / "worker.lock"

    @property
    def log(self) -> Path:
        return self.home / "archive-ferry.log"


def home_from_env() -> Path | None:
    value = os.environ.get("ARCHIVE_FERRY_HOME") or os.environ.get("YTIA_HOME")
    return Path(value) if value else None


def default_home() -> Path:
    home = Path.home() / "archive-ferry"
    legacy = Path.home() / "ytia"
    return legacy if legacy.is_dir() and not home.exists() else home


class Settings:
    def __init__(self, db: Database):
        self.db = db

    def get(self, key: str) -> str:
        return self.db.get_setting(key, DEFAULTS[key])

    def get_bool(self, key: str) -> bool:
        return self.get(key) == "true"

    def get_int(self, key: str) -> int:
        return int(self.get(key))

    @property
    def paused(self) -> bool:
        return self.db.get_setting("paused", "0") != "0"

    @property
    def pause_now(self) -> bool:
        return self.db.get_setting("paused", "0") == "now"

    def set(self, key: str, value: str) -> str:
        """Validate and store a setting. Returns the stored value."""
        if key not in DEFAULTS:
            raise ValueError(f"Unknown setting '{key}'. Run `archive-ferry config show` to see all settings.")
        value = value.strip()
        if key in _BOOL_KEYS:
            if value.lower() not in _TRUE | _FALSE:
                raise ValueError(f"{key} must be true or false.")
            value = "true" if value.lower() in _TRUE else "false"
        elif key in _INT_KEYS:
            if not value.isdigit():
                raise ValueError(f"{key} must be a whole number of 0 or more.")
        elif key == "merge_format" and value not in {"mp4", "mkv", "webm"}:
            raise ValueError("merge_format must be mp4, mkv or webm.")
        elif key == "rate_limit" and value:
            from yt_dlp.utils import parse_bytes

            if parse_bytes(value) is None:
                raise ValueError("rate_limit must be a size per second, for example 500K or 5M.")
        elif key == "cookies_file" and value:
            path = Path(value).expanduser()
            if not path.is_file():
                raise ValueError(f"File not found: {path}")
            value = str(path.resolve())
        self.db.set_setting(key, value)
        return value

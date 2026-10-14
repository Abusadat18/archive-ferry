"""yt-dlp options that sync and download share."""

from __future__ import annotations

import logging

from .config import Settings
from .tools import find_ffmpeg

log = logging.getLogger("archive_ferry.yt-dlp")


class _Logger:
    """Send yt-dlp output to the log file instead of the terminal."""

    def debug(self, msg: str) -> None:
        log.debug(msg)

    def info(self, msg: str) -> None:
        log.debug(msg)

    def warning(self, msg: str) -> None:
        log.warning(msg)

    def error(self, msg: str) -> None:
        log.error(msg)


def base_options(settings: Settings) -> dict:
    opts: dict = {"quiet": True, "noprogress": True, "logger": _Logger()}
    if cookies_file := settings.get("cookies_file"):
        opts["cookiefile"] = cookies_file
    if browser := settings.get("cookies_from_browser"):
        opts["cookiesfrombrowser"] = (browser, None, None, None)
    if runtime := settings.get("js_runtime"):
        opts["js_runtimes"] = {runtime: {}}
    # yt-dlp finds a system ffmpeg on its own, but not the bundled one.
    ffmpeg = find_ffmpeg()
    if ffmpeg.bundled:
        opts["ffmpeg_location"] = ffmpeg.path
    return opts

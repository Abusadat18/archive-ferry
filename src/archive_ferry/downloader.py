"""Download one video with yt-dlp, with its metadata, thumbnail and subtitles."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError, parse_bytes

from .config import Settings
from .progress import Aborted
from .ytdl import base_options

__all__ = ["Aborted", "DownloadResult", "clean_info_json", "completed_files", "download_video", "find_info_json"]

log = logging.getLogger("archive_ferry.download")

MEDIA_SUFFIXES = {".mp4", ".mkv", ".webm", ".m4a", ".mp3", ".opus", ".mov"}
# yt-dlp temporary files, and the separate video/audio streams that exist before the merge.
_PARTIAL = re.compile(r"(\.part(-Frag\d+)?|\.ytdl|\.temp\.\w+|\.f\d+(-\d+)?\.\w+)$")
# These keys hold signed googlevideo URLs (they contain your IP address) or local paths
# (they contain your user name). They must not go to a public archive.
_PRIVATE_KEYS = {
    "formats",
    "requested_formats",
    "requested_downloads",
    "requested_subtitles",
    "automatic_captions",
    "http_headers",
    "url",
    "manifest_url",
    "fragment_base_url",
    "fragments",
    "filename",
    "_filename",
    "filepath",
}

# (bytes done, bytes total, bytes per second, seconds left) for the stream that downloads now.
ProgressCallback = Callable[[int, "int | None", "float | None", "float | None"], None]


@dataclass
class DownloadResult:
    info: dict
    files: list[Path]


def completed_files(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir() if p.is_file() and not _PARTIAL.search(p.name))


def find_info_json(folder: Path) -> Path | None:
    if not folder.is_dir():
        return None
    return next(folder.glob("*.info.json"), None)


def _scrub(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: _scrub(item)
            for key, item in value.items()
            if key not in _PRIVATE_KEYS and not key.startswith("__")
        }
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    if isinstance(value, str) and "googlevideo.com" in value:
        return None
    return value


def clean_info_json(path: Path) -> dict:
    """Remove private data from a yt-dlp .info.json in place and return the clean data."""
    info = _scrub(json.loads(path.read_text(encoding="utf-8")))
    path.write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
    return info  # type: ignore[return-value]


def _options(dest: Path, settings: Settings, hook: Callable[[dict], None], subtitles: bool) -> dict:
    opts = {
        **base_options(settings),
        "format": settings.get("format"),
        "merge_output_format": settings.get("merge_format"),
        "outtmpl": {"default": str(dest / "%(id)s.%(ext)s")},
        "writeinfojson": True,
        "writethumbnail": True,
        "writedescription": True,
        "writesubtitles": subtitles,
        "subtitleslangs": ["all", "-live_chat"],
        "continuedl": True,
        "retries": 10,
        "fragment_retries": 10,
        "progress_hooks": [hook],
        "postprocessors": [{"key": "FFmpegThumbnailsConvertor", "format": "jpg", "when": "before_dl"}],
    }
    if rate_limit := settings.get("rate_limit"):
        opts["ratelimit"] = parse_bytes(rate_limit)
    return opts


def download_video(
    video_id: str,
    dest: Path,
    settings: Settings,
    on_progress: ProgressCallback,
    should_abort: Callable[[], bool],
) -> DownloadResult:
    dest.mkdir(parents=True, exist_ok=True)

    def hook(status: dict) -> None:
        if should_abort():
            raise Aborted()
        if status.get("status") == "downloading":
            total = status.get("total_bytes") or status.get("total_bytes_estimate")
            on_progress(
                status.get("downloaded_bytes") or 0,
                int(total) if total else None,
                status.get("speed"),
                status.get("eta"),
            )

    url = f"https://www.youtube.com/watch?v={video_id}"
    subtitles = settings.get_bool("subtitles")
    try:
        with YoutubeDL(_options(dest, settings, hook, subtitles)) as ydl:
            ydl.extract_info(url, download=True)
    except DownloadError as exc:
        # YouTube often rate-limits subtitle requests. The video is more important than its subtitles.
        if not subtitles or "subtitle" not in str(exc).lower():
            raise
        log.warning("Subtitles failed for %s, download again without them: %s", video_id, exc)
        with YoutubeDL(_options(dest, settings, hook, subtitles=False)) as ydl:
            ydl.extract_info(url, download=True)

    files = completed_files(dest)
    if not any(f.suffix in MEDIA_SUFFIXES for f in files):
        raise RuntimeError(f"yt-dlp completed, but no video file is in {dest}")
    info_path = find_info_json(dest)
    if info_path is None:
        raise RuntimeError(f"yt-dlp completed, but no .info.json file is in {dest}")
    return DownloadResult(clean_info_json(info_path), files)

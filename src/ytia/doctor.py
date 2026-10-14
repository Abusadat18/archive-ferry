"""Setup checks that the CLI and the web UI share."""

from __future__ import annotations

import platform
import shutil
from dataclasses import asdict, dataclass

from . import __version__
from .archive import Uploader
from .config import Paths, Settings


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    fix: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def run_checks(paths: Paths, settings: Settings) -> list[Check]:
    import yt_dlp.version

    checks = [
        Check("ytia", True, __version__),
        Check("Python", True, platform.python_version()),
        Check("yt-dlp", True, yt_dlp.version.__version__),
    ]
    ffmpeg = shutil.which("ffmpeg")
    checks.append(
        Check("ffmpeg", bool(ffmpeg), ffmpeg or "not found", "Install ffmpeg (Windows: winget install Gyan.FFmpeg)")
    )
    runtime = settings.get("js_runtime") or "deno"
    binary = shutil.which({"quickjs": "qjs"}.get(runtime, runtime))
    checks.append(
        Check(
            f"JS runtime ({runtime})",
            bool(binary),
            binary or "not found",
            "Install Deno (Windows: winget install DenoLand.Deno). yt-dlp needs it for YouTube.",
        )
    )
    keys = Uploader().has_credentials()
    checks.append(
        Check(
            "Internet Archive keys",
            keys,
            "found" if keys else "not found",
            "Log in to the Internet Archive (ytia ui → Setup, or run: ia configure)",
        )
    )
    free_gb = shutil.disk_usage(paths.downloads).free / 1e9
    checks.append(
        Check(
            "Free disk space",
            free_gb >= settings.get_int("min_free_gb"),
            f"{free_gb:.1f} GB in {paths.downloads}",
            "Make space free, or use --home on another disk",
        )
    )
    return checks

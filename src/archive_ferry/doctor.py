"""Setup checks that the CLI and the web UI share."""

from __future__ import annotations

import platform
import shutil
from dataclasses import asdict, dataclass

from . import __version__
from .archive import Uploader
from .config import Paths, Settings
from .tools import find_ffmpeg, find_js_runtime


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
        Check("archive-ferry", True, __version__),
        Check("Python", True, platform.python_version()),
        Check("yt-dlp", True, yt_dlp.version.__version__),
    ]
    reinstall = "Install archive-ferry again; it brings ffmpeg and Deno with it."
    ffmpeg = find_ffmpeg()
    checks.append(Check("ffmpeg", ffmpeg.path is not None, ffmpeg.describe(), reinstall))
    runtime = settings.get("js_runtime") or "deno"
    js = find_js_runtime(runtime)
    checks.append(
        Check(
            f"JS runtime ({runtime})",
            js.path is not None,
            js.describe(),
            reinstall if runtime == "deno" else f"Install {runtime}, or set js_runtime back to deno.",
        )
    )
    keys = Uploader().has_credentials()
    checks.append(
        Check(
            "Internet Archive keys",
            keys,
            "found" if keys else "not found",
            "Log in to the Internet Archive (archive-ferry ui → Setup, or run: ia configure)",
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

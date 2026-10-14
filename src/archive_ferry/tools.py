"""Find ffmpeg and the JavaScript runtime: the user's own copy first, then the copy that ships with archive-ferry."""

from __future__ import annotations

import os
import shutil
import sysconfig
from dataclasses import dataclass

_JS_BINARIES = {"deno": "deno", "node": "node", "bun": "bun", "quickjs": "qjs"}


@dataclass(frozen=True)
class Tool:
    path: str | None
    bundled: bool = False

    def describe(self) -> str:
        if self.path is None:
            return "not found"
        return f"{self.path} (bundled)" if self.bundled else self.path


def find_ffmpeg() -> Tool:
    if system := shutil.which("ffmpeg"):
        return Tool(system)
    try:
        import imageio_ffmpeg

        return Tool(imageio_ffmpeg.get_ffmpeg_exe(), bundled=True)
    except Exception:
        return Tool(None)


def find_js_runtime(name: str = "deno") -> Tool:
    binary = _JS_BINARIES.get(name, name)
    if system := shutil.which(binary):
        return Tool(system)
    # The deno package puts deno in the scripts folder of this Python. yt-dlp looks there too.
    scripts = os.path.join(sysconfig.get_path("scripts"), binary + (sysconfig.get_config_var("EXE") or ""))
    if os.path.isfile(scripts):
        return Tool(scripts, bundled=True)
    return Tool(None)

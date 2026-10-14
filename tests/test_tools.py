import shutil

from archive_ferry import tools
from archive_ferry.ytdl import base_options


def test_system_ffmpeg_comes_first(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    assert tools.find_ffmpeg() == tools.Tool("/usr/bin/ffmpeg")


def test_bundled_ffmpeg_without_system_copy(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    ffmpeg = tools.find_ffmpeg()
    assert ffmpeg.bundled and "ffmpeg" in ffmpeg.path
    assert ffmpeg.describe().endswith("(bundled)")


def test_bundled_deno_without_system_copy(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    deno = tools.find_js_runtime("deno")
    assert deno.bundled and "deno" in deno.path


def test_yt_dlp_gets_bundled_ffmpeg_location(monkeypatch, settings):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert "ffmpeg" in base_options(settings)["ffmpeg_location"]
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    assert "ffmpeg_location" not in base_options(settings)

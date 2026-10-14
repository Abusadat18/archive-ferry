"""The download → upload → verify loop, one video at a time."""

from __future__ import annotations

import json
import logging
import re
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Callable

from .archive import IdentifierTaken, Uploader, build_metadata, file_md5, make_identifier
from .config import Paths, Settings
from .db import (
    DONE,
    DOWNLOADED,
    DOWNLOADING,
    FAILED,
    PENDING,
    SKIPPED,
    UPLOADED,
    UPLOADING,
    Database,
)
from .downloader import Aborted, completed_files, download_video, find_info_json

log = logging.getLogger("ytia.worker")

RETRY_BASE_SECONDS = 5 * 60
RETRY_MAX_SECONDS = 6 * 3600
FIRST_CHECK_DELAY = 60
CHECK_INTERVAL = 5 * 60
PROGRESS_INTERVAL = 2.0

# Download errors that a retry cannot fix. `ytia retry --skipped` puts these videos back.
_PERMANENT_ERRORS = (
    "Private video",
    "Video unavailable",
    "This video has been removed",
    "members-only",
    "Join this channel",
)
# YouTube wants proof that a person is there. More requests make the block longer.
_BLOCKED_ERRORS = ("Sign in to confirm", "HTTP Error 429", "Too Many Requests")
_ANSI = re.compile(r"\x1b\[[0-9;]*m")

Output = Callable[[str, str], None]


def _short_error(exc: BaseException) -> str:
    text = _ANSI.sub("", str(exc)).strip() or type(exc).__name__
    first = text.splitlines()[0].removeprefix("ERROR: ")
    return first[:500]


def _size(num: float | None) -> str:
    if not num:
        return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if num < 1024:
            return f"{num:.0f} {unit}"
        num /= 1024
    return f"{num:.1f} TB"


class Worker:
    def __init__(
        self,
        db: Database,
        settings: Settings,
        paths: Paths,
        uploader: Uploader,
        downloader=download_video,
        out: Output | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.db = db
        self.settings = settings
        self.paths = paths
        self.uploader = uploader
        self.downloader = downloader
        self.out = out or (lambda message, level: None)
        self.clock = clock
        self.sleep = sleep
        self.stop_requested = False
        self._activity_text = ""
        self._last_progress = 0.0
        self._abort_checked_at = 0.0
        self._abort_cached = False

    def request_stop(self) -> None:
        """Stop after the current step. A second Ctrl+C stops at once (KeyboardInterrupt)."""
        self.stop_requested = True

    def run(self) -> None:
        self._event("info", "Worker started.")
        while not self.stop_requested:
            if self.settings.paused:
                self._activity("Paused")
                self._wait(2)
                continue

            self._check_uploads()
            waiting = self.db.count(UPLOADED)
            if waiting >= self.settings.get_int("max_unverified"):
                self._activity(f"Waiting for the Internet Archive to confirm {waiting} uploads")
                self._wait(30)
                continue

            row = self.db.next_video(self.clock())
            if row is None:
                if waiting:
                    self._activity(f"Waiting for the Internet Archive to confirm {waiting} uploads")
                    self._wait(30)
                    continue
                retry_at = self.db.next_retry_at()
                if retry_at is not None:
                    self._activity("Waiting to retry failed videos")
                    self._wait(min(60.0, max(1.0, retry_at - self.clock())))
                    continue
                self._event("info", "The queue is empty. All videos are processed.")
                break

            if not self._enough_disk():
                continue
            self.process(row)
            if not self.stop_requested and not self.settings.paused:
                self._wait(self.settings.get_int("pause_between"))
        self._activity("Idle")

    def process(self, row: sqlite3.Row) -> None:
        video_id = row["video_id"]
        if self._needs_download(row):
            try:
                self._download(row)
            except Aborted:
                self._event("info", "Download paused. It continues from the same point after resume.", video_id)
                return
            except Exception as exc:
                if self._abort_requested(force=True):
                    self._event("info", "Download paused. It continues from the same point after resume.", video_id)
                    return
                self._fail(video_id, DOWNLOADING, exc)
                return
            if self.stop_requested or self.settings.paused:
                return
        try:
            self._upload(self.db.video(video_id))
        except Exception as exc:
            self._fail(video_id, UPLOADING, exc)

    def _local_dir(self, row: sqlite3.Row) -> Path:
        return Path(row["local_dir"]) if row["local_dir"] else self.paths.downloads / row["video_id"]

    def _needs_download(self, row: sqlite3.Row) -> bool:
        resumes_upload = row["status"] in (DOWNLOADED, UPLOADING) or (
            row["status"] == FAILED and row["failed_from"] == UPLOADING
        )
        return not (resumes_upload and find_info_json(self._local_dir(row)))

    def _download(self, row: sqlite3.Row) -> None:
        video_id = row["video_id"]
        dest = self.paths.downloads / video_id
        self.db.update_video(video_id, status=DOWNLOADING, local_dir=str(dest))
        self._event("info", f"Downloading: {row['title'] or video_id}", video_id)
        self._activity(f"Downloading {video_id}")
        result = self.downloader(
            video_id,
            dest,
            self.settings,
            on_progress=lambda done, total: self._progress(video_id, done, total),
            should_abort=self._abort_requested,
        )
        info = result.info
        self.db.update_video(
            video_id,
            status=DOWNLOADED,
            title=info.get("title") or row["title"],
            upload_date=info.get("upload_date"),
        )
        total = sum(f.stat().st_size for f in result.files)
        self._event("info", f"Downloaded {len(result.files)} files ({_size(total)}).", video_id)

    def _upload(self, row: sqlite3.Row) -> None:
        video_id = row["video_id"]
        dest = self._local_dir(row)
        info_path = find_info_json(dest)
        if info_path is None:
            raise RuntimeError(f"No .info.json file in {dest}")
        info = json.loads(info_path.read_text(encoding="utf-8"))
        channel = self.db.channel(row["channel_id"])
        identifier = row["ia_identifier"] or make_identifier(channel["ia_prefix"], video_id)
        files = completed_files(dest)
        self.db.update_video(video_id, status=UPLOADING, ia_identifier=identifier)
        self._activity(f"Uploading {video_id} to {identifier}")
        self._event(
            "info",
            f"Uploading {len(files)} files ({_size(sum(f.stat().st_size for f in files))})"
            f" to https://archive.org/details/{identifier}",
            video_id,
        )
        md5s = {path.name: file_md5(path) for path in files}
        self.uploader.upload(identifier, files, build_metadata(info, self.settings.get("collection")))
        self.db.update_video(
            video_id,
            status=UPLOADED,
            file_md5s=json.dumps(md5s),
            next_attempt_at=self.clock() + FIRST_CHECK_DELAY,
            attempts=0,
            failed_from=None,
            last_error=None,
        )
        self._event("info", "Uploaded. Waiting for the Internet Archive to confirm.", video_id)

    def _check_uploads(self) -> None:
        for row in self.db.due_for_check(self.clock()):
            video_id = row["video_id"]
            try:
                confirmed = self.uploader.verify(row["ia_identifier"], json.loads(row["file_md5s"] or "{}"))
            except Exception as exc:
                log.warning("Check of %s failed: %s", row["ia_identifier"], exc)
                confirmed = False
            if not confirmed:
                self.db.update_video(video_id, next_attempt_at=self.clock() + CHECK_INTERVAL)
                continue
            if not self.settings.get_bool("keep_files"):
                self._delete_local(row)
            self.db.update_video(video_id, status=DONE, next_attempt_at=None)
            self._event("success", f"Done: https://archive.org/details/{row['ia_identifier']}", video_id)

    def _delete_local(self, row: sqlite3.Row) -> None:
        folder = self._local_dir(row).resolve()
        # Never delete outside the downloads folder, even if the database holds a strange path.
        if folder.is_relative_to(self.paths.downloads.resolve()) and folder != self.paths.downloads.resolve():
            shutil.rmtree(folder, ignore_errors=True)

    def _fail(self, video_id: str, step: str, exc: BaseException) -> None:
        message = _short_error(exc)
        log.exception("%s failed during %s", video_id, step, exc_info=exc)
        if step == DOWNLOADING and any(text in message for text in _BLOCKED_ERRORS):
            self.db.update_video(video_id, status=PENDING, last_error=message)
            self.db.set_setting("paused", "1")
            self._event(
                "error",
                f"YouTube blocked the download: {message}\n"
                "The queue is paused. Give yt-dlp your YouTube cookies, for example:\n"
                "  ytia config set cookies_from_browser firefox\n"
                "Then run: ytia resume",
                video_id,
            )
            return

        attempts = self.db.video(video_id)["attempts"] + 1
        max_attempts = self.settings.get_int("max_attempts")
        permanent = isinstance(exc, IdentifierTaken) or (
            step == DOWNLOADING and any(text in message for text in _PERMANENT_ERRORS)
        )
        if permanent or attempts >= max_attempts:
            self.db.update_video(
                video_id, status=SKIPPED, failed_from=step, attempts=attempts, last_error=message
            )
            self._event("error", f"Skipped after {attempts} attempt(s): {message}", video_id)
            return
        delay = min(RETRY_BASE_SECONDS * 2 ** (attempts - 1), RETRY_MAX_SECONDS)
        self.db.update_video(
            video_id,
            status=FAILED,
            failed_from=step,
            attempts=attempts,
            last_error=message,
            next_attempt_at=self.clock() + delay,
        )
        self._event(
            "warning",
            f"Failed (attempt {attempts} of {max_attempts}): {message}. Next attempt in {delay // 60} min.",
            video_id,
        )

    def _enough_disk(self) -> bool:
        free_gb = shutil.disk_usage(self.paths.downloads).free / 1e9
        if free_gb >= self.settings.get_int("min_free_gb"):
            return True
        self.db.set_setting("paused", "1")
        self._event(
            "error",
            f"Only {free_gb:.1f} GB of disk space is free. The queue is paused."
            " Make space free, then run: ytia resume",
        )
        return False

    def _abort_requested(self, force: bool = False) -> bool:
        # The progress hook calls this many times each second, so read the database at most every 2 s.
        now = self.clock()
        if force or now - self._abort_checked_at >= PROGRESS_INTERVAL:
            self._abort_cached = self.settings.pause_now
            self._abort_checked_at = now
        return self._abort_cached

    def _progress(self, video_id: str, done: int, total: int | None) -> None:
        now = self.clock()
        if now - self._last_progress < PROGRESS_INTERVAL:
            return
        self._last_progress = now
        percent = f"{done * 100 // total}% of {_size(total)}" if total else _size(done)
        self._activity(f"Downloading {video_id}: {percent}")

    def _activity(self, text: str) -> None:
        if text != self._activity_text:
            self._activity_text = text
            self.db.set_setting("activity", text)

    def _event(self, level: str, message: str, video_id: str | None = None) -> None:
        self.db.log(level, message, video_id)
        getattr(log, "info" if level == "success" else level)(f"[{video_id or '-'}] {message}")
        self.out(f"{video_id}: {message}" if video_id else message, level)

    def _wait(self, seconds: float) -> None:
        end = self.clock() + seconds
        while not self.stop_requested and self.clock() < end:
            self.sleep(min(1.0, end - self.clock()))

from __future__ import annotations

import json
from pathlib import Path

import pytest

from archive_ferry.archive import IdentifierTaken
from archive_ferry.db import DONE, DOWNLOADED, FAILED, PENDING, SKIPPED, UPLOADED
from archive_ferry.downloader import Aborted, DownloadResult, completed_files
from archive_ferry.worker import Worker


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class FakeDownloader:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.errors: dict[str, list[Exception]] = {}

    def __call__(self, video_id, dest: Path, settings, on_progress, should_abort):
        self.calls.append(video_id)
        if self.errors.get(video_id):
            raise self.errors[video_id].pop(0)
        dest.mkdir(parents=True, exist_ok=True)
        info = {"id": video_id, "title": f"Title {video_id}", "upload_date": "20240101"}
        (dest / f"{video_id}.mp4").write_bytes(b"video-" + video_id.encode())
        (dest / f"{video_id}.info.json").write_text(json.dumps(info), encoding="utf-8")
        on_progress(50, 100, 1000.0, 1.0)
        return DownloadResult(info, completed_files(dest))


class FakeUploader:
    def __init__(self) -> None:
        self.items: dict[str, dict] = {}
        self.errors: list[Exception] = []
        self.confirm = True
        self.clock: FakeClock | None = None
        self.seen_local_files: list[bool] = []

    def has_credentials(self):
        return True

    def upload(self, identifier, files, metadata, md5s, on_progress=None, should_abort=None):
        if self.errors:
            raise self.errors.pop(0)
        assert set(md5s) == {f.name for f in files}
        total = sum(f.stat().st_size for f in files)
        for done in (total // 2, total):
            if self.clock is not None:
                self.clock.now += 5
            if on_progress:
                on_progress(done, total)
        self.seen_local_files.append(all(f.exists() for f in files))
        self.items[identifier] = {"metadata": metadata, "files": [f.name for f in files]}
        return total

    def verify(self, identifier, md5s):
        return self.confirm and identifier in self.items


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def downloader():
    return FakeDownloader()


@pytest.fixture
def uploader():
    return FakeUploader()


@pytest.fixture
def worker(db, settings, paths, uploader, downloader, clock):
    return Worker(db, settings, paths, uploader, downloader=downloader, clock=clock, sleep=clock.sleep)


def test_full_run_uploads_everything_and_cleans_up(worker, db, paths, uploader, channel):
    worker.run()
    assert db.counts()[DONE] == 3
    assert sorted(uploader.items) == ["demo-vid00000001", "demo-vid00000002", "demo-vid00000003"]
    item = uploader.items["demo-vid00000001"]
    assert item["files"] == ["vid00000001.info.json", "vid00000001.mp4"]
    assert item["metadata"]["date"] == "2024-01-01"
    assert list(paths.downloads.iterdir()) == []
    assert db.video("vid00000001")["title"] == "Title vid00000001"


def test_keep_files(worker, db, settings, paths, channel):
    settings.set("keep_files", "true")
    worker.run()
    assert db.counts()[DONE] == 3
    assert len(list(paths.downloads.iterdir())) == 3


def test_upload_failure_retries_without_new_download(worker, db, downloader, uploader, clock, channel):
    uploader.errors = [RuntimeError("503 SlowDown")]
    worker.process(db.next_video(clock()))
    row = db.video("vid00000001")
    assert row["status"] == FAILED and row["failed_from"] == "uploading"
    assert row["last_error"] == "503 SlowDown"

    clock.now = row["next_attempt_at"]
    worker.process(db.video("vid00000001"))
    assert db.video("vid00000001")["status"] == UPLOADED
    assert downloader.calls == ["vid00000001"]


def test_skip_after_max_attempts(worker, db, settings, downloader, clock, channel):
    settings.set("max_attempts", "2")
    downloader.errors["vid00000001"] = [RuntimeError("boom"), RuntimeError("boom")]
    worker.process(db.video("vid00000001"))
    assert db.video("vid00000001")["status"] == FAILED
    worker.process(db.video("vid00000001"))
    row = db.video("vid00000001")
    assert row["status"] == SKIPPED and row["attempts"] == 2


def test_private_video_is_skipped_at_once(worker, db, downloader, channel):
    downloader.errors["vid00000001"] = [RuntimeError("ERROR: [youtube] vid00000001: Private video. Sign in")]
    worker.process(db.video("vid00000001"))
    row = db.video("vid00000001")
    assert row["status"] == SKIPPED
    assert row["last_error"].startswith("[youtube]")


def test_identifier_taken_is_skipped(worker, db, uploader, channel):
    uploader.errors = [IdentifierTaken("taken")]
    worker.process(db.video("vid00000001"))
    assert db.video("vid00000001")["status"] == SKIPPED


def test_bot_check_pauses_queue(worker, db, settings, downloader, channel):
    downloader.errors["vid00000001"] = [RuntimeError("Sign in to confirm you're not a bot")]
    worker.process(db.video("vid00000001"))
    assert settings.paused
    row = db.video("vid00000001")
    assert row["status"] == PENDING and row["attempts"] == 0


def test_hard_pause_keeps_download_resumable(worker, db, downloader, channel):
    db.set_setting("paused", "now")
    downloader.errors["vid00000001"] = [Aborted()]
    worker.process(db.video("vid00000001"))
    assert db.video("vid00000001")["status"] == "downloading"
    assert db.next_video(0)["video_id"] == "vid00000001"


def test_soft_pause_stops_after_download(worker, db, uploader, channel):
    worker.downloader = _pause_after(worker.downloader, db)
    worker.process(db.video("vid00000001"))
    assert db.video("vid00000001")["status"] == DOWNLOADED
    assert uploader.items == {}


def test_waits_for_confirmation_before_next_download(worker, db, settings, uploader, downloader, clock, channel):
    settings.set("max_unverified", "1")
    uploader.confirm = False
    worker.process(db.next_video(clock()))
    assert db.count(UPLOADED) == 1

    calls = []
    original_sleep = clock.sleep

    def sleep(seconds):
        calls.append(seconds)
        if len(calls) > 5:
            worker.request_stop()
        original_sleep(seconds)

    worker.sleep = sleep
    worker.run()
    assert downloader.calls == ["vid00000001"]


def test_resume_upload_after_crash(worker, db, clock, uploader, downloader, channel):
    worker.process(db.next_video(clock()))
    db.update_video("vid00000002", status="uploading")
    worker.downloader(  # files of an interrupted run are still on disk
        "vid00000002", worker.paths.downloads / "vid00000002", None, lambda *a: None, lambda: False
    )
    db.update_video("vid00000002", local_dir=str(worker.paths.downloads / "vid00000002"))
    downloader.calls.clear()
    worker.process(db.next_video(clock()))
    assert downloader.calls == []
    assert "demo-vid00000002" in uploader.items


def _pause_after(download, db):
    def wrapped(*args, **kwargs):
        result = download(*args, **kwargs)
        db.set_setting("paused", "1")
        return result

    return wrapped

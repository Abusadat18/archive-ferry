import json

import pytest

from archive_ferry.db import DONE, FAILED, SKIPPED, UPLOADED
from archive_ferry.progress import Aborted
from archive_ferry.worker import CONFIRM_TIMEOUT

from .test_worker import clock, downloader, uploader, worker  # noqa: F401  (pytest fixtures)


def test_files_are_deleted_right_after_upload(worker, db, paths, uploader, clock, channel):
    uploader.confirm = False
    worker.process(db.next_video(clock()))
    row = db.video("vid00000001")
    assert row["status"] == UPLOADED
    assert uploader.seen_local_files == [True]
    assert not (paths.downloads / "vid00000001").exists()
    assert row["local_dir"] is None
    assert row["size_bytes"] > 0 and row["uploaded_at"] == clock.now
    assert json.loads(row["file_md5s"]).keys() == {"vid00000001.info.json", "vid00000001.mp4"}


def test_metadata_row_survives_and_resync_does_not_requeue(worker, db, channel):
    worker.run()
    assert db.add_videos(channel["id"], [{"id": "vid00000001"}, {"id": "vid00000002"}]) == 0
    row = db.video("vid00000001")
    assert row["status"] == DONE and row["ia_identifier"] == "demo-vid00000001"
    assert row["title"] == "Title vid00000001" and row["upload_date"] == "20240101"


def test_unconfirmed_upload_is_downloaded_again_after_timeout(worker, db, uploader, clock, channel):
    uploader.confirm = False
    worker.process(db.next_video(clock()))
    clock.now += CONFIRM_TIMEOUT + 1
    worker._check_uploads()
    row = db.video("vid00000001")
    assert row["status"] == FAILED and row["failed_from"] == "downloading"
    assert "did not confirm" in row["last_error"]


def test_upload_speed_is_recorded_and_transfer_cleared(worker, db, uploader, clock, channel):
    uploader.clock = clock
    phases = []
    original = worker._report

    def spy(*args):
        phases.append(args[0])
        original(*args)

    worker._report = spy
    worker.process(db.next_video(clock()))
    assert phases == ["download", "upload", "upload"]
    row = db.video("vid00000001")
    speeds = json.loads(db.get_setting("speeds"))
    assert speeds["upload"]["bps"] == pytest.approx(row["size_bytes"] / 10)
    assert speeds["download"]["bps"] == 1000.0
    assert db.get_setting("transfer") == ""
    assert row["upload_seconds"] == 10


def test_upload_abort_keeps_files(worker, db, paths, uploader, channel):
    uploader.errors = [Aborted()]
    worker.process(db.video("vid00000001"))
    row = db.video("vid00000001")
    assert row["status"] == "uploading" and row["attempts"] == 0
    assert (paths.downloads / "vid00000001" / "vid00000001.mp4").exists()


def test_skipped_download_removes_partial_files(worker, db, paths, downloader, channel):
    folder = paths.downloads / "vid00000001"
    folder.mkdir()
    (folder / "vid00000001.mp4.part").write_bytes(b"partial")
    downloader.errors["vid00000001"] = [RuntimeError("Video unavailable")]
    worker.process(db.video("vid00000001"))
    assert db.video("vid00000001")["status"] == SKIPPED
    assert not folder.exists()

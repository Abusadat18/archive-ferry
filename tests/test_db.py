from archive_ferry.db import DOWNLOADED, FAILED, PENDING, SKIPPED, UPLOADING, Database


def test_next_video_takes_oldest_pending_first(db: Database, channel):
    assert db.next_video(now=0)["video_id"] == "vid00000001"


def test_next_video_resumes_interrupted_work_first(db: Database, channel):
    db.update_video("vid00000003", status=UPLOADING)
    assert db.next_video(now=0)["video_id"] == "vid00000003"


def test_failed_video_waits_for_retry_time(db: Database, channel):
    for vid in ("vid00000001", "vid00000002"):
        db.update_video(vid, status=SKIPPED)
    db.update_video("vid00000003", status=FAILED, next_attempt_at=100)
    assert db.next_video(now=50) is None
    assert db.next_video(now=100)["video_id"] == "vid00000003"


def test_add_videos_ignores_known_ids(db: Database, channel):
    added = db.add_videos(channel["id"], [{"id": "vid00000001"}, {"id": "new00000001"}])
    assert added == 1
    assert db.video("new00000001")["sort_key"] == 4


def test_requeue_keeps_downloaded_files_for_upload_failures(db: Database, channel):
    db.update_video("vid00000001", status=FAILED, failed_from=UPLOADING, attempts=2)
    db.update_video("vid00000002", status=SKIPPED, failed_from="downloading", attempts=3)
    assert db.requeue([FAILED]) == 1
    assert db.video("vid00000001")["status"] == DOWNLOADED
    assert db.video("vid00000001")["attempts"] == 0
    assert db.requeue([FAILED, SKIPPED]) == 1
    assert db.video("vid00000002")["status"] == PENDING


def test_reopen_keeps_schema(paths, channel):
    again = Database(paths.db)
    assert again.counts()[PENDING] == 3
    again.close()

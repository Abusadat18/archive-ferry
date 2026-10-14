from pathlib import Path

import pytest

from archive_ferry.archive import Uploader, file_md5
from archive_ferry.progress import Aborted


class FakeItem:
    def __init__(self, existing: dict[str, str] | None = None):
        self.exists = existing is not None
        self.metadata = {"originalurl": "https://www.youtube.com/watch?v=abc"}
        self.files = [{"name": name, "md5": md5} for name, md5 in (existing or {}).items()]
        self.calls = []

    def upload_file(self, body, key, metadata, headers, queue_derive, retries, retries_sleep):
        # Like the real client: measure the size, start, retry from the start, then stream all.
        body.seek(0, 2)
        body.seek(0)
        body.read(3)
        body.seek(0)
        while body.read(4):
            pass
        self.calls.append((key, headers["Content-MD5"], queue_derive))


class FakeSession:
    access_key = secret_key = "x"

    def __init__(self, item):
        self.item = item

    def get_item(self, identifier):
        return self.item


@pytest.fixture
def files(tmp_path: Path):
    paths = []
    for name, data in (("abc.mp4", b"0123456789"), ("abc.info.json", b"{}")):
        path = tmp_path / name
        path.write_bytes(data)
        paths.append(path)
    return paths


def test_upload_reports_progress_and_skips_existing(files):
    md5s = {p.name: file_md5(p) for p in files}
    item = FakeItem(existing={"abc.info.json": md5s["abc.info.json"]})
    progress = []
    sent = Uploader(FakeSession(item)).upload(
        "demo-abc",
        files,
        {"originalurl": item.metadata["originalurl"]},
        md5s,
        on_progress=lambda done, total: progress.append((done, total)),
    )
    assert sent == 10
    assert item.calls == [("abc.mp4", md5s["abc.mp4"], True)]
    assert progress[-1] == (10, 10)
    assert max(done for done, _ in progress) == 10  # the retry restarted the count


def test_upload_can_be_aborted(files):
    md5s = {p.name: file_md5(p) for p in files}
    item = FakeItem()
    with pytest.raises(Aborted):
        Uploader(FakeSession(item)).upload(
            "demo-abc", files, {"originalurl": item.metadata["originalurl"]}, md5s, should_abort=lambda: True
        )
    assert item.calls == []

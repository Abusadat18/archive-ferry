from __future__ import annotations

import http.client
import json
import threading
import time

import pytest

from ytia.db import DONE, Database
from ytia.sync import ChannelListing
from ytia.web import App, WorkerHost, make_server

from .test_worker import FakeDownloader, FakeUploader


class Client:
    def __init__(self, port: int):
        self.port = port

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        data = json.dumps(body).encode() if body is not None else None
        hdrs = {"Content-Type": "application/json"} if body is not None else {}
        hdrs.update(headers or {})
        conn.request(method, path, body=data, headers=hdrs)
        response = conn.getresponse()
        raw = response.read()
        conn.close()
        return response.status, (json.loads(raw) if response.getheader("Content-Type", "").startswith("application/json") else raw)

    def get(self, path):
        return self.request("GET", path)

    def post(self, path, body=None, **kw):
        return self.request("POST", path, body if body is not None else {}, **kw)


@pytest.fixture
def uploader():
    return FakeUploader()


@pytest.fixture
def client(paths, settings, uploader):
    listing = ChannelListing("UC1", "Demo Channel", [{"id": f"vid{i:08d}"} for i in range(1, 3)])
    host = WorkerHost(paths, uploader_factory=lambda: uploader, worker_kwargs={"downloader": FakeDownloader()})
    app = App(paths, lister=lambda url, s: listing, host=host)
    server = make_server(app, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield Client(server.server_address[1])
    host.stop()
    server.shutdown()
    server.server_close()


def test_page_and_state(client):
    status, page = client.get("/")
    assert status == 200 and b"<title>ytia</title>" in page
    status, state = client.get("/api/state")
    assert status == 200 and state["app"] == "ytia" and state["total"] == 0


def test_rejects_foreign_host_and_origin(client):
    assert client.request("GET", "/api/state", headers={"Host": "evil.example:80"})[0] == 403
    assert client.post("/api/pause", headers={"Origin": "https://evil.example"})[0] == 403


def test_rejects_non_json_post(client):
    status, _ = client.request("POST", "/api/pause", headers={"Content-Type": "text/plain"})
    assert status == 415


def test_add_channel_and_list_videos(client):
    status, result = client.post("/api/channels", {"url": "https://www.youtube.com/@demo/videos"})
    assert status == 200
    assert result == {"title": "Demo Channel", "found": 2, "added": 2, "prefix": "demo-channel"}
    status, videos = client.get("/api/videos?status=pending")
    assert [v["video_id"] for v in videos] == ["vid00000001", "vid00000002"]
    assert client.post("/api/channels", {"url": "https://vimeo.com/x"})[0] == 400


def test_settings_validation(client):
    assert client.post("/api/settings", {"key": "rate_limit", "value": "5M"}) == (200, {"key": "rate_limit", "value": "5M"})
    status, body = client.post("/api/settings", {"key": "merge_format", "value": "avi"})
    assert status == 400 and "merge_format" in body["error"]


def test_pause_resume(client, paths):
    client.post("/api/pause", {"now": True})
    assert client.get("/api/state")[1]["pause_now"] is True
    client.post("/api/resume")
    assert client.get("/api/state")[1]["paused"] is False


def test_worker_runs_from_ui(client, paths, settings):
    client.post("/api/channels", {"url": "@demo"})
    assert client.post("/api/worker/start")[0] == 200
    db = Database(paths.db)
    deadline = time.time() + 15
    # The fake uploader confirms only after the first check delay, so wait for both uploads.
    while time.time() < deadline and db.count("uploaded") + db.count(DONE) < 2:
        time.sleep(0.1)
    assert db.count("uploaded") + db.count(DONE) == 2
    assert client.get("/api/state")[1]["worker"]["in_ui"] is True
    client.post("/api/worker/stop")
    db.close()


def test_worker_start_needs_ia_login(client, uploader):
    client.post("/api/channels", {"url": "@demo"})
    uploader.has_credentials = lambda: False
    status, body = client.post("/api/worker/start")
    assert status == 409 and "Log in" in body["error"]

"""Local web interface. `ytia ui` (or the `ytia-ui` launcher) opens it in the browser.

The server listens on 127.0.0.1 only. It accepts only requests whose Host header
names that address, which blocks DNS rebinding, and POST requests must be JSON,
which a different website cannot send without a CORS preflight that this server
never answers.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import urllib.request
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlparse

from . import __version__
from .archive import Uploader, slugify
from .config import DEFAULTS, HELP, Paths, Settings
from .db import FAILED, SKIPPED, STATUSES, Database
from .doctor import run_checks
from .lock import WorkerLock
from .power import KeepAwake
from .progress import current_transfer
from .sync import Lister, fetch_listing, normalize_channel_url, sync_channel

log = logging.getLogger("ytia.web")

DEFAULT_PORT = 8765
_ASSET_TYPES = {".woff2": "font/woff2", ".txt": "text/plain; charset=utf-8"}


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class WorkerHost:
    """Runs the worker in a background thread of the web server."""

    def __init__(self, paths: Paths, uploader_factory: Callable[[], Uploader] = Uploader, worker_kwargs=None):
        self.paths = paths
        self.uploader_factory = uploader_factory
        self.worker_kwargs = worker_kwargs or {}
        self.thread: threading.Thread | None = None
        self.worker = None
        self._lock = WorkerLock(paths.lock)
        self._guard = threading.Lock()

    @property
    def running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def start(self) -> None:
        with self._guard:
            if self.running:
                return
            uploader = self.uploader_factory()
            if not uploader.has_credentials():
                raise ApiError(HTTPStatus.CONFLICT, "Log in to the Internet Archive first (Setup tab).")
            if not self._lock.acquire():
                raise ApiError(HTTPStatus.CONFLICT, "A worker already runs in a terminal. Stop it there first.")
            ready = threading.Event()
            self.thread = threading.Thread(target=self._run, args=(uploader, ready), name="ytia-worker", daemon=True)
            self.thread.start()
            ready.wait(5)

    def stop(self) -> None:
        if self.worker is not None:
            self.worker.request_stop()

    def _run(self, uploader: Uploader, ready: threading.Event) -> None:
        from .worker import Worker

        db = Database(self.paths.db)
        try:
            self.worker = Worker(db, Settings(db), self.paths, uploader, **self.worker_kwargs)
            ready.set()
            with KeepAwake():
                self.worker.run()
        except Exception as exc:
            log.exception("Worker crashed")
            db.log("error", f"The worker stopped because of an error: {exc}")
        finally:
            ready.set()
            db.set_setting("activity", "Idle")
            db.close()
            self.worker = None
            self._lock.release()


class App:
    def __init__(self, paths: Paths, lister: Lister = fetch_listing, host: WorkerHost | None = None):
        self.paths = paths
        self.lister = lister
        self.host = host or WorkerHost(paths)
        self.server: ThreadingHTTPServer | None = None

    def _db(self) -> Database:
        return Database(self.paths.db)

    def handle(self, method: str, path: str, query: dict, body: dict) -> dict | list:
        route = getattr(self, f"{method.lower()}_{path.strip('/').replace('/', '_').replace('-', '_')}", None)
        if route is None:
            raise ApiError(HTTPStatus.NOT_FOUND, f"Unknown endpoint: {method} {path}")
        db = self._db()
        try:
            return route(db, query if method == "GET" else body)
        finally:
            db.close()

    # GET

    def get_api_state(self, db: Database, query: dict) -> dict:
        settings = Settings(db)
        counts = db.counts()
        here = self.host.running
        running = here or WorkerLock.is_held(self.paths.lock)
        channels = []
        for row in db.channels():
            channel_counts = db.counts(row["id"])
            channels.append(
                {
                    "id": row["id"],
                    "url": row["url"],
                    "title": row["title"],
                    "prefix": row["ia_prefix"],
                    "last_synced_at": row["last_synced_at"],
                    "done": channel_counts["done"],
                    "total": sum(channel_counts.values()),
                }
            )
        return {
            "app": "ytia",
            "version": __version__,
            "home": str(self.paths.home),
            "worker": {"running": running, "in_ui": here},
            "paused": settings.paused,
            "pause_now": settings.pause_now,
            "activity": db.get_setting("activity", "Idle") if running else "Idle",
            "transfer": current_transfer(db) if running else None,
            "speeds": json.loads(db.get_setting("speeds") or "{}"),
            "counts": counts,
            "total": sum(counts.values()),
            "channels": channels,
        }

    def get_api_videos(self, db: Database, query: dict) -> list:
        statuses = [s for s in query.get("status", []) if s in STATUSES]
        limit = min(int((query.get("limit") or ["200"])[0]), 1000)
        return [dict(row) for row in db.videos(statuses, limit)]

    def get_api_events(self, db: Database, query: dict) -> list:
        after = int((query.get("after") or ["0"])[0])
        return [dict(row) for row in db.events_after(after)]

    def get_api_settings(self, db: Database, query: dict) -> list:
        settings = Settings(db)
        return [{"key": key, "value": settings.get(key), "default": DEFAULTS[key], "help": HELP[key]} for key in DEFAULTS]

    def get_api_doctor(self, db: Database, query: dict) -> list:
        return [check.as_dict() for check in run_checks(self.paths, Settings(db))]

    # POST

    def post_api_channels(self, db: Database, body: dict) -> dict:
        try:
            url = normalize_channel_url(str(body.get("url") or ""))
        except ValueError as exc:
            raise ApiError(HTTPStatus.BAD_REQUEST, str(exc)) from None
        prefix = (body.get("prefix") or "").strip().lower() or None
        if prefix is not None and slugify(prefix) != prefix:
            raise ApiError(HTTPStatus.BAD_REQUEST, "The prefix can contain only letters, digits and dashes.")
        channel = db.add_channel(url, prefix)
        return self._sync(db, channel)

    def post_api_sync(self, db: Database, body: dict) -> list:
        rows = [db.channel(int(body["channel_id"]))] if body.get("channel_id") else db.channels()
        return [self._sync(db, row) for row in rows if row is not None]

    def _sync(self, db: Database, channel) -> dict:
        try:
            found, added = sync_channel(db, channel, Settings(db), lister=self.lister)
        except Exception as exc:
            raise ApiError(HTTPStatus.BAD_GATEWAY, f"Sync failed: {exc}") from None
        channel = db.channel(channel["id"])
        return {"title": channel["title"] or channel["url"], "found": found, "added": added, "prefix": channel["ia_prefix"]}

    def post_api_worker_start(self, db: Database, body: dict) -> dict:
        if not db.channels():
            raise ApiError(HTTPStatus.CONFLICT, "Add a channel first.")
        self.host.start()
        return {"ok": True}

    def post_api_worker_stop(self, db: Database, body: dict) -> dict:
        self.host.stop()
        return {"ok": True}

    def post_api_pause(self, db: Database, body: dict) -> dict:
        db.set_setting("paused", "now" if body.get("now") else "1")
        return {"ok": True}

    def post_api_resume(self, db: Database, body: dict) -> dict:
        db.set_setting("paused", "0")
        return {"ok": True}

    def post_api_retry(self, db: Database, body: dict) -> dict:
        return {"count": db.requeue([FAILED, SKIPPED] if body.get("skipped") else [FAILED])}

    def post_api_settings(self, db: Database, body: dict) -> dict:
        try:
            value = Settings(db).set(str(body.get("key")), str(body.get("value", "")))
        except ValueError as exc:
            raise ApiError(HTTPStatus.BAD_REQUEST, str(exc)) from None
        return {"key": body.get("key"), "value": value}

    def post_api_ia_login(self, db: Database, body: dict) -> dict:
        import internetarchive

        email, password = str(body.get("email") or "").strip(), str(body.get("password") or "")
        if not email or not password:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Enter your Internet Archive e-mail and password.")
        try:
            # Exchanges the password for S3 keys and writes them to ia.ini. The password is not stored.
            config_file = internetarchive.configure(email, password)
        except Exception as exc:
            raise ApiError(HTTPStatus.UNAUTHORIZED, f"Login failed: {exc}") from None
        return {"ok": True, "config_file": str(config_file)}

    def post_api_quit(self, db: Database, body: dict) -> dict:
        self.host.stop()
        if self.server is not None:
            threading.Thread(target=self.server.shutdown, daemon=True).start()
        return {"ok": True}


def _make_handler(app: App, allowed_hosts: set[str]) -> type[BaseHTTPRequestHandler]:
    static = files("ytia").joinpath("static")
    page = static.joinpath("index.html").read_bytes()
    # A fixed table of the bundled assets, so a request path can never reach another file.
    assets = {
        f"/static/fonts/{entry.name}": (entry.read_bytes(), _ASSET_TYPES[Path(entry.name).suffix])
        for entry in static.joinpath("fonts").iterdir()
        if Path(entry.name).suffix in _ASSET_TYPES
    }

    class Handler(BaseHTTPRequestHandler):
        server_version = f"ytia/{__version__}"

        def log_message(self, fmt: str, *args: object) -> None:
            log.debug("%s - %s", self.address_string(), fmt % args)

        def _send(self, status: int, body: bytes, content_type: str, cache: str = "no-store") -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, data: object) -> None:
            self._send(status, json.dumps(data).encode(), "application/json; charset=utf-8")

        def _dispatch(self, method: str, body: dict) -> None:
            url = urlparse(self.path)
            try:
                self._json(HTTPStatus.OK, app.handle(method, url.path, parse_qs(url.query), body))
            except ApiError as exc:
                self._json(exc.status, {"error": str(exc)})
            except Exception as exc:
                log.exception("Request failed: %s %s", method, self.path)
                self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

        def do_GET(self) -> None:
            if self.headers.get("Host") not in allowed_hosts:
                return self._json(HTTPStatus.FORBIDDEN, {"error": "Forbidden host"})
            path = urlparse(self.path).path
            if path in ("/", "/index.html"):
                return self._send(HTTPStatus.OK, page, "text/html; charset=utf-8")
            if path in assets:
                body, content_type = assets[path]
                return self._send(HTTPStatus.OK, body, content_type, cache="max-age=86400")
            self._dispatch("GET", {})

        def do_POST(self) -> None:
            host = self.headers.get("Host")
            origin = self.headers.get("Origin")
            if host not in allowed_hosts or (origin and origin != f"http://{host}"):
                return self._json(HTTPStatus.FORBIDDEN, {"error": "Forbidden origin"})
            if not (self.headers.get("Content-Type") or "").startswith("application/json"):
                return self._json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "Use application/json"})
            try:
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                body = json.loads(raw or b"{}")
            except ValueError:
                return self._json(HTTPStatus.BAD_REQUEST, {"error": "Invalid JSON"})
            if not isinstance(body, dict):
                return self._json(HTTPStatus.BAD_REQUEST, {"error": "Invalid JSON"})
            self._dispatch("POST", body)

    return Handler


def make_server(app: App, port: int = DEFAULT_PORT) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", port), _make_handler(app, set()))
    real_port = server.server_address[1]
    server.RequestHandlerClass = _make_handler(app, {f"127.0.0.1:{real_port}", f"localhost:{real_port}"})
    server.daemon_threads = True
    app.server = server
    return server


def _is_ytia(url: str) -> bool:
    try:
        with urllib.request.urlopen(url + "api/state", timeout=2) as response:
            return json.load(response).get("app") == "ytia"
    except Exception:
        return False


def serve(
    paths: Paths,
    port: int = DEFAULT_PORT,
    open_browser: bool = True,
    announce: Callable[[str], None] = print,
) -> None:
    url = f"http://127.0.0.1:{port}/"
    try:
        server = make_server(App(paths), port)
    except OSError:
        if _is_ytia(url):
            announce(f"ytia is already open at {url}")
            if open_browser:
                webbrowser.open(url)
            return
        raise SystemExit(f"Port {port} is in use by a different program. Use --port to choose a different port.")
    announce(f"ytia is open at {url} . Keep this window open. Use 'Close ytia' on the page to stop.")
    if open_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main() -> None:
    """Entry point of the `ytia-ui` launcher, which has no console window on Windows."""
    home = os.environ.get("YTIA_HOME")
    paths = Paths.from_home(Path(home) if home else None)
    logging.basicConfig(
        filename=paths.log,
        encoding="utf-8",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    serve(paths, announce=log.info)

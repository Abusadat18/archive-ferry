"""SQLite state for channels, videos, settings and the event log.

Every status change commits at once, so a crash or a power cut loses at most
the step that was running. The worker reads the status and continues from there.
"""

from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Iterator

PENDING = "pending"
DOWNLOADING = "downloading"
DOWNLOADED = "downloaded"
UPLOADING = "uploading"
UPLOADED = "uploaded"
DONE = "done"
FAILED = "failed"
SKIPPED = "skipped"
STATUSES = (PENDING, DOWNLOADING, DOWNLOADED, UPLOADING, UPLOADED, DONE, FAILED, SKIPPED)

_MIGRATIONS = [
    """
    CREATE TABLE channels (
        id             INTEGER PRIMARY KEY,
        url            TEXT NOT NULL UNIQUE,
        yt_channel_id  TEXT,
        title          TEXT,
        ia_prefix      TEXT,
        added_at       REAL NOT NULL,
        last_synced_at REAL
    );
    CREATE TABLE videos (
        video_id        TEXT PRIMARY KEY,
        channel_id      INTEGER NOT NULL REFERENCES channels(id),
        title           TEXT,
        kind            TEXT,
        duration        INTEGER,
        upload_date     TEXT,
        sort_key        INTEGER NOT NULL,
        status          TEXT NOT NULL DEFAULT 'pending',
        failed_from     TEXT,
        attempts        INTEGER NOT NULL DEFAULT 0,
        -- failed rows: earliest retry time; uploaded rows: next Internet Archive check
        next_attempt_at REAL,
        last_error      TEXT,
        ia_identifier   TEXT,
        local_dir       TEXT,
        file_md5s       TEXT,
        added_at        REAL NOT NULL,
        updated_at      REAL NOT NULL
    );
    CREATE INDEX videos_status ON videos(status, sort_key);
    CREATE TABLE settings (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    CREATE TABLE events (
        id       INTEGER PRIMARY KEY,
        ts       REAL NOT NULL,
        level    TEXT NOT NULL,
        video_id TEXT,
        message  TEXT NOT NULL
    );
    """,
    # Local files go away after the upload, so the row keeps what is needed to prove and show it.
    """
    ALTER TABLE videos ADD COLUMN size_bytes INTEGER;
    ALTER TABLE videos ADD COLUMN uploaded_at REAL;
    ALTER TABLE videos ADD COLUMN upload_seconds REAL;
    """,
]


class Database:
    def __init__(self, path: Path | str):
        self.conn = sqlite3.connect(str(path), timeout=30, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._migrate()

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        self.conn.execute("COMMIT")

    def _migrate(self) -> None:
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        for number, sql in enumerate(_MIGRATIONS[version:], start=version + 1):
            self.conn.executescript(f"BEGIN; {sql}; PRAGMA user_version = {number}; COMMIT;")

    # Channels

    def add_channel(self, url: str, ia_prefix: str | None = None) -> sqlite3.Row:
        self.conn.execute(
            "INSERT OR IGNORE INTO channels (url, ia_prefix, added_at) VALUES (?, ?, ?)",
            (url, ia_prefix, time.time()),
        )
        return self.conn.execute("SELECT * FROM channels WHERE url = ?", (url,)).fetchone()

    def update_channel(self, channel_id: int, **fields: object) -> None:
        cols = ", ".join(f"{key} = ?" for key in fields)
        self.conn.execute(f"UPDATE channels SET {cols} WHERE id = ?", (*fields.values(), channel_id))

    def channel(self, channel_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM channels WHERE id = ?", (channel_id,)).fetchone()

    def find_channel(self, ref: str) -> sqlite3.Row | None:
        if ref.isdigit():
            return self.channel(int(ref))
        return self.conn.execute(
            "SELECT * FROM channels WHERE url = ? OR yt_channel_id = ?", (ref, ref)
        ).fetchone()

    def channels(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM channels ORDER BY id").fetchall()

    # Videos

    def add_videos(self, channel_id: int, entries: Iterable[dict]) -> int:
        """Insert new videos in the given order (oldest first). Known IDs stay as they are."""
        now = time.time()
        added = 0
        with self.transaction():
            (max_key,) = self.conn.execute("SELECT COALESCE(MAX(sort_key), 0) FROM videos").fetchone()
            for entry in entries:
                cur = self.conn.execute(
                    "INSERT OR IGNORE INTO videos"
                    " (video_id, channel_id, title, kind, duration, sort_key, added_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        entry["id"],
                        channel_id,
                        entry.get("title"),
                        entry.get("kind"),
                        entry.get("duration"),
                        max_key + added + 1,
                        now,
                        now,
                    ),
                )
                added += cur.rowcount
        return added

    def video(self, video_id: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM videos WHERE video_id = ?", (video_id,)).fetchone()

    def update_video(self, video_id: str, **fields: object) -> None:
        fields["updated_at"] = time.time()
        cols = ", ".join(f"{key} = ?" for key in fields)
        self.conn.execute(f"UPDATE videos SET {cols} WHERE video_id = ?", (*fields.values(), video_id))

    def next_video(self, now: float) -> sqlite3.Row | None:
        """Interrupted work first, then new videos (oldest first), then failed videos due for a retry."""
        return self.conn.execute(
            """
            SELECT * FROM videos
            WHERE status IN ('downloading', 'downloaded', 'uploading', 'pending')
               OR (status = 'failed' AND next_attempt_at <= ?)
            ORDER BY CASE status WHEN 'pending' THEN 1 WHEN 'failed' THEN 2 ELSE 0 END, sort_key
            LIMIT 1
            """,
            (now,),
        ).fetchone()

    def next_retry_at(self) -> float | None:
        return self.conn.execute(
            "SELECT MIN(next_attempt_at) FROM videos WHERE status = 'failed'"
        ).fetchone()[0]

    def due_for_check(self, now: float, limit: int = 10) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM videos WHERE status = 'uploaded' AND COALESCE(next_attempt_at, 0) <= ?"
            " ORDER BY next_attempt_at LIMIT ?",
            (now, limit),
        ).fetchall()

    def count(self, status: str) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM videos WHERE status = ?", (status,)).fetchone()[0]

    def counts(self, channel_id: int | None = None) -> dict[str, int]:
        sql = "SELECT status, COUNT(*) FROM videos"
        args: tuple = ()
        if channel_id is not None:
            sql += " WHERE channel_id = ?"
            args = (channel_id,)
        rows = self.conn.execute(sql + " GROUP BY status", args).fetchall()
        result = {status: 0 for status in STATUSES}
        result.update({row[0]: row[1] for row in rows})
        return result

    def videos(self, statuses: Iterable[str] | None = None, limit: int = 50) -> list[sqlite3.Row]:
        statuses = list(statuses or [])
        sql = "SELECT * FROM videos"
        if statuses:
            sql += f" WHERE status IN ({', '.join('?' * len(statuses))})"
        return self.conn.execute(sql + " ORDER BY sort_key LIMIT ?", (*statuses, limit)).fetchall()

    def requeue(self, statuses: Iterable[str]) -> int:
        """Put failed or skipped videos back in the queue. Upload failures keep their downloaded files."""
        statuses = list(statuses)
        cur = self.conn.execute(
            f"""
            UPDATE videos
            SET status = CASE WHEN failed_from = 'uploading' THEN 'downloaded' ELSE 'pending' END,
                attempts = 0, failed_from = NULL, last_error = NULL, next_attempt_at = NULL,
                updated_at = ?
            WHERE status IN ({', '.join('?' * len(statuses))})
            """,
            (time.time(), *statuses),
        )
        return cur.rowcount

    # Settings and runtime flags

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        row = self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row[0] if row else default

    def set_setting(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    # Event log

    def log(self, level: str, message: str, video_id: str | None = None) -> None:
        self.conn.execute(
            "INSERT INTO events (ts, level, video_id, message) VALUES (?, ?, ?, ?)",
            (time.time(), level, video_id, message),
        )

    def events_after(self, after_id: int, limit: int = 200) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM events WHERE id > ? ORDER BY id DESC LIMIT ?", (after_id, limit)
        ).fetchall()[::-1]

    def events(self, limit: int = 30) -> list[sqlite3.Row]:
        rows = self.conn.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return list(reversed(rows))

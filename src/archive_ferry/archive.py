"""Internet Archive identifiers, metadata, upload and verification."""

from __future__ import annotations

import hashlib
import html
import re
from pathlib import Path
from typing import Callable
from urllib.parse import quote

import internetarchive

from . import __version__
from .progress import Aborted

# The first upload sends metadata in HTTP headers, and a very long header makes
# the request fail. The full description is also in the uploaded .description file.
MAX_DESCRIPTION_BYTES = 6000
MAX_TAGS = 40
YOUTUBE_CC_LICENSE = "https://creativecommons.org/licenses/by/3.0/"
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{4,99}$")
_SHORTENED_NOTE = "\n\n[Description shortened. The full text is in the .description file of this item.]"


class IdentifierTaken(Exception):
    """The identifier already exists on the Internet Archive and holds other content."""


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40].strip("-")


def make_identifier(prefix: str, video_id: str) -> str:
    identifier = f"{prefix}-{video_id}"
    if not _IDENTIFIER.match(identifier):
        raise ValueError(f"Not a valid Internet Archive identifier: {identifier}")
    return identifier


def _format_date(upload_date: str | None) -> str | None:
    if upload_date and len(upload_date) == 8 and upload_date.isdigit():
        return f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:]}"
    return None


def _to_html(text: str) -> str:
    return html.escape(text).replace("\n", "<br />")


def _description(text: str) -> str:
    body = _to_html(text)
    if len(quote(body)) <= MAX_DESCRIPTION_BYTES:
        return body
    while text and len(quote(_to_html(text + _SHORTENED_NOTE))) > MAX_DESCRIPTION_BYTES:
        text = text[: int(len(text) * 0.9)]
    return _to_html(text + _SHORTENED_NOTE)


def build_metadata(info: dict, collection: str) -> dict:
    video_id = info["id"]
    url = f"https://www.youtube.com/watch?v={video_id}"
    tags = [tag for tag in (info.get("tags") or []) if tag][:MAX_TAGS]
    metadata = {
        "mediatype": "movies",
        "collection": collection,
        "title": info.get("title") or video_id,
        "creator": info.get("channel") or info.get("uploader"),
        "date": _format_date(info.get("upload_date")),
        "description": _description(info.get("description") or ""),
        "subject": ["YouTube", *tags],
        "originalurl": url,
        "source": url,
        "external-identifier": f"urn:youtube:{video_id}",
        "scanner": f"archive-ferry {__version__}",
    }
    if "creative commons" in (info.get("license") or "").lower():
        metadata["licenseurl"] = YOUTUBE_CC_LICENSE
    return {key: value for key, value in metadata.items() if value}


def file_md5(path: Path) -> str:
    digest = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class Uploader:
    def __init__(self, session: internetarchive.ArchiveSession | None = None):
        self.session = session or internetarchive.get_session()

    def has_credentials(self) -> bool:
        return bool(self.session.access_key and self.session.secret_key)

    def upload(
        self,
        identifier: str,
        files: list[Path],
        metadata: dict,
        md5s: dict[str, str],
        on_progress: Callable[[int, int], None] | None = None,
        should_abort: Callable[[], bool] | None = None,
    ) -> int:
        """Upload the files that are not on the item yet. Returns the number of bytes sent.

        on_progress gets (bytes sent, bytes to send) for all files together.
        should_abort can stop the upload between two network writes by returning True.
        """
        item = self.session.get_item(identifier)
        if item.exists and item.metadata.get("originalurl") != metadata["originalurl"]:
            raise IdentifierTaken(
                f"https://archive.org/details/{identifier} already exists and holds other content."
                " Use a different --prefix for this channel."
            )
        remote = {f.get("name"): f.get("md5") for f in item.files} if item.exists else {}
        todo = [path for path in files if remote.get(path.name) != md5s[path.name]]
        total = sum(path.stat().st_size for path in todo)
        sent_before = 0
        for index, path in enumerate(todo):
            current = 0

            def on_read(count: int | None) -> None:
                nonlocal current
                if should_abort is not None and should_abort():
                    raise Aborted()
                current = 0 if count is None else current + count
                if on_progress is not None:
                    on_progress(sent_before + current, total)

            with _ProgressReader(path, on_read) as body:
                # Content-MD5 makes the Internet Archive reject a file that arrives damaged.
                item.upload_file(
                    body,
                    key=path.name,
                    metadata=metadata,
                    headers={"Content-MD5": md5s[path.name]},
                    queue_derive=index == len(todo) - 1,
                    retries=10,
                    retries_sleep=30,
                )
            sent_before += path.stat().st_size
        return total

    def verify(self, identifier: str, md5s: dict[str, str]) -> bool:
        """True when every uploaded file is in the item with the same MD5."""
        if not md5s:
            return False
        item = self.session.get_item(identifier)
        if not item.exists:
            return False
        remote = {f.get("name"): f.get("md5") for f in item.files}
        return all(remote.get(name) == md5 for name, md5 in md5s.items())


class _ProgressReader:
    """A read-only file that reports each read of the HTTP client.

    A seek to the start means a new attempt (the client retries), so the count restarts.
    """

    def __init__(self, path: Path, on_read: Callable[[int | None], None]):
        self._fh = open(path, "rb")
        self._on_read = on_read
        self.name = str(path)

    def read(self, size: int = -1) -> bytes:
        data = self._fh.read(size)
        if data:
            self._on_read(len(data))
        return data

    def seek(self, offset: int, whence: int = 0) -> int:
        position = self._fh.seek(offset, whence)
        if position == 0:
            self._on_read(None)
        return position

    def tell(self) -> int:
        return self._fh.tell()

    def fileno(self) -> int:
        return self._fh.fileno()

    def close(self) -> None:
        self._fh.close()

    def __enter__(self) -> "_ProgressReader":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

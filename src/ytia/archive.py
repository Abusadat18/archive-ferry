"""Internet Archive identifiers, metadata, upload and verification."""

from __future__ import annotations

import hashlib
import html
import re
from pathlib import Path
from urllib.parse import quote

import internetarchive

from . import __version__

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
        "scanner": f"ytia {__version__}",
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

    def upload(self, identifier: str, files: list[Path], metadata: dict) -> None:
        item = self.session.get_item(identifier)
        if item.exists and item.metadata.get("originalurl") != metadata["originalurl"]:
            raise IdentifierTaken(
                f"https://archive.org/details/{identifier} already exists and holds other content."
                " Use a different --prefix for this channel."
            )
        responses = item.upload(
            {path.name: str(path) for path in files},
            metadata=metadata,
            queue_derive=True,
            verify=True,
            checksum=True,
            retries=10,
            retries_sleep=30,
        )
        for response in responses:
            # checksum=True returns an empty Response (no status) for files that are already there.
            if response.status_code not in (None, 200):
                raise RuntimeError(f"Upload failed: HTTP {response.status_code} {response.text[:200]}")

    def verify(self, identifier: str, md5s: dict[str, str]) -> bool:
        """True when every uploaded file is in the item with the same MD5."""
        if not md5s:
            return False
        item = self.session.get_item(identifier)
        if not item.exists:
            return False
        remote = {f.get("name"): f.get("md5") for f in item.files}
        return all(remote.get(name) == md5 for name, md5 in md5s.items())

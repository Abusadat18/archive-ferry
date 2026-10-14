"""Read the video list of a channel and add new videos to the queue."""

from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import urlparse

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

from .archive import slugify
from .config import Settings
from .db import Database
from .ytdl import base_options

TABS = {"videos": "video", "shorts": "short", "streams": "live"}
_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com"}
_CHANNEL_PATH = re.compile(r"^/(@[^/]+|channel/[^/]+|c/[^/]+|user/[^/]+)")
# Upcoming premieres and running streams cannot be downloaded yet. A later resync adds them.
_NOT_READY = {"is_live", "is_upcoming"}


@dataclass
class ChannelListing:
    channel_id: str | None = None
    title: str | None = None
    entries: list[dict] = field(default_factory=list)


Lister = Callable[[str, Settings], ChannelListing]


def normalize_channel_url(url: str) -> str:
    """Return https://www.youtube.com/<@handle|channel/ID|c/name|user/name> without a tab suffix."""
    url = url.strip()
    if url.startswith("@"):
        url = "https://www.youtube.com/" + url
    elif not re.match(r"^https?://", url):
        url = "https://" + url
    parsed = urlparse(url)
    match = _CHANNEL_PATH.match(parsed.path)
    if parsed.netloc.lower() not in _HOSTS or not match:
        raise ValueError(
            f"Not a YouTube channel URL: {url}\n"
            "Use a URL like https://www.youtube.com/@handle or https://www.youtube.com/channel/UC..."
        )
    return f"https://www.youtube.com/{match.group(1)}"


def fetch_listing(base_url: str, settings: Settings) -> ChannelListing:
    listing = ChannelListing()
    opts = {**base_options(settings), "extract_flat": "in_playlist", "skip_download": True}
    found_tab = False
    with YoutubeDL(opts) as ydl:
        for tab, kind in TABS.items():
            try:
                info = ydl.extract_info(f"{base_url}/{tab}", download=False)
            except DownloadError as exc:
                if "does not have a" in str(exc):
                    continue
                raise
            found_tab = True
            listing.channel_id = listing.channel_id or info.get("channel_id")
            listing.title = listing.title or info.get("channel") or info.get("uploader")
            # YouTube lists newest first. The queue works oldest first.
            for entry in reversed(list(info.get("entries") or [])):
                if not entry or not entry.get("id") or entry.get("live_status") in _NOT_READY:
                    continue
                duration = entry.get("duration")
                listing.entries.append(
                    {
                        "id": entry["id"],
                        "title": entry.get("title"),
                        "kind": kind,
                        "duration": int(duration) if duration else None,
                    }
                )
    if not found_tab:
        raise ValueError(f"No videos, shorts or streams found at {base_url}")
    return listing


def default_prefix(title: str | None, url: str) -> str:
    handle = url.rstrip("/").rsplit("/", 1)[-1].lstrip("@")
    return slugify(title or "") or slugify(handle) or "youtube"


def sync_channel(
    db: Database, channel: sqlite3.Row, settings: Settings, lister: Lister = fetch_listing
) -> tuple[int, int]:
    """Add new videos of a channel to the queue. Returns (videos found, videos added)."""
    listing = lister(channel["url"], settings)
    added = db.add_videos(channel["id"], listing.entries)
    fields: dict[str, object] = {"last_synced_at": time.time()}
    if listing.channel_id:
        fields["yt_channel_id"] = listing.channel_id
    if listing.title:
        fields["title"] = listing.title
    if not channel["ia_prefix"]:
        fields["ia_prefix"] = default_prefix(listing.title, channel["url"])
    db.update_channel(channel["id"], **fields)
    db.log("info", f"Synced {listing.title or channel['url']}: {len(listing.entries)} found, {added} new.")
    return len(listing.entries), added

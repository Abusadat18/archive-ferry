import pytest

from archive_ferry.sync import ChannelListing, default_prefix, normalize_channel_url, sync_channel


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://www.youtube.com/@demo", "https://www.youtube.com/@demo"),
        ("https://youtube.com/@demo/videos", "https://www.youtube.com/@demo"),
        ("youtube.com/@demo/shorts/", "https://www.youtube.com/@demo"),
        ("@demo", "https://www.youtube.com/@demo"),
        ("https://m.youtube.com/channel/UC123/featured", "https://www.youtube.com/channel/UC123"),
        ("https://www.youtube.com/c/Demo?view=0", "https://www.youtube.com/c/Demo"),
    ],
)
def test_normalize_channel_url(url, expected):
    assert normalize_channel_url(url) == expected


@pytest.mark.parametrize("url", ["https://www.youtube.com/watch?v=abc", "https://vimeo.com/@demo"])
def test_normalize_rejects_other_urls(url):
    with pytest.raises(ValueError):
        normalize_channel_url(url)


def test_default_prefix_falls_back_to_handle():
    assert default_prefix("Demo Channel!", "https://www.youtube.com/@x") == "demo-channel"
    assert default_prefix("हिन्दी", "https://www.youtube.com/@my_handle") == "my-handle"


def test_resync_adds_only_new_videos(db, settings):
    channel = db.add_channel("https://www.youtube.com/@demo")
    first = ChannelListing("UC1", "Demo Channel", [{"id": "aaaaaaaaaaa"}, {"id": "bbbbbbbbbbb"}])
    assert sync_channel(db, channel, settings, lister=lambda url, s: first) == (2, 2)

    channel = db.channel(channel["id"])
    assert channel["ia_prefix"] == "demo-channel"
    assert channel["yt_channel_id"] == "UC1"

    db.update_video("aaaaaaaaaaa", status="done")
    second = ChannelListing("UC1", "Demo Channel", [*first.entries, {"id": "ccccccccccc"}])
    assert sync_channel(db, channel, settings, lister=lambda url, s: second) == (3, 1)
    assert db.video("aaaaaaaaaaa")["status"] == "done"
    assert db.video("ccccccccccc")["sort_key"] > db.video("bbbbbbbbbbb")["sort_key"]

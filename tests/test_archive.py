from urllib.parse import quote

import pytest

from ytia.archive import MAX_DESCRIPTION_BYTES, YOUTUBE_CC_LICENSE, build_metadata, make_identifier


def test_make_identifier():
    assert make_identifier("demo", "-abc_DEF123") == "demo--abc_DEF123"
    with pytest.raises(ValueError):
        make_identifier("-bad", "abc")


def test_build_metadata():
    info = {
        "id": "abc",
        "title": "Hello",
        "channel": "Demo",
        "upload_date": "20240131",
        "description": "Line 1\n<b>Line 2</b>",
        "tags": ["one", "", "two"],
        "license": "Creative Commons Attribution license (reuse allowed)",
    }
    md = build_metadata(info, "opensource_movies")
    assert md["mediatype"] == "movies"
    assert md["date"] == "2024-01-31"
    assert md["description"] == "Line 1<br />&lt;b&gt;Line 2&lt;/b&gt;"
    assert md["subject"] == ["YouTube", "one", "two"]
    assert md["originalurl"] == "https://www.youtube.com/watch?v=abc"
    assert md["licenseurl"] == YOUTUBE_CC_LICENSE


def test_long_description_is_shortened():
    md = build_metadata({"id": "abc", "description": "ü" * 20000}, "opensource_movies")
    assert len(quote(md["description"])) <= MAX_DESCRIPTION_BYTES
    assert md["description"].endswith("The full text is in the .description file of this item.]")
    assert "licenseurl" not in md

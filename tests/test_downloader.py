import json

from archive_ferry.downloader import clean_info_json, completed_files


def test_completed_files_skips_partial_files(tmp_path):
    names = [
        "abc.mp4",
        "abc.info.json",
        "abc.jpg",
        "abc.en.vtt",
        "abc.fr.vtt",
        "abc.description",
        "abc.f137.mp4",
        "abc.f251-1.webm",
        "abc.mp4.part",
        "abc.mp4.part-Frag3",
        "abc.mp4.ytdl",
        "abc.temp.mp4",
    ]
    for name in names:
        (tmp_path / name).write_text("x")
    assert [p.name for p in completed_files(tmp_path)] == [
        "abc.description",
        "abc.en.vtt",
        "abc.fr.vtt",
        "abc.info.json",
        "abc.jpg",
        "abc.mp4",
    ]


def test_clean_info_json_removes_private_data(tmp_path):
    path = tmp_path / "abc.info.json"
    path.write_text(
        json.dumps(
            {
                "id": "abc",
                "title": "Hello",
                "formats": [{"url": "https://rr1.googlevideo.com/videoplayback?ip=1.2.3.4"}],
                "requested_downloads": [{"filepath": "C:\\Users\\me\\abc.mp4"}],
                "_filename": "C:\\Users\\me\\abc.mp4",
                "__files_to_move": {},
                "thumbnails": [{"id": "0", "url": "https://i.ytimg.com/x.jpg"}],
                "subtitles": {"en": [{"ext": "vtt", "url": "https://www.youtube.com/api/timedtext?ip=1.2.3.4"}]},
                "note": "https://rr1.googlevideo.com/x",
            }
        ),
        encoding="utf-8",
    )
    clean = clean_info_json(path)
    text = path.read_text(encoding="utf-8")
    assert "1.2.3.4" not in text and "Users" not in text and "googlevideo" not in text
    assert clean["title"] == "Hello"
    assert clean["subtitles"] == {"en": [{"ext": "vtt"}]}
    assert clean["thumbnails"] == [{"id": "0"}]

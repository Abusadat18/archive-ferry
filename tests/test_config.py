from pathlib import Path

import pytest

from archive_ferry.config import Paths, default_home, home_from_env


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    return tmp_path


def test_default_home_is_archive_ferry(fake_home):
    assert default_home() == fake_home / "archive-ferry"


def test_existing_ytia_folder_is_kept(fake_home):
    (fake_home / "ytia").mkdir()
    assert default_home() == fake_home / "ytia"
    (fake_home / "archive-ferry").mkdir()
    assert default_home() == fake_home / "archive-ferry"


def test_existing_ytia_database_is_kept(tmp_path):
    paths = Paths.from_home(tmp_path)
    assert paths.db.name == "archive-ferry.db"
    (tmp_path / "ytia.db").touch()
    assert paths.db.name == "ytia.db"


def test_home_from_env(monkeypatch, tmp_path):
    monkeypatch.delenv("ARCHIVE_FERRY_HOME", raising=False)
    monkeypatch.delenv("YTIA_HOME", raising=False)
    assert home_from_env() is None
    monkeypatch.setenv("YTIA_HOME", str(tmp_path / "old"))
    assert home_from_env() == tmp_path / "old"
    monkeypatch.setenv("ARCHIVE_FERRY_HOME", str(tmp_path / "new"))
    assert home_from_env() == tmp_path / "new"

from __future__ import annotations

from pathlib import Path

import pytest

from archive_ferry.config import Paths, Settings
from archive_ferry.db import Database


@pytest.fixture
def paths(tmp_path: Path) -> Paths:
    return Paths.from_home(tmp_path / "home")


@pytest.fixture
def db(paths: Paths) -> Database:
    database = Database(paths.db)
    yield database
    database.close()


@pytest.fixture
def settings(db: Database) -> Settings:
    s = Settings(db)
    s.set("pause_between", "0")
    s.set("min_free_gb", "0")
    return s


@pytest.fixture
def channel(db: Database):
    row = db.add_channel("https://www.youtube.com/@demo", "demo")
    db.add_videos(row["id"], [{"id": f"vid{i:08d}", "title": f"Video {i}"} for i in range(1, 4)])
    return db.channel(row["id"])

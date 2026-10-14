import json
import time

from typer.testing import CliRunner

from ytia.cli import app
from ytia.db import Database

runner = CliRunner()


def test_status_shows_speeds(tmp_path):
    home = tmp_path / "home"
    assert runner.invoke(app, ["--home", str(home), "status"]).exit_code == 0
    db = Database(home / "ytia.db")
    db.set_setting("speeds", json.dumps({"upload": {"bps": 1_250_000, "at": time.time()}}))
    db.close()
    result = runner.invoke(app, ["--home", str(home), "status"])
    assert result.exit_code == 0, result.output
    assert "Last upload speed: 1.2 MB/s (10.0 Mbit/s)" in result.output


def test_config_rejects_bad_value(tmp_path):
    result = runner.invoke(app, ["--home", str(tmp_path), "config", "set", "merge_format", "avi"])
    assert result.exit_code == 2

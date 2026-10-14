"""Command line interface."""

from __future__ import annotations

import json
import logging
import signal
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from . import __version__
from .archive import Uploader, slugify
from .config import DEFAULTS, HELP, Paths, Settings
from .db import DONE, FAILED, SKIPPED, STATUSES, Database
from .lock import WorkerLock
from .power import KeepAwake
from .progress import current_transfer, fmt_eta, fmt_size, fmt_speed
from .sync import normalize_channel_url, sync_channel

app = typer.Typer(
    help="Mirror a YouTube channel to the Internet Archive, one video at a time.",
    no_args_is_help=True,
    add_completion=False,
)
config_app = typer.Typer(help="Show or change settings.", no_args_is_help=True)
app.add_typer(config_app, name="config")

console = Console(highlight=False)
_STYLES = {"info": "", "success": "green", "warning": "yellow", "error": "red"}


class _State:
    paths: Paths


state = _State()


def _version(value: bool) -> None:
    if value:
        console.print(f"ytia {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    home: Optional[Path] = typer.Option(
        None, "--home", envvar="YTIA_HOME", help="Folder for the database and downloads. Default: ~/ytia"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Write debug messages to ytia.log."),
    version: bool = typer.Option(False, "--version", callback=_version, is_eager=True, help="Show the version."),
) -> None:
    state.paths = Paths.from_home(home)
    logging.basicConfig(
        filename=state.paths.log,
        encoding="utf-8",
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _db() -> Database:
    return Database(state.paths.db)


def _time(ts: float | None) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else "never"


def _fail(message: str, code: int = 1) -> None:
    console.print(f"[red]{escape(message)}[/]")
    raise typer.Exit(code)


def _sync(db: Database, channel) -> None:
    with console.status(f"Reading the video list of {channel['url']} ..."):
        try:
            found, added = sync_channel(db, channel, Settings(db))
        except Exception as exc:
            _fail(f"Sync failed: {exc}")
    channel = db.channel(channel["id"])
    console.print(
        f"[green]{escape(channel['title'] or channel['url'])}[/]: {found} videos found, {added} new."
        f" Internet Archive prefix: [bold]{channel['ia_prefix']}[/]"
    )


@app.command()
def add(
    url: str = typer.Argument(..., help="Channel URL, for example https://www.youtube.com/@handle"),
    prefix: Optional[str] = typer.Option(
        None, help="Start of each Internet Archive identifier, for example 'mychannel'. Default: from the channel name."
    ),
) -> None:
    """Add a channel and read its video list."""
    try:
        base = normalize_channel_url(url)
    except ValueError as exc:
        _fail(str(exc), 2)
    if prefix is not None and slugify(prefix) != prefix.lower():
        _fail("The prefix can contain only letters, digits and dashes, for example 'my-channel'.", 2)
    db = _db()
    channel = db.add_channel(base, prefix.lower() if prefix else None)
    _sync(db, channel)
    console.print("Next step: [bold]ytia run[/]")


@app.command()
def sync(channel: Optional[str] = typer.Argument(None, help="Channel ID number or URL. Default: all channels.")) -> None:
    """Read the video lists again and add only the new videos."""
    db = _db()
    if channel is None:
        rows = db.channels()
        if not rows:
            _fail("No channels. Add one with: ytia add <channel-url>")
    else:
        ref = channel if channel.isdigit() else normalize_channel_url(channel)
        row = db.find_channel(ref)
        if row is None:
            _fail(f"Unknown channel: {channel}. Run `ytia channels` to see the list.")
        rows = [row]
    for row in rows:
        _sync(db, row)


@app.command()
def channels() -> None:
    """Show the channels and their progress."""
    db = _db()
    table = Table("ID", "Channel", "IA prefix", "Done", "Total", "Last sync")
    for row in db.channels():
        counts = db.counts(row["id"])
        table.add_row(
            str(row["id"]),
            escape(row["title"] or row["url"]),
            row["ia_prefix"] or "",
            str(counts[DONE]),
            str(sum(counts.values())),
            _time(row["last_synced_at"]),
        )
    console.print(table)


@app.command()
def run() -> None:
    """Start the worker. It downloads and uploads one video at a time until the queue is empty."""
    from .worker import Worker

    db = _db()
    if not db.channels():
        _fail("No channels. Add one with: ytia add <channel-url>")
    lock = WorkerLock(state.paths.lock)
    if not lock.acquire():
        _fail("A worker is already running. Use `ytia status` to see what it does.")
    try:
        uploader = Uploader()
        if not uploader.has_credentials():
            _fail("No Internet Archive keys found. Run `ia configure` and log in, then try again.")
        settings = Settings(db)
        worker = Worker(
            db,
            settings,
            state.paths,
            uploader,
            out=lambda message, level: console.print(
                f"[dim]{datetime.now():%H:%M:%S}[/] [{_STYLES.get(level) or 'default'}]{escape(message)}[/]"
            ),
        )

        def on_sigint(signum, frame):
            if worker.stop_requested:
                raise KeyboardInterrupt
            worker.request_stop()
            console.print("[yellow]Stopping after the current step. Press Ctrl+C again to stop now.[/]")

        signal.signal(signal.SIGINT, on_sigint)
        if settings.paused:
            console.print("[yellow]The queue is paused. Run `ytia resume` in another terminal to continue.[/]")
        console.print(f"Worker started. Data folder: {state.paths.home}. Press Ctrl+C to stop.")
        with KeepAwake():
            worker.run()
        console.print("Worker stopped. Run [bold]ytia run[/] to continue from the same place.")
    except KeyboardInterrupt:
        console.print("[yellow]Stopped. Run `ytia run` to continue from the same place.[/]")
    finally:
        db.set_setting("activity", "Idle")
        lock.release()


@app.command()
def pause(
    now: bool = typer.Option(
        False, "--now", help="Stop the current download or upload at once. Without this option, the current step completes first."
    ),
) -> None:
    """Pause the worker. Use `ytia resume` to continue."""
    db = _db()
    db.set_setting("paused", "now" if now else "1")
    if not WorkerLock.is_held(state.paths.lock):
        console.print("Paused. No worker is running now.")
    elif now:
        console.print("Paused. The worker stops the current download or upload now.")
    else:
        console.print("Paused. The worker completes the current step, then waits.")


@app.command()
def resume() -> None:
    """Continue after a pause."""
    db = _db()
    db.set_setting("paused", "0")
    if WorkerLock.is_held(state.paths.lock):
        console.print("Resumed.")
    else:
        console.print("Resumed. No worker is running. Start one with: [bold]ytia run[/]")


@app.command()
def status() -> None:
    """Show the queue, the worker and the current activity."""
    db = _db()
    settings = Settings(db)
    counts = db.counts()
    total = sum(counts.values())
    table = Table("Status", "Videos")
    for name in STATUSES:
        if counts[name]:
            table.add_row(name, str(counts[name]))
    table.add_row("[bold]total[/]", f"[bold]{total}[/]")
    console.print(table)
    if total:
        console.print(f"Progress: {counts[DONE] * 100 // total}% done")
    running = WorkerLock.is_held(state.paths.lock)
    console.print(f"Worker: {'[green]running[/]' if running else 'not running'}")
    console.print(f"Queue: {'[yellow]paused[/]' if settings.paused else 'active'}")
    if running:
        console.print(f"Now: {escape(db.get_setting('activity', 'Idle'))}")
        transfer = current_transfer(db)
        if transfer:
            verb = "Downloading" if transfer["phase"] == "download" else "Uploading"
            done, total = transfer["done"], transfer["total"]
            amount = f"{fmt_size(done)} of {fmt_size(total)}" if total else fmt_size(done)
            console.print(f"{verb}: {escape(transfer['title'])}")
            console.print(f"  {amount} · {fmt_speed(transfer['speed'])} · {fmt_eta(transfer['eta'])}")
    speeds = json.loads(db.get_setting("speeds") or "{}")
    for phase, label in (("download", "Download"), ("upload", "Upload")):
        if phase in speeds:
            measured = speeds[phase]
            console.print(f"Last {label.lower()} speed: {fmt_speed(measured['bps'])} ({_time(measured['at'])})")
    if counts[FAILED] or counts[SKIPPED]:
        console.print("See the problems with: ytia list --status failed --status skipped")


@app.command(name="list")
def list_videos(
    status_filter: List[str] = typer.Option([], "--status", "-s", help=f"Show only this status: {', '.join(STATUSES)}."),
    limit: int = typer.Option(50, help="Maximum number of rows."),
) -> None:
    """List videos in the queue."""
    unknown = set(status_filter) - set(STATUSES)
    if unknown:
        _fail(f"Unknown status: {', '.join(sorted(unknown))}. Use one of: {', '.join(STATUSES)}", 2)
    table = Table("Video ID", "Title", "Status", "Tries", "Archive item / last error")
    for row in _db().videos(status_filter, limit):
        detail = row["last_error"] if row["status"] in (FAILED, SKIPPED) else (row["ia_identifier"] or "")
        table.add_row(
            row["video_id"],
            escape((row["title"] or "")[:60]),
            row["status"],
            str(row["attempts"]),
            escape(detail or ""),
        )
    console.print(table)


@app.command()
def retry(skipped: bool = typer.Option(False, "--skipped", help="Also retry skipped videos.")) -> None:
    """Put failed videos back in the queue."""
    count = _db().requeue([FAILED, SKIPPED] if skipped else [FAILED])
    console.print(f"{count} videos are back in the queue.")


@app.command(name="log")
def show_log(lines: int = typer.Option(30, "--lines", "-n", help="Number of events to show.")) -> None:
    """Show the most recent events."""
    for row in _db().events(lines):
        style = _STYLES.get(row["level"]) or "default"
        who = f"{row['video_id']}: " if row["video_id"] else ""
        console.print(f"[dim]{_time(row['ts'])}[/] [{style}]{escape(who + row['message'])}[/]")


@config_app.command("show")
def config_show() -> None:
    """Show all settings."""
    settings = Settings(_db())
    table = Table("Setting", "Value", "Description")
    for key in DEFAULTS:
        value = settings.get(key)
        table.add_row(key, escape(value) if value else "[dim](empty)[/]", HELP[key])
    console.print(table)


@config_app.command("set")
def config_set(key: str, value: str = typer.Argument(..., help='New value. Use "" for an empty value.')) -> None:
    """Change a setting."""
    try:
        stored = Settings(_db()).set(key, value)
    except ValueError as exc:
        _fail(str(exc), 2)
    console.print(f"{key} = {stored or '(empty)'}")


@app.command()
def doctor() -> None:
    """Check that all tools and keys are ready."""
    from .doctor import run_checks

    checks = run_checks(state.paths, Settings(_db()))
    table = Table("Check", "Result", "Fix")
    for check in checks:
        mark = "[green]OK[/]" if check.ok else "[red]MISSING[/]"
        table.add_row(check.name, f"{mark} {escape(check.detail)}", "" if check.ok else escape(check.fix))
    console.print(table)
    if not all(check.ok for check in checks):
        raise typer.Exit(1)
    console.print("[green]All checks passed.[/]")


@app.command()
def ui(
    port: int = typer.Option(8765, help="Port of the local web page."),
    no_browser: bool = typer.Option(False, "--no-browser", help="Do not open the browser."),
) -> None:
    """Open the web interface in your browser. It runs only on this computer."""
    from .web import serve

    serve(state.paths, port=port, open_browser=not no_browser, announce=console.print)

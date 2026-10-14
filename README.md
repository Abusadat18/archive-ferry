# ytia — YouTube to Internet Archive

`ytia` copies all videos of a YouTube channel to the [Internet Archive](https://archive.org).
It works on one video at a time: download, upload, verify, delete the local copy, then the next video.

- **Pause and resume.** Stop at any time and continue later from the same point. You can let it run overnight.
- **Resync.** Run `ytia sync` after new uploads. The tool adds only the new videos to the queue.
- **Crash-safe.** A SQLite database records each step. After a crash or a power cut, the next `ytia run` continues the interrupted step.
- **Verified.** The tool deletes a local file only after the Internet Archive shows the same MD5 checksum.

> **Use this tool only for content that you own or have the rights to share.**
> Obey the YouTube Terms of Service and the Internet Archive [Terms of Use](https://archive.org/about/terms.php).

## Requirements

- Python 3.10 or later
- [ffmpeg](https://ffmpeg.org) on your `PATH`
- [Deno](https://deno.com) on your `PATH`. yt-dlp needs a JavaScript runtime for YouTube.
- An [Internet Archive account](https://archive.org/account/signup)

On Windows, install the tools with winget:

```powershell
winget install Gyan.FFmpeg
winget install DenoLand.Deno
```

## Install

```bash
pipx install git+https://github.com/<you>/ytia
# or, with uv:
uv tool install git+https://github.com/<you>/ytia
```

## Use the web page (no terminal)

Start `ytia-ui`. On Windows, this program opens without a console window. It is in the same folder as `ytia`. You can pin it to the Start menu or put a shortcut on the desktop. You can also run `ytia ui` in a terminal.

The page opens in your browser at `http://127.0.0.1:8765`. It runs only on your computer. On the page, you can:

1. Log in to the Internet Archive (**Setup** tab).
2. Paste a channel URL and click **Add channel**.
3. Click **Start**, **Pause**, **Resume** or **Stop**.
4. Click **Check for new videos** after new uploads.
5. See the progress, the list of videos, the activity log and the settings.

Keep the computer on while the worker runs. You can close the browser tab: the work continues. Click **Close ytia** at the bottom of the page to stop everything.

## Test safely first

Use the Internet Archive test collection for the first runs. The Internet Archive deletes items in `test_collection` after about 30 days.
Use a separate data folder, so that the test queue does not mix with the real queue.

```bash
ytia --home ~/ytia-test config set collection test_collection
ytia --home ~/ytia-test add https://www.youtube.com/@yourchannel --prefix test-yourchannel
ytia --home ~/ytia-test run
```

Stop the worker after 2 or 3 videos. Examine the items on archive.org. Then delete the `~/ytia-test` folder and start the real run.

## Quick start (terminal)

1. Connect your Internet Archive account. The command asks for your e-mail and password and stores the keys in `ia.ini`.

   ```bash
   ia configure
   ```

2. Check the setup.

   ```bash
   ytia doctor
   ```

3. Add the channel. The tool reads the list of videos, shorts and streams.

   ```bash
   ytia add https://www.youtube.com/@yourchannel --prefix yourchannel
   ```

4. Start the worker.

   ```bash
   ytia run
   ```

Each video becomes one Internet Archive item: `https://archive.org/details/<prefix>-<video id>`.
Choose the prefix carefully. The identifier of an item cannot change after the upload.

## Daily use

| Task | Command |
|---|---|
| Start or continue the work | `ytia run` |
| Pause after the current step | `ytia pause` (or press Ctrl+C one time in the worker window) |
| Pause the current download at once | `ytia pause --now` |
| Continue after a pause | `ytia resume` |
| Add new uploads to the queue | `ytia sync` |
| See the progress | `ytia status` |
| See recent events | `ytia log` |
| See failed videos | `ytia list --status failed --status skipped` |
| Try failed videos again | `ytia retry` (add `--skipped` for skipped videos) |
| Open the web page | `ytia ui` |

Press Ctrl+C two times to stop the worker at once. The next `ytia run` continues the interrupted step.
yt-dlp continues a partial download. An interrupted upload starts again for the file that was not complete.

## Settings

Run `ytia config show` to see all settings. Run `ytia config set <key> <value>` to change one.

| Setting | Default | Description |
|---|---|---|
| `format` | `bv*+ba/b` | yt-dlp format selector |
| `merge_format` | `mp4` | Container for merged video and audio |
| `subtitles` | `true` | Download and upload subtitles |
| `cookies_from_browser` | | Read YouTube cookies from a browser, for example `firefox` |
| `cookies_file` | | Path to a `cookies.txt` file |
| `js_runtime` | | `deno` (default), `node`, `bun` or `quickjs` |
| `rate_limit` | | Maximum download speed, for example `5M` |
| `pause_between` | `10` | Seconds between two videos |
| `max_attempts` | `3` | Attempts for each video before the worker skips it |
| `max_unverified` | `3` | Uploads that can wait for confirmation before the next download starts |
| `min_free_gb` | `5` | The worker pauses when the free disk space is less than this |
| `keep_files` | `false` | Keep local files after a confirmed upload |
| `collection` | `opensource_movies` | Internet Archive collection |

## How it works

```
pending → downloading → downloaded → uploading → uploaded → done
              │                          │           (MD5 confirmed, local files deleted)
              └────────► failed ◄────────┘
                (retry with backoff, then skipped)
```

- The worker takes interrupted work first. Then it takes new videos, oldest first.
- A failed video waits 5 minutes, then 10 minutes, then 20 minutes, and so on.
- If YouTube asks for a sign-in ("confirm you're not a bot"), the worker pauses the queue. Set `cookies_from_browser`, then run `ytia resume`.
- When the free disk space is too low, the worker pauses the queue.
- On Windows and macOS, the computer does not go to sleep while the worker runs. On Linux, use `systemd-inhibit ytia run`.

### Files in each item

- The video
- `<id>.info.json` — YouTube metadata. The tool removes the signed stream URLs (they contain your IP address) and local paths before the upload.
- `<id>.jpg` — the thumbnail
- `<id>.description` — the full description
- `<id>.<lang>.vtt` — subtitles

### Data folder

All data is in `~/ytia`. To use a different folder, for example a large external disk, use `--home` or set `YTIA_HOME`:

```bash
ytia --home D:\ytia run
```

| File | Content |
|---|---|
| `ytia.db` | The queue and the settings |
| `ytia.log` | The detailed log |
| `downloads/` | Videos that are not uploaded yet |

## Development

```bash
uv sync
uv run pytest
uv run ytia --help
```

## License

To be decided before the first release. The `internetarchive` library uses AGPL-3.0.

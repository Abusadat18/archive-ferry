# archive-ferry: archive your YouTube channel to the Internet Archive

`archive-ferry` copies all videos, shorts and streams of a YouTube channel to the [Internet Archive](https://archive.org) (archive.org). It uses [yt-dlp](https://github.com/yt-dlp/yt-dlp) to download and the official `internetarchive` library to upload.
It works on one video at a time: download, upload, delete the local copy, then the next video.

- **Pause and resume.** Stop at any time and continue later from the same point. You can let it run overnight.
- **Resync.** After new uploads on YouTube, check the channel again. archive-ferry adds only the new videos to the queue.
- **Crash-safe.** A SQLite database records each step. After a crash or a power cut, archive-ferry continues the interrupted step.
- **Small disk use.** archive-ferry deletes the video files from your computer right after each upload. The database keeps only a record of each video.
- **Verified.** The Internet Archive checks the MD5 checksum of each file when it arrives. Later, archive-ferry checks that the item shows the same checksums.
- **Speed and time left.** archive-ferry shows the download speed, the upload speed and the time left for the current video.
- **Web page or terminal.** Use the local web page, or use the `archive-ferry` command.

> **Status: beta (v0.1).** The tests pass, but few real channels have gone through archive-ferry yet.
> Test it with the Internet Archive test collection first (see [Test safely first](#test-safely-first)).

## Disclaimer

**Use archive-ferry only for content that you own or that you have the right to copy and share.**

- You alone are responsible for what you download and what you upload with archive-ferry.
- The authors and contributors of archive-ferry are **not responsible** for any use of this tool. This includes the upload of copyrighted content without permission, a breach of the YouTube Terms of Service or of the Internet Archive [Terms of Use](https://archive.org/about/terms.php), and any loss of data or of accounts.
- archive-ferry is not affiliated with, endorsed by or connected to YouTube, Google or the Internet Archive.
- archive-ferry comes **without any warranty**. Sections 15 and 16 of the [license](LICENSE) apply.

This section is not legal advice. If you are not sure that you have the rights to a video, do not upload it.

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

archive-ferry is not on PyPI yet. Install it from the source code with [uv](https://docs.astral.sh/uv/) or [pipx](https://pipx.pypa.io):

```bash
git clone https://github.com/Abusadat18/archive-ferry.git
cd archive-ferry
uv tool install .
# or: pipx install .
```

This adds two commands: `archive-ferry` (terminal) and `archive-ferry-ui` (web page).

### Update

YouTube changes often, and old versions of yt-dlp stop working. Update archive-ferry and its tools often:

```bash
git pull
uv tool install --reinstall .
# or: pipx install --force .
```

## Use the web page

Start `archive-ferry-ui`. On Windows, it opens without a console window, so you can pin it to the Start menu. You can also run `archive-ferry ui` in a terminal.

The page opens in your browser at `http://127.0.0.1:8765`. It runs only on your computer.

1. Open **Setup** and log in to the Internet Archive. Your password goes only to archive.org; archive-ferry keeps only the upload keys.
2. Under **Channels**, paste the channel URL and click **Add channel**.
3. Click **Start**. Use **Pause**, **Resume** or **Stop** at any time.
4. After new uploads on YouTube, click **Check for new videos**.

The front page shows the current download or upload with its speed and time left, the queue, and the last measured line speed.
The sections at the bottom show the list of videos, the activity log (**Dispatches**), the settings and the setup checks.

Keep the computer on while the worker runs. You can close the browser tab: the work continues. Click **Close archive-ferry** at the bottom of the page to stop everything.

## Test safely first

Use the Internet Archive test collection for the first runs. The Internet Archive deletes items in `test_collection` after about 30 days.
Use a separate data folder, so that the test queue does not mix with the real queue.

```bash
archive-ferry --home ~/archive-ferry-test config set collection test_collection
archive-ferry --home ~/archive-ferry-test add https://www.youtube.com/@yourchannel --prefix test-yourchannel
archive-ferry --home ~/archive-ferry-test run
```

Stop the worker after 2 or 3 videos. Examine the items on archive.org. Then delete the `~/archive-ferry-test` folder and start the real run.

## Use the terminal

1. Connect your Internet Archive account. The command asks for your e-mail and password and stores the upload keys in `ia.ini`.

   ```bash
   ia configure
   ```

2. Check the setup.

   ```bash
   archive-ferry doctor
   ```

3. Add the channel. archive-ferry reads the list of videos, shorts and streams.

   ```bash
   archive-ferry add https://www.youtube.com/@yourchannel --prefix yourchannel
   ```

4. Start the worker.

   ```bash
   archive-ferry run
   ```

Each video becomes one Internet Archive item: `https://archive.org/details/<prefix>-<video id>`.
Choose the prefix carefully. The identifier of an item cannot change after the upload.

| Task | Command |
|---|---|
| Start or continue the work | `archive-ferry run` |
| Pause after the current step | `archive-ferry pause` (or press Ctrl+C one time in the worker window) |
| Pause the current download or upload at once | `archive-ferry pause --now` |
| Continue after a pause | `archive-ferry resume` |
| Add new uploads to the queue | `archive-ferry sync` |
| See the progress, speed and time left | `archive-ferry status` |
| See recent events | `archive-ferry log` |
| See failed videos | `archive-ferry list --status failed --status skipped` |
| Try failed videos again | `archive-ferry retry` (add `--skipped` for skipped videos) |
| Open the web page | `archive-ferry ui` |

Press Ctrl+C two times to stop the worker at once. The next `archive-ferry run` continues the interrupted step.
yt-dlp continues a partial download. An interrupted upload starts again for the file that was not complete. Files that are already on the Internet Archive do not upload again.

## Settings

Change the settings on the web page (**Settings**), or with `archive-ferry config set <key> <value>`. Run `archive-ferry config show` to see all settings.

| Setting | Default | Description |
|---|---|---|
| `cookies_from_browser` | | Use the YouTube login of a browser when YouTube blocks downloads. Firefox works best. |
| `keep_files` | `false` | Keep the video files on your computer after the upload |
| `rate_limit` | | Maximum download speed, for example `5M` |
| `collection` | `opensource_movies` | Internet Archive collection. New accounts can upload only to `opensource_movies`. |
| `format` | `bv*+ba/b` | yt-dlp format selector |
| `merge_format` | `mp4` | Container for merged video and audio |
| `subtitles` | `true` | Download and upload subtitles |
| `cookies_file` | | Path to a `cookies.txt` file |
| `js_runtime` | | `deno` (default), `node`, `bun` or `quickjs` |
| `pause_between` | `10` | Seconds between two videos |
| `max_attempts` | `3` | Attempts for each video before the worker skips it |
| `max_unverified` | `10` | Uploads that can wait for confirmation before the next download starts |
| `min_free_gb` | `5` | The worker pauses when the free disk space is less than this |

## How it works

```
pending → downloading → downloaded → uploading → uploaded → done
              │                          │     (local files    (the item shows
              │                          │      deleted)        the same MD5s)
              └────────► failed ◄────────┘
                (retry with backoff, then skipped)
```

- The worker takes interrupted work first. Then it takes new videos, oldest first.
- Only one video is in transfer at a time. Only one worker can run.
- A failed video waits 5 minutes, then 10 minutes, then 20 minutes, and so on. After 3 attempts, the worker skips it.
- If YouTube asks for a sign-in ("confirm you're not a bot"), the worker pauses the queue. Set `cookies_from_browser`, then resume.
- When the free disk space is too low, the worker pauses the queue.
- On Windows and macOS, the computer does not go to sleep while the worker runs. On Linux, use `systemd-inhibit archive-ferry run`.

### What stays on your computer

After an upload, archive-ferry deletes the folder of that video. The database keeps one row for each video:

- the YouTube ID, the title, the upload date and the duration
- the Internet Archive identifier
- the size, the MD5 checksum of each file, the upload time and how long the upload took

Resync uses the YouTube ID. A video that is in the database never goes back in the queue.
If the Internet Archive does not show the item within 48 hours, archive-ferry downloads the video again and uploads it again.

### Files in each item

- The video
- `<id>.info.json`: the YouTube metadata. archive-ferry removes the signed stream URLs (they contain your IP address) and local paths before the upload.
- `<id>.jpg`: the thumbnail
- `<id>.description`: the full description
- `<id>.<lang>.vtt`: the subtitles

### Data folder

All data is in `~/archive-ferry`. To use a different folder, for example a large external disk, use `--home` or set `ARCHIVE_FERRY_HOME`:

```bash
archive-ferry --home D:\archive-ferry run
```

| File | Content |
|---|---|
| `archive-ferry.db` | The queue, the video records and the settings |
| `archive-ferry.log` | The detailed log |
| `downloads/` | Videos that are not uploaded yet |

The project was called `ytia` before. The `ytia` and `ytia-ui` commands still work. If a `~/ytia` folder exists, archive-ferry keeps using it and its `ytia.db`, so no progress is lost.

## Known limits

- The install needs a terminal. A Windows installer is planned.
- YouTube cookies from Chrome often do not work on Windows. Use Firefox, or export a `cookies.txt` file.
- archive-ferry handles one video at a time. It does not download the next video during an upload.
- archive-ferry was tested on Windows. macOS and Linux are expected to work, but nobody has tested them yet.

## Development

```bash
uv sync
uv run pytest
uv run archive-ferry --help
```

Bug reports and pull requests are welcome. Please run the tests before you send a pull request.

## License

archive-ferry is free software under the [GNU Affero General Public License v3.0 or later](LICENSE) (AGPL-3.0-or-later).

The web page uses three fonts that ship with archive-ferry: Newsreader, Libre Franklin and UnifrakturMaguntia. All three use the SIL Open Font License 1.1. The license files are in `src/archive_ferry/static/fonts/`. archive-ferry does not load fonts from the internet.

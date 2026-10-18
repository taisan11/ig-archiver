# ig-archiver

A small long-running Instagram archive daemon built around `instagrapi`.

It archives:

- incoming Instants / disappearing Direct media (`raven_media`)
- Stories for configured accounts
- feed posts for configured accounts

The scheduler uses stable daily anchor times plus a small configurable jitter for ordinary load distribution. It does **not** include automation-evasion logic.

> `instagrapi` is an unofficial API client. Instagram can change endpoints or restrict automated access at any time. Use only for content you are allowed to access/archive and keep request volume modest.

## Requirements

- Python 3.11+
- `uv`

The project pins `instagrapi >=3.0.3,<4`, matching the current CAA-login / HTTP/2 generation of instagrapi.

## Setup

```bash
unzip ig-archiver.zip
cd ig-archiver
uv sync
uv run ig-archive init
```

Edit `config.toml`:

```toml
[account]
username = "your_username"

[targets]
usernames = ["account_a", "account_b"]
```

Then log in interactively:

```bash
uv run ig-archive login
```

Your password is requested interactively and is **not** written to `config.toml`. A reusable instagrapi session is stored in `./state/session.json`.

Check the session:

```bash
uv run ig-archive check
```

Run one pass immediately:

```bash
uv run ig-archive once all
uv run ig-archive once instants
uv run ig-archive once stories
uv run ig-archive once posts
```

Run continuously:

```bash
uv run ig-archive run
```

View last job results:

```bash
uv run ig-archive status
```

## Default schedule

The defaults in `config.example.toml` are:

| Job | Daily anchors | Jitter |
|---|---|---:|
| Instants | 08:00, 20:00 | ±30 min |
| Stories | 07:30, 14:30, 21:30 | ±20 min |
| Posts | 18:00 | ±45 min |

Timezone defaults to `Asia/Tokyo`.

The daemon calculates the next occurrence from these anchors rather than sleeping a fixed number of hours from the previous run. This prevents schedule drift after restarts or slow requests.

## Story behavior

By default Stories use the Home reels tray as an update index:

1. `get_reels_tray_feed("pull_to_refresh")`
2. compare `latest_reel_media` with SQLite state
3. call `user_stories()` only for configured targets whose tray entry changed
4. save unseen story PKs

Set:

```toml
[stories]
use_reels_tray = false
```

to call `user_stories()` for every configured target on every Story run.

## Instant behavior

Incoming disappearing Direct media are read through `direct_threads()` and filtered by:

```python
message.item_type == "raven_media" and message.visual_media
```

The daemon does not call the Direct `seen` endpoint. If Instagram no longer returns a playable URL for an expired/already-viewed item, the item is left unmarked so a future poll can try again.

By default outgoing visual messages are ignored:

```toml
[instants]
incoming_only = true
```

## Archive layout

Example:

```text
archive/
  account_a/
    posts/2026/09/...
    stories/2026/09/...
  some_dm_peer/
    instants/2026/09/...
state/
  session.json
  archive.sqlite3
```

Metadata JSON is saved next to media by default. Disable with:

```toml
[archive]
save_metadata = false
```

## Rate limiting / backoff

`instagrapi`'s `delay_range` is configured from `[pacing]` for its public/private request methods. The separate fixed `request_timeout` pause is set to zero so it does not stack on top of the configured random delay. The scheduler runs one job at a time; temporary throttling errors are rescheduled using `[backoff]` rather than retried in a tight loop. The daemon validates the saved session with `account_info()` at startup but does not automatically relogin. If that check fails, run `ig-archive login`: it loads the saved settings, calls `login()` with the password entered interactively, and saves refreshed settings. The password is not stored for unattended automatic relogin.

```toml
[pacing]
min_seconds = 1.0
max_seconds = 3.0

[backoff]
min_minutes = 60
max_minutes = 180
```

## Notes

- Keep `state/session.json` private: it contains authenticated session state.
- `config.toml`, `state/`, and `archive/` are in `.gitignore`.
- For a server, run `uv run ig-archive run` under tmux/screen or a process supervisor if desired.

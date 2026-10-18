from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from instagrapi import Client

from .config import Config
from .db import ArchiveDB


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "dict"):
        return value.dict()
    return value


def _write_metadata(folder: Path, name: str, value: Any, enabled: bool) -> None:
    if not enabled:
        return
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / f"{name}.json").open("w", encoding="utf-8") as f:
        json.dump(_jsonable(value), f, ensure_ascii=False, indent=2, default=str)


def _target_dir(cfg: Config, username: str, kind: str, taken_at: datetime | None = None) -> Path:
    dt = taken_at or datetime.now(UTC)
    folder = cfg.archive_root / username / kind / dt.strftime("%Y") / dt.strftime("%m")
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def resolve_targets(cl: Client, cfg: Config, db: ArchiveDB) -> dict[str, str]:
    result: dict[str, str] = {}
    for username in cfg.targets:
        cached = db.get_target_id(username)
        if cached:
            result[username] = cached
            continue
        user_id = str(cl.user_id_from_username(username))
        db.set_target_id(username, user_id)
        result[username] = user_id
    return result


def archive_posts(cl: Client, cfg: Config, db: ArchiveDB) -> int:
    archived = 0
    for username, user_id in resolve_targets(cl, cfg, db).items():
        medias = cl.user_medias(user_id, amount=cfg.post_scan_amount)
        for media in reversed(medias):
            key = str(media.pk)
            if db.has_seen("post", key):
                continue
            folder = _target_dir(cfg, username, "posts", getattr(media, "taken_at", None))
            path: Path | None = None
            media_type = int(media.media_type)
            if media_type == 1:
                path = cl.photo_download(media.pk, folder=folder, overwrite=False)
            elif media_type == 2:
                path = cl.video_download(int(media.pk), folder=folder, overwrite=False)
            elif media_type == 8:
                paths = cl.album_download(int(media.pk), folder=folder, overwrite=False)
                if paths:
                    path = Path(paths[0])
            else:
                continue
            _write_metadata(folder, f"{media.pk}.post", media, cfg.save_metadata)
            db.mark_seen("post", key, username, path)
            archived += 1
    return archived


def _tray_user_id(reel: dict) -> str | None:
    user = reel.get("user") or {}
    value = user.get("pk") or user.get("id") or reel.get("id")
    return str(value) if value is not None else None


def archive_stories(cl: Client, cfg: Config, db: ArchiveDB) -> int:
    targets = resolve_targets(cl, cfg, db)
    id_to_username = {str(v): k for k, v in targets.items()}
    candidates: list[tuple[str, str, str | None]] = []

    if cfg.story_use_reels_tray:
        tray = cl.get_reels_tray_feed("pull_to_refresh")
        for reel in tray.get("tray", []):
            user_id = _tray_user_id(reel)
            if not user_id or user_id not in id_to_username:
                continue
            latest = reel.get("latest_reel_media")
            state_key = f"story_latest:{user_id}"
            if latest is not None and db.get_state(state_key) == str(latest):
                continue
            candidates.append((id_to_username[user_id], user_id, str(latest) if latest is not None else None))
    else:
        candidates = [(username, user_id, None) for username, user_id in targets.items()]

    archived = 0
    for username, user_id, latest in candidates:
        stories = cl.user_stories(user_id)
        for story in stories:
            key = str(story.pk)
            if db.has_seen("story", key):
                continue
            folder = _target_dir(cfg, username, "stories", getattr(story, "taken_at", None))
            path = cl.story_download(story.pk, folder=folder)
            _write_metadata(folder, f"{story.pk}.story", story, cfg.save_metadata)
            db.mark_seen("story", key, username, path)
            archived += 1
        if latest is not None:
            db.set_state(f"story_latest:{user_id}", latest)
    return archived


def _best_visual_url(message) -> tuple[str | None, str | None]:
    vm = message.visual_media
    if not vm or not vm.media:
        return None, None
    media = vm.media
    if media.video_versions:
        # Prefer the largest listed candidate when dimensions are available.
        best = max(media.video_versions, key=lambda x: (x.width or 0) * (x.height or 0))
        return str(best.url), "video"
    if media.image_versions2 and media.image_versions2.candidates:
        best = max(
            media.image_versions2.candidates,
            key=lambda x: (x.width or 0) * (x.height or 0),
        )
        return str(best.url), "image"
    return None, None


def archive_instants(cl: Client, cfg: Config, db: ArchiveDB) -> int:
    archived = 0
    threads = cl.direct_threads(
        amount=cfg.instant_thread_amount,
        thread_message_limit=cfg.instant_thread_message_limit,
    )

    for thread in threads:
        peer_name = "direct"
        if getattr(thread, "users", None):
            peer_name = thread.users[0].username or peer_name

        for message in reversed(thread.messages):
            if message.item_type != "raven_media" or not message.visual_media:
                continue
            if cfg.instant_incoming_only and message.is_sent_by_viewer:
                continue
            key = str(message.id)
            if db.has_seen("instant", key):
                continue

            url, media_kind = _best_visual_url(message)
            if not url:
                # Keep it unmarked so a later poll can still archive it if Instagram
                # starts returning the media URL.
                continue

            sender = getattr(getattr(message.visual_media, "media", None), "user", None)
            sender_name = getattr(sender, "username", None) or peer_name
            folder = _target_dir(cfg, sender_name, "instants", getattr(message, "timestamp", None))
            filename = f"instant_{message.id}"
            if media_kind == "video":
                path = cl.video_download_by_url(url, filename=filename, folder=folder, overwrite=False)
            else:
                path = cl.photo_download_by_url(url, filename=filename, folder=folder, overwrite=False)

            _write_metadata(folder, f"{message.id}.instant", message, cfg.save_metadata)
            db.mark_seen("instant", key, sender_name, path)
            archived += 1
    return archived

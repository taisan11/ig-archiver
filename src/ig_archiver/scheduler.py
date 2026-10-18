from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from instagrapi.exceptions import (
    ClientError,
    ClientThrottledError,
    FeedbackRequired,
    LoginRequired,
    PleaseWaitFewMinutes,
)
from rich.console import Console

from .archive import archive_instants, archive_posts, archive_stories
from .client import load_session
from .config import Config, JobSchedule
from .db import ArchiveDB

console = Console()


@dataclass(slots=True)
class ScheduledJob:
    name: str
    schedule: JobSchedule
    callback: object
    next_run: datetime | None = None


def _parse_time(value: str) -> tuple[int, int]:
    h, m = value.split(":", 1)
    return int(h), int(m)


def next_anchor(now: datetime, schedule: JobSchedule) -> datetime:
    candidates: list[datetime] = []
    for day_offset in (0, 1, 2):
        day = (now + timedelta(days=day_offset)).date()
        for raw in schedule.times:
            hour, minute = _parse_time(raw)
            base = datetime(day.year, day.month, day.day, hour, minute, tzinfo=now.tzinfo)
            if base > now:
                candidates.append(base)
    if not candidates:
        raise RuntimeError("No future schedule candidate")

    base = min(candidates)
    if schedule.jitter_minutes:
        base += timedelta(minutes=random.uniform(-schedule.jitter_minutes, schedule.jitter_minutes))
        # Never schedule into the past because of negative jitter.
        if base <= now:
            base = now + timedelta(seconds=random.uniform(15, 60))
    return base


async def _run_job(name: str, callback, cl, cfg: Config, db: ArchiveDB) -> int:
    started = datetime.now(UTC)
    try:
        count = await asyncio.to_thread(callback, cl, cfg, db)
        cl.dump_settings(cfg.session_file)
        db.record_run(name, started, True, f"archived={count}")
        console.print(f"[green]{name}[/green]: archived {count}")
        return count
    except Exception as exc:
        db.record_run(name, started, False, repr(exc))
        raise


async def run_daemon(cfg: Config) -> None:
    db = ArchiveDB(cfg.database)
    cl = load_session(cfg)
    tz = ZoneInfo(cfg.timezone)
    jobs = [
        ScheduledJob("instants", cfg.instant_schedule, archive_instants),
        ScheduledJob("stories", cfg.story_schedule, archive_stories),
        ScheduledJob("posts", cfg.post_schedule, archive_posts),
    ]
    jobs = [job for job in jobs if job.schedule.enabled]
    if not jobs:
        raise RuntimeError("No enabled jobs")

    if cfg.run_on_start:
        for job in jobs:
            try:
                await _run_job(job.name, job.callback, cl, cfg, db)
            except Exception as exc:  # noqa: BLE001
                console.print(f"[red]{job.name} startup run failed:[/red] {exc}")

    now = datetime.now(tz)
    for job in jobs:
        job.next_run = next_anchor(now, job.schedule)

    console.print("Scheduler started")
    for job in jobs:
        assert job.next_run is not None
        console.print(f"  {job.name}: {job.next_run.isoformat(timespec='minutes')}")

    while True:
        job = min(jobs, key=lambda x: x.next_run or datetime.max.replace(tzinfo=tz))
        assert job.next_run is not None
        delay = max(0.0, (job.next_run - datetime.now(tz)).total_seconds())
        await asyncio.sleep(delay)

        try:
            await _run_job(job.name, job.callback, cl, cfg, db)
            job.next_run = next_anchor(datetime.now(tz), job.schedule)
        except (ClientThrottledError, PleaseWaitFewMinutes, FeedbackRequired) as exc:
            minutes = random.randint(cfg.backoff_min_minutes, cfg.backoff_max_minutes)
            job.next_run = datetime.now(tz) + timedelta(minutes=minutes)
            console.print(
                f"[yellow]{job.name}: Instagram asked to slow down; "
                f"retry scheduled in {minutes} min ({type(exc).__name__})[/yellow]"
            )
        except LoginRequired as exc:
            console.print("[red]Session is no longer valid. Run `ig-archive login` again.[/red]")
            raise RuntimeError("Instagram session expired") from exc
        except ClientError as exc:
            minutes = random.randint(cfg.backoff_min_minutes, cfg.backoff_max_minutes)
            job.next_run = datetime.now(tz) + timedelta(minutes=minutes)
            console.print(
                f"[yellow]{job.name}: {type(exc).__name__}; retry in {minutes} min[/yellow]"
            )
        except Exception as exc:  # noqa: BLE001
            minutes = random.randint(cfg.backoff_min_minutes, cfg.backoff_max_minutes)
            job.next_run = datetime.now(tz) + timedelta(minutes=minutes)
            console.print(f"[red]{job.name}: {exc!r}; retry in {minutes} min[/red]")

        console.print(f"next {job.name}: {job.next_run.isoformat(timespec='minutes')}")


async def run_once(cfg: Config, job_name: str) -> dict[str, int]:
    db = ArchiveDB(cfg.database)
    cl = load_session(cfg)
    callbacks = {
        "instants": archive_instants,
        "stories": archive_stories,
        "posts": archive_posts,
    }
    names = list(callbacks) if job_name == "all" else [job_name]
    result: dict[str, int] = {}
    for name in names:
        result[name] = await _run_job(name, callbacks[name], cl, cfg, db)
    return result

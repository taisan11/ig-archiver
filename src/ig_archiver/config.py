from __future__ import annotations

import tomllib
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path


@dataclass(slots=True)
class JobSchedule:
    enabled: bool
    times: list[str]
    jitter_minutes: int


@dataclass(slots=True)
class Config:
    path: Path
    username: str
    session_file: Path
    archive_root: Path
    database: Path
    save_metadata: bool
    targets: list[str]
    timezone: str
    run_on_start: bool
    instant_schedule: JobSchedule
    story_schedule: JobSchedule
    post_schedule: JobSchedule
    instant_thread_amount: int
    instant_thread_message_limit: int
    instant_incoming_only: bool
    story_use_reels_tray: bool
    post_scan_amount: int
    pacing_min_seconds: float
    pacing_max_seconds: float
    backoff_min_minutes: int
    backoff_max_minutes: int

    @property
    def base_dir(self) -> Path:
        return self.path.parent


def _resolve(base: Path, value: str) -> Path:
    p = Path(value).expanduser()
    return p if p.is_absolute() else (base / p).resolve()


def _job_schedule(section: dict, name: str) -> JobSchedule:
    value = section.get(name, {})
    return JobSchedule(
        enabled=bool(value.get("enabled", True)),
        times=[str(x) for x in value.get("times", [])],
        jitter_minutes=int(value.get("jitter_minutes", 0)),
    )


def load_config(path: Path) -> Config:
    path = path.expanduser().resolve()
    with path.open("rb") as f:
        raw = tomllib.load(f)

    base = path.parent
    account = raw.get("account", {})
    archive = raw.get("archive", {})
    schedule = raw.get("schedule", {})
    targets = raw.get("targets", {})
    instants = raw.get("instants", {})
    stories = raw.get("stories", {})
    posts = raw.get("posts", {})
    pacing = raw.get("pacing", {})
    backoff = raw.get("backoff", {})

    username = str(account.get("username", "")).strip()
    if not username or username == "your_username":
        raise ValueError("Set [account].username in config.toml")

    cfg = Config(
        path=path,
        username=username,
        session_file=_resolve(base, str(account.get("session_file", "./state/session.json"))),
        archive_root=_resolve(base, str(archive.get("root", "./archive"))),
        database=_resolve(base, str(archive.get("database", "./state/archive.sqlite3"))),
        save_metadata=bool(archive.get("save_metadata", True)),
        targets=[str(x).strip() for x in targets.get("usernames", []) if str(x).strip()],
        timezone=str(schedule.get("timezone", "Asia/Tokyo")),
        run_on_start=bool(schedule.get("run_on_start", False)),
        instant_schedule=_job_schedule(schedule, "instants"),
        story_schedule=_job_schedule(schedule, "stories"),
        post_schedule=_job_schedule(schedule, "posts"),
        instant_thread_amount=int(instants.get("thread_amount", 30)),
        instant_thread_message_limit=int(instants.get("thread_message_limit", 30)),
        instant_incoming_only=bool(instants.get("incoming_only", True)),
        story_use_reels_tray=bool(stories.get("use_reels_tray", True)),
        post_scan_amount=int(posts.get("scan_amount", 12)),
        pacing_min_seconds=float(pacing.get("min_seconds", 1.0)),
        pacing_max_seconds=float(pacing.get("max_seconds", 3.0)),
        backoff_min_minutes=int(backoff.get("min_minutes", 60)),
        backoff_max_minutes=int(backoff.get("max_minutes", 180)),
    )

    if cfg.pacing_min_seconds < 0 or cfg.pacing_max_seconds < cfg.pacing_min_seconds:
        raise ValueError("Invalid [pacing] range")
    if cfg.backoff_min_minutes < 1 or cfg.backoff_max_minutes < cfg.backoff_min_minutes:
        raise ValueError("Invalid [backoff] range")

    for job_name, job in (
        ("instants", cfg.instant_schedule),
        ("stories", cfg.story_schedule),
        ("posts", cfg.post_schedule),
    ):
        if job.enabled and not job.times:
            raise ValueError(f"[schedule.{job_name}] needs at least one time")
        for value in job.times:
            _validate_time(value)

    return cfg


def _validate_time(value: str) -> None:
    try:
        h, m = value.split(":", 1)
        hour, minute = int(h), int(m)
    except Exception as exc:
        raise ValueError(f"Invalid schedule time: {value!r}") from exc
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError(f"Invalid schedule time: {value!r}")


def initialize_config(destination: Path) -> Path:
    destination = destination.expanduser().resolve()
    if destination.exists():
        raise FileExistsError(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    template = files("ig_archiver").joinpath("config.example.toml").read_text(encoding="utf-8")
    destination.write_text(template, encoding="utf-8")
    return destination

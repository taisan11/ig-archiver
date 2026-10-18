from __future__ import annotations

import asyncio
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from .client import interactive_login, load_session
from .config import initialize_config, load_config
from .db import ArchiveDB
from .scheduler import run_daemon, run_once

app = typer.Typer(no_args_is_help=True, help="Instagram archive daemon")
console = Console()


def _cfg(path: Path):
    try:
        return load_config(path)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]Config error:[/red] {exc}")
        raise typer.Exit(2)


@app.command()
def init(
    path: Path = typer.Argument(Path("config.toml"), help="Config file to create"),  # noqa: B008
):
    """Create a config.toml from the bundled example."""
    try:
        created = initialize_config(path)
    except FileExistsError:
        console.print(f"[red]Already exists:[/red] {path}")
        raise typer.Exit(1)
    console.print(f"Created [green]{created}[/green]")
    console.print("Edit [account].username and [targets].usernames, then run `ig-archive login`.")


@app.command()
def login(
    config: Path = typer.Option(Path("config.toml"), "--config", "-c"),  # noqa: B008
):
    """Interactively log in and save a reusable instagrapi session."""
    cfg = _cfg(config)
    try:
        cl = interactive_login(cfg)
        user = cl.account_info()
        console.print(f"Logged in as [green]@{user.username}[/green]")
        console.print(f"Session saved to {cfg.session_file}")
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]Login failed:[/red] {type(exc).__name__}: {exc}")
        raise typer.Exit(1)


@app.command()
def check(
    config: Path = typer.Option(Path("config.toml"), "--config", "-c"),  # noqa: B008
):
    """Check that the saved session can make an authenticated request."""
    cfg = _cfg(config)
    try:
        load_session(cfg)
        console.print(f"Session OK: [green]@{cfg.username}[/green]")
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]Session check failed:[/red] {type(exc).__name__}: {exc}")
        raise typer.Exit(1)


@app.command()
def run(
    config: Path = typer.Option(Path("config.toml"), "--config", "-c"),  # noqa: B008
):
    """Run the long-lived scheduler."""
    cfg = _cfg(config)
    try:
        asyncio.run(run_daemon(cfg))
    except KeyboardInterrupt:
        console.print("Stopped")


@app.command("once")
def once_cmd(
    job: str = typer.Argument("all", help="all | instants | stories | posts"),
    config: Path = typer.Option(Path("config.toml"), "--config", "-c"),  # noqa: B008
):
    """Run one archive job immediately."""
    if job not in {"all", "instants", "stories", "posts"}:
        console.print("[red]job must be all, instants, stories, or posts[/red]")
        raise typer.Exit(2)
    cfg = _cfg(config)
    try:
        result = asyncio.run(run_once(cfg, job))
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]Archive failed:[/red] {type(exc).__name__}: {exc}")
        raise typer.Exit(1)
    for name, count in result.items():
        console.print(f"{name}: {count}")


@app.command()
def status(
    config: Path = typer.Option(Path("config.toml"), "--config", "-c"),  # noqa: B008
):
    """Show the latest recorded run for each job."""
    cfg = _cfg(config)
    db = ArchiveDB(cfg.database)
    rows = db.latest_runs()
    if not rows:
        console.print("No recorded runs yet.")
        return
    table = Table("Job", "Ended", "OK", "Detail")
    for row in rows:
        table.add_row(
            row["job"],
            row["ended_at"],
            "yes" if row["success"] else "no",
            row["detail"] or "",
        )
    console.print(table)


if __name__ == "__main__":
    app()

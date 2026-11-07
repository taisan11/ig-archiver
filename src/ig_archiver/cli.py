from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from .client import interactive_login, load_session
from .config import Config, initialize_config, load_config
from .db import ArchiveDB
from .scheduler import run_daemon, run_once
from .viewer import serve_archive

app = typer.Typer(no_args_is_help=True, help="Instagram archive daemon and local viewer")
console = Console()

ConfigOption = Annotated[Path, typer.Option("--config", "-c", help="Path to config.toml")]


def _cfg(path: Path) -> Config:
    try:
        return load_config(path)
    except (OSError, ValueError) as exc:
        console.print(f"[red]Config error:[/red] {exc}")
        raise typer.Exit(2) from exc


@app.command()
def init(
    path: Annotated[Path, typer.Argument(help="Config file to create")] = Path("config.toml"),
) -> None:
    """Create a config.toml from the bundled example."""
    try:
        created = initialize_config(path)
    except FileExistsError as exc:
        console.print(f"[red]Already exists:[/red] {path}")
        raise typer.Exit(1) from exc
    console.print(f"Created [green]{created}[/green]")
    console.print("Edit [account].username and [targets].usernames, then run `ig-archive login`.")


@app.command()
def login(config: ConfigOption = Path("config.toml")) -> None:
    """Interactively log in and save a reusable instagrapi session."""
    cfg = _cfg(config)
    try:
        cl = interactive_login(cfg)
        user = cl.account_info()
        console.print(f"Logged in as [green]@{user.username}[/green]")
        console.print(f"Session saved to {cfg.session_file}")
    except Exception as exc:
        console.print(f"[red]Login failed:[/red] {type(exc).__name__}: {exc}")
        raise typer.Exit(1) from exc


@app.command()
def check(config: ConfigOption = Path("config.toml")) -> None:
    """Check that the saved session can make an authenticated request."""
    cfg = _cfg(config)
    try:
        load_session(cfg)
        console.print(f"Session OK: [green]@{cfg.username}[/green]")
    except Exception as exc:
        console.print(f"[red]Session check failed:[/red] {type(exc).__name__}: {exc}")
        raise typer.Exit(1) from exc


@app.command()
def run(config: ConfigOption = Path("config.toml")) -> None:
    """Run the long-lived scheduler."""
    cfg = _cfg(config)
    try:
        asyncio.run(run_daemon(cfg))
    except KeyboardInterrupt:
        console.print("Stopped")


@app.command("once")
def once_cmd(
    job: Annotated[str, typer.Argument(help="all | instants | stories | posts")] = "all",
    config: ConfigOption = Path("config.toml"),
) -> None:
    """Run one archive job immediately."""
    if job not in {"all", "instants", "stories", "posts"}:
        console.print("[red]job must be all, instants, stories, or posts[/red]")
        raise typer.Exit(2)
    cfg = _cfg(config)
    try:
        result = asyncio.run(run_once(cfg, job))
    except Exception as exc:
        console.print(f"[red]Archive failed:[/red] {type(exc).__name__}: {exc}")
        raise typer.Exit(1) from exc
    for name, count in result.items():
        console.print(f"{name}: {count}")


@app.command()
def status(config: ConfigOption = Path("config.toml")) -> None:
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


@app.command()
def view(
    config: ConfigOption = Path("config.toml"),
    host: Annotated[str, typer.Option(help="Listen address (localhost by default)")] = "127.0.0.1",
    port: Annotated[int, typer.Option(min=1, max=65535, help="HTTP listen port")] = 8765,
) -> None:
    """Browse saved media without logging into Instagram or changing archive state."""
    cfg = _cfg(config)
    if host not in {"127.0.0.1", "localhost", "::1"}:
        console.print("[yellow]Warning:[/yellow] This viewer has no authentication. Do not expose it to the internet.")
    try:
        serve_archive(cfg.database, cfg.archive_root, host=host, port=port)
    except KeyboardInterrupt:
        console.print("Stopped")
    except OSError as exc:
        console.print(f"[red]Viewer failed:[/red] {exc}")
        raise typer.Exit(1) from exc


if __name__ == "__main__":
    app()

from __future__ import annotations

from getpass import getpass

from instagrapi import Client
from instagrapi.mixins.challenge import ChallengeChoice

from .config import Config


def _new_client(cfg: Config) -> Client:
    cl = Client(delay_range=[cfg.pacing_min_seconds, cfg.pacing_max_seconds])
    # request_timeout is a separate fixed pause; disable it so [pacing] is the
    # sole request delay rather than stacking a fixed second on the random range.
    cl.set_retry_config(request_timeout=0)
    return cl


def _challenge_handler(username: str, choice: ChallengeChoice | None = None):
    label = "SMS" if choice == ChallengeChoice.SMS else "email"
    code = input(f"Instagram verification code ({label}) for {username}: ").strip()
    return code or False


def interactive_login(cfg: Config) -> Client:
    password = getpass(f"Instagram password for @{cfg.username}: ")
    verification_code = input("2FA code (press Enter if not needed): ").strip() or None

    cfg.session_file.parent.mkdir(parents=True, exist_ok=True)
    cl = _new_client(cfg)
    setattr(  # noqa: B010 - instagrapi's annotation incorrectly includes `self` here.
        cl, "challenge_code_handler", lambda username, choice=None: _challenge_handler(username, choice)
    )

    if cfg.session_file.exists():
        settings = cl.load_settings(cfg.session_file)
        cl.set_settings(settings)
        cl.set_retry_config(request_timeout=0)

    cl.login(cfg.username, password, verification_code=verification_code or "")
    cl.dump_settings(cfg.session_file)
    return cl


def load_session(cfg: Config) -> Client:
    if not cfg.session_file.exists():
        raise FileNotFoundError(
            f"Session file not found: {cfg.session_file}. Run `ig-archive login` first."
        )
    cl = _new_client(cfg)
    setattr(  # noqa: B010 - instagrapi's annotation incorrectly includes `self` here.
        cl, "challenge_code_handler", lambda username, choice=None: False
    )
    settings = cl.load_settings(cfg.session_file)
    cl.set_settings(settings)
    cl.set_retry_config(request_timeout=0)
    account = cl.account_info()
    if account.username.casefold() != cfg.username.casefold():
        raise ValueError(
            f"Saved session belongs to @{account.username}, not @{cfg.username}. "
            "Run `ig-archive login` with the intended account."
        )
    return cl

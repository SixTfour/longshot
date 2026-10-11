"""Process configuration, read from the environment.

Locally that means a .env file; on a host it means the host's secret store.
Secrets are never written anywhere else.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from . import redact

# Starting values for the settings the commissioner can change with /config.
# Each can be overridden once, at first run, by the matching DEFAULT_* variable.
SETTING_DEFAULTS = {
    "deadline": "thu 19:00",
    "announce": "tue 10:00",
    "reminders": "24,7,2",
    "sportsbook": "draftkings",
    "stake": "10",
    "cache_hours": "12",
}


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Config:
    discord_token: str = field(repr=False)
    odds_api_key: str = field(repr=False)
    db_path: Path
    guild_id: int | None
    setting_defaults: dict[str, str]


def default_db_path() -> Path:
    """Outside the project folder, which may be synced by OneDrive."""
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / "Longshot" / "longshot.db"
    return Path.home() / ".longshot" / "longshot.db"


def load_config(env_file: str | Path | None = ".env") -> Config:
    if env_file is not None:
        load_dotenv(env_file)

    token = os.environ.get("DISCORD_TOKEN", "").strip()
    odds_key = os.environ.get("ODDS_API_KEY", "").strip()
    missing = [name for name, value in (("DISCORD_TOKEN", token), ("ODDS_API_KEY", odds_key)) if not value]
    if missing:
        raise ConfigError(
            f"Missing {' and '.join(missing)}. Copy .env.example to .env and fill them in."
        )
    redact.register_secret(token)
    redact.register_secret(odds_key)

    guild = os.environ.get("GUILD_ID", "").strip()
    if guild and not guild.isdigit():
        raise ConfigError("GUILD_ID must be a number (your server's ID) or left empty.")

    db_path = os.environ.get("DB_PATH", "").strip()
    defaults = {
        key: os.environ.get(f"DEFAULT_{key.upper()}", "").strip() or value
        for key, value in SETTING_DEFAULTS.items()
    }
    return Config(
        discord_token=token,
        odds_api_key=odds_key,
        db_path=Path(db_path) if db_path else default_db_path(),
        guild_id=int(guild) if guild else None,
        setting_defaults=defaults,
    )

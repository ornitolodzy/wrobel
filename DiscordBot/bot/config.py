"""Settings from environment variables (docker-compose / .env)."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    return int(raw) if raw else default


@dataclass(frozen=True)
class Settings:
    discord_token: str
    gateway_url: str
    guild_id: int | None          # dev server: slash commands appear instantly there
    user_limit_per_minute: int    # per Discord user; the gateway limits the whole bot (one IP)
    request_timeout_s: float
    health_file: str
    log_level: str

    @classmethod
    def from_env(cls) -> "Settings":
        guild = os.environ.get("DISCORD_GUILD_ID", "").strip()
        return cls(
            discord_token=os.environ.get("DISCORD_TOKEN", "").strip(),
            gateway_url=os.environ.get("GATEWAY_URL", "http://api-gateway:8000").rstrip("/"),
            guild_id=int(guild) if guild else None,
            user_limit_per_minute=_int("USER_LIMIT_PER_MINUTE", 5),
            request_timeout_s=float(os.environ.get("REQUEST_TIMEOUT_SECONDS", "120")),
            health_file=os.environ.get("HEALTH_FILE", "/tmp/discord-bot.alive"),
            log_level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        )

import secrets
import time
from collections import defaultdict, deque
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import APIKeyHeader

from app.config import Settings
from app.db import Database
from app.runtime_config import RuntimeConfigStore
from app.services import Downstream

admin_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


@dataclass(frozen=True)
class Client:
    name: str
    is_admin: bool


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_db(request: Request) -> Database:
    return request.app.state.db


def get_runtime(request: Request) -> RuntimeConfigStore:
    return request.app.state.runtime


def get_downstream(request: Request) -> Downstream:
    return request.app.state.downstream


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def get_channel(
    x_channel: str | None = Header(default=None, alias="X-Channel", max_length=32),
    settings: Settings = Depends(get_settings),
) -> Client:
    channel = (x_channel or "").strip().lower()
    if channel not in settings.allowed_channel_list:
        channel = "unknown"
    return Client(name=channel, is_admin=False)


async def require_admin(
    request: Request,
    api_key: str | None = Depends(admin_key_header),
    settings: Settings = Depends(get_settings),
    db: Database = Depends(get_db),
) -> Client:
    if not settings.admin_api_key:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Endpointy administracyjne są wyłączone (brak ADMIN_API_KEY).",
        )
    if api_key and secrets.compare_digest(api_key, settings.admin_api_key):
        return Client(name="admin", is_admin=True)

    await db.log_event(
        "auth_failed",
        detail={"ip": _client_ip(request), "path": request.url.path, "key_present": bool(api_key)},
    )
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Brak lub niepoprawny klucz administratora (nagłówek X-API-Key).",
    )


class RateLimiter:
    def __init__(self, per_minute: int) -> None:
        self.per_minute = per_minute
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def hit(self, key: str) -> tuple[bool, int]:
        now = time.monotonic()
        if len(self._hits) > 10_000:
            for k in [k for k, w in self._hits.items() if not w or now - w[-1] >= 60]:
                del self._hits[k]
        window = self._hits[key]
        while window and now - window[0] >= 60:
            window.popleft()
        if len(window) >= self.per_minute:
            return False, max(1, int(60 - (now - window[0])) + 1)
        window.append(now)
        return True, 0


async def rate_limit(
    request: Request,
    client: Client = Depends(get_channel),
    db: Database = Depends(get_db),
) -> Client:
    limiter: RateLimiter = request.app.state.rate_limiter
    key = f"ip:{_client_ip(request)}"
    allowed, retry_after = limiter.hit(key)
    if not allowed:
        await db.log_event("rate_limited", client=client.name, detail={"path": request.url.path, "key": key})
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Przekroczono limit zapytań. Spróbuj ponownie za chwilę.",
            headers={"Retry-After": str(retry_after)},
        )
    return client

"""Lightweight exception types (no web framework imports - also used inside worker processes)."""

from __future__ import annotations

from collections.abc import Mapping


class ApiError(Exception):
    """An error with an HTTP status and a stable machine-readable ``code``."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.headers = dict(headers or {})

    def __reduce__(self) -> tuple[type[ApiError], tuple[int, str, str, dict[str, str]]]:
        # Picklable, so it can cross process boundaries intact.
        return (type(self), (self.status_code, self.code, self.message, self.headers))

"""Postgres connection (pgvector registered). URL: $DATABASE_URL, else params['url'], else local compose."""

from __future__ import annotations

import os

DEFAULT_URL = "postgresql://prawdowid:prawdowid@localhost:5432/prawdowid"


def database_url(params: dict | None = None) -> str:
    return os.environ.get("DATABASE_URL") or (params or {}).get("url") or DEFAULT_URL


def connect(params: dict | None = None):
    import psycopg
    from pgvector.psycopg import register_vector

    conn = psycopg.connect(database_url(params), connect_timeout=5)
    register_vector(conn)
    return conn

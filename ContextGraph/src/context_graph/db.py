"""Thin wrapper over the Neo4j driver: read/write helpers returning plain dicts."""

from __future__ import annotations

import time
import warnings
from collections.abc import Callable
from typing import Any

from neo4j import Driver, GraphDatabase, ManagedTransaction
from neo4j.exceptions import ServiceUnavailable

from context_graph.config import Settings


def to_plain(value: Any) -> Any:
    """Convert neo4j temporal types (and nested containers) to Python natives."""
    if hasattr(value, "to_native"):
        return value.to_native()
    if isinstance(value, dict):
        return {k: to_plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [to_plain(v) for v in value]
    return value


class Graph:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.database = settings.neo4j_database
        with warnings.catch_warnings():  # the option is a driver "preview feature"
            warnings.simplefilter("ignore")
            self.driver: Driver = GraphDatabase.driver(
                settings.neo4j_uri,
                auth=(settings.neo4j_user, settings.neo4j_password),
                # SUPPORTS/CONTRADICTS may not exist yet in a fresh graph: expected, not a typo.
                notifications_disabled_classifications=["UNRECOGNIZED"],
            )

    def wait_until_ready(self, timeout_s: float | None = None) -> None:
        deadline = time.monotonic() + (timeout_s if timeout_s is not None else self.settings.connect_timeout_s)
        while True:
            try:
                self.driver.verify_connectivity()
                return
            except (ServiceUnavailable, OSError):
                if time.monotonic() > deadline:
                    raise
                time.sleep(1.0)

    def close(self) -> None:
        self.driver.close()

    def read(self, query: str, **params: Any) -> list[dict]:
        records, _, _ = self.driver.execute_query(query, params, database_=self.database, routing_="r")
        return [to_plain(r.data()) for r in records]

    def write(self, query: str, **params: Any) -> list[dict]:
        records, _, _ = self.driver.execute_query(query, params, database_=self.database)
        return [to_plain(r.data()) for r in records]

    def transaction(self, fn: Callable[[ManagedTransaction], Any]) -> Any:
        """Run several statements atomically (retried by the driver on transient errors)."""
        with self.driver.session(database=self.database) as session:
            return session.execute_write(fn)

    def ping(self) -> bool:
        try:
            return self.read("RETURN 1 AS ok")[0]["ok"] == 1
        except Exception:
            return False

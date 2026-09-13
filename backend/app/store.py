"""Durable SQLite storage for the click counter."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class CounterState:
    count: int
    updated_at: str


class CounterStore:
    """A concurrency-safe, process-persistent counter."""

    def __init__(self, database_path: Path):
        self.database_path = Path(database_path)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection, connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS counter (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    count INTEGER NOT NULL CHECK (count >= 0),
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO counter (id, count, updated_at)
                VALUES (1, 0, ?)
                """,
                (self._now(),),
            )

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _state(row: sqlite3.Row) -> CounterState:
        return CounterState(count=int(row["count"]), updated_at=row["updated_at"])

    def get(self) -> CounterState:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT count, updated_at FROM counter WHERE id = 1"
            ).fetchone()
        if row is None:
            raise RuntimeError("Counter database is not initialized")
        return self._state(row)

    def increment(self) -> CounterState:
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE counter SET count = count + 1, updated_at = ? WHERE id = 1",
                (self._now(),),
            )
            row = connection.execute(
                "SELECT count, updated_at FROM counter WHERE id = 1"
            ).fetchone()
        if row is None:
            raise RuntimeError("Counter database is not initialized")
        return self._state(row)

    def reset(self) -> CounterState:
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE counter SET count = 0, updated_at = ? WHERE id = 1",
                (self._now(),),
            )
            row = connection.execute(
                "SELECT count, updated_at FROM counter WHERE id = 1"
            ).fetchone()
        if row is None:
            raise RuntimeError("Counter database is not initialized")
        return self._state(row)

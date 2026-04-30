from __future__ import annotations

import sqlite3
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from time import time

from flask import current_app, has_app_context


@dataclass(frozen=True)
class ThrottleDecision:
    allowed: bool
    retry_after_seconds: int = 0


class InMemoryRequestThrottle:
    def __init__(self) -> None:
        self._buckets: dict[str, deque[float]] = {}
        self._lock = Lock()

    def clear(self, key: str) -> None:
        with self._lock:
            self._buckets.pop(key, None)

    def clear_all(self) -> None:
        with self._lock:
            self._buckets.clear()

    def check(self, key: str, *, limit: int, window_seconds: int) -> ThrottleDecision:
        if limit <= 0 or window_seconds <= 0:
            return ThrottleDecision(allowed=True)

        now = time()
        with self._lock:
            bucket = self._prune_bucket(key, window_seconds, now)
            if len(bucket) >= limit:
                retry_after = max(1, int((bucket[0] + window_seconds) - now + 0.999))
                return ThrottleDecision(allowed=False, retry_after_seconds=retry_after)

        return ThrottleDecision(allowed=True)

    def hit(self, key: str, *, window_seconds: int) -> None:
        if window_seconds <= 0:
            return

        now = time()
        with self._lock:
            bucket = self._prune_bucket(key, window_seconds, now)
            bucket.append(now)
            self._buckets[key] = bucket

    def _prune_bucket(self, key: str, window_seconds: int, now: float) -> deque[float]:
        bucket = self._buckets.get(key) or deque()
        cutoff = now - window_seconds
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()

        if bucket:
            self._buckets[key] = bucket
        else:
            self._buckets.pop(key, None)

        return bucket


class SQLiteRequestThrottle:
    _GC_META_KEY = "last_global_cleanup_at"

    def __init__(self) -> None:
        self._lock = Lock()

    def clear(self, key: str) -> None:
        self._execute_write(
            "DELETE FROM throttle_hits WHERE bucket_key = ?",
            (key,),
        )

    def clear_all(self) -> None:
        self._execute_write("DELETE FROM throttle_hits")

    def check(self, key: str, *, limit: int, window_seconds: int) -> ThrottleDecision:
        if limit <= 0 or window_seconds <= 0:
            return ThrottleDecision(allowed=True)

        now = time()
        cutoff = now - window_seconds
        path = self._storage_path()
        if path is None:
            return ThrottleDecision(allowed=True)

        with self._lock:
            with self._connect(path) as connection:
                self._maybe_global_cleanup(connection, now)
                connection.execute(
                    "DELETE FROM throttle_hits WHERE bucket_key = ? AND hit_at <= ?",
                    (key, cutoff),
                )
                row = connection.execute(
                    """
                    SELECT COUNT(*) AS total_hits, MIN(hit_at) AS first_hit_at
                    FROM throttle_hits
                    WHERE bucket_key = ?
                    """,
                    (key,),
                ).fetchone()

        total_hits = int(row[0] or 0)
        if total_hits < limit:
            return ThrottleDecision(allowed=True)

        first_hit_at = float(row[1] or now)
        retry_after = max(1, int((first_hit_at + window_seconds) - now + 0.999))
        return ThrottleDecision(allowed=False, retry_after_seconds=retry_after)

    def hit(self, key: str, *, window_seconds: int) -> None:
        if window_seconds <= 0:
            return

        now = time()
        cutoff = now - window_seconds
        path = self._storage_path()
        if path is None:
            return

        with self._lock:
            with self._connect(path) as connection:
                self._maybe_global_cleanup(connection, now)
                connection.execute(
                    "DELETE FROM throttle_hits WHERE bucket_key = ? AND hit_at <= ?",
                    (key, cutoff),
                )
                connection.execute(
                    "INSERT INTO throttle_hits (bucket_key, hit_at) VALUES (?, ?)",
                    (key, now),
                )

    def _execute_write(self, sql: str, params: tuple = ()) -> None:
        path = self._storage_path()
        if path is None:
            return

        with self._lock:
            with self._connect(path) as connection:
                connection.execute(sql, params)

    def _storage_path(self) -> Path | None:
        if not has_app_context():
            return None

        configured_path = str(
            current_app.config.get("REQUEST_THROTTLE_STORAGE_PATH") or ""
        ).strip()
        if configured_path:
            return Path(configured_path).expanduser().resolve()

        return Path(current_app.instance_path) / "request_throttle.sqlite3"

    def _gc_interval_seconds(self) -> int:
        if not has_app_context():
            return 3600
        return int(current_app.config.get("REQUEST_THROTTLE_GC_INTERVAL_SECONDS", 3600) or 0)

    def _gc_max_age_seconds(self) -> int:
        if not has_app_context():
            return 604800
        return int(current_app.config.get("REQUEST_THROTTLE_GC_MAX_AGE_SECONDS", 604800) or 0)

    def _maybe_global_cleanup(self, connection: sqlite3.Connection, now: float) -> None:
        gc_interval = self._gc_interval_seconds()
        gc_max_age = self._gc_max_age_seconds()
        if gc_interval <= 0 or gc_max_age <= 0:
            return

        row = connection.execute(
            "SELECT value FROM throttle_meta WHERE meta_key = ?",
            (self._GC_META_KEY,),
        ).fetchone()
        last_cleanup_at = float(row[0]) if row and row[0] else 0.0
        if last_cleanup_at and (now - last_cleanup_at) < gc_interval:
            return

        connection.execute(
            "DELETE FROM throttle_hits WHERE hit_at <= ?",
            (now - gc_max_age,),
        )
        connection.execute(
            "INSERT OR REPLACE INTO throttle_meta (meta_key, value) VALUES (?, ?)",
            (self._GC_META_KEY, str(now)),
        )

    def _connect(self, path: Path) -> sqlite3.Connection:
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(path), timeout=5)
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS throttle_hits (
                bucket_key TEXT NOT NULL,
                hit_at REAL NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_throttle_hits_key_hit_at
            ON throttle_hits (bucket_key, hit_at)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_throttle_hits_hit_at
            ON throttle_hits (hit_at)
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS throttle_meta (
                meta_key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        return connection


class RequestThrottle:
    def __init__(self) -> None:
        self._memory_backend = InMemoryRequestThrottle()
        self._sqlite_backend = SQLiteRequestThrottle()

    def clear(self, key: str) -> None:
        self._backend().clear(key)

    def clear_all(self) -> None:
        self._backend().clear_all()

    def check(self, key: str, *, limit: int, window_seconds: int) -> ThrottleDecision:
        return self._backend().check(
            key,
            limit=limit,
            window_seconds=window_seconds,
        )

    def hit(self, key: str, *, window_seconds: int) -> None:
        self._backend().hit(key, window_seconds=window_seconds)

    def _backend(self):
        if not has_app_context():
            return self._memory_backend

        backend_name = str(
            current_app.config.get("REQUEST_THROTTLE_BACKEND") or "sqlite"
        ).strip().lower()
        if backend_name in {"memory", "inmemory", "in-memory", "in_memory"}:
            return self._memory_backend

        return self._sqlite_backend


request_throttle = RequestThrottle()

"""Small process-local TTL cache for public incident read responses."""

import time
from collections import OrderedDict
from threading import RLock

READ_CACHE_TTL_SECONDS = 15.0
READ_CACHE_MAX_ENTRIES = 8
READ_CACHE_MAX_BYTES = 8 * 1024 * 1024


class IncidentReadCache:
    def __init__(
        self,
        *,
        ttl_seconds: float,
        max_entries: int,
        max_bytes: int = READ_CACHE_MAX_BYTES,
    ) -> None:
        if ttl_seconds <= 0 or max_entries < 1 or max_bytes < 1:
            raise ValueError("TTL, max_entries, and max_bytes must be positive.")
        self._ttl_seconds = ttl_seconds
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self._entries: OrderedDict[tuple[str | None, ...], tuple[float, bytes]] = (
            OrderedDict()
        )
        self._lock = RLock()
        self._generation = 0

    @property
    def generation(self) -> int:
        with self._lock:
            return self._generation

    def get(self, key: tuple[str | None, ...]) -> bytes | None:
        now = time.monotonic()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            expires_at, payload = entry
            if expires_at <= now:
                del self._entries[key]
                return None
            self._entries.move_to_end(key)
            return payload

    def set(
        self,
        key: tuple[str | None, ...],
        payload: bytes,
        *,
        expected_generation: int | None = None,
    ) -> None:
        if len(payload) > self._max_bytes:
            return
        with self._lock:
            if (
                expected_generation is not None
                and expected_generation != self._generation
            ):
                return
            self._entries[key] = (time.monotonic() + self._ttl_seconds, payload)
            self._entries.move_to_end(key)
            while (
                len(self._entries) > self._max_entries
                or sum(len(item[1]) for item in self._entries.values())
                > self._max_bytes
            ):
                self._entries.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._generation += 1
            self._entries.clear()

    def stats(self) -> tuple[int, int]:
        """Return retained entries and JSON payload bytes, excluding object overhead."""
        now = time.monotonic()
        with self._lock:
            expired = [
                key
                for key, (expires_at, _) in self._entries.items()
                if expires_at <= now
            ]
            for key in expired:
                del self._entries[key]
            return len(self._entries), sum(
                len(payload) for _, payload in self._entries.values()
            )


incident_read_cache = IncidentReadCache(
    ttl_seconds=READ_CACHE_TTL_SECONDS,
    max_entries=READ_CACHE_MAX_ENTRIES,
)


def clear_read_cache() -> None:
    incident_read_cache.clear()


def read_cache_stats() -> tuple[int, int]:
    return incident_read_cache.stats()

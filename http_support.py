"""Small process-local policies shared by fixed-host API clients."""

from __future__ import annotations

import asyncio
import copy
import json
import math
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

JsonObject = dict[str, Any]
Clock = Callable[[], float]
Sleep = Callable[[float], Awaitable[None]]


@dataclass(frozen=True)
class _Entry:
    expires_at: float
    value: JsonObject
    size: int


class ResponseCache:
    """Copying TTL/LRU cache bounded by entries and serialized UTF-8 bytes."""

    def __init__(
        self, ttl: float, max_entries: int, max_bytes: int, *, clock: Clock | None = None
    ) -> None:
        if not math.isfinite(ttl) or ttl < 0 or max_entries < 0 or max_bytes < 0:
            raise ValueError("Cache limits must be finite and nonnegative.")
        self._ttl = ttl
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self._clock = clock or time.monotonic
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        self._bytes = 0

    def get(self, key: str) -> JsonObject | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        if self._clock() >= entry.expires_at:
            self._remove(key)
            return None
        self._entries.move_to_end(key)
        return copy.deepcopy(entry.value)

    def put(self, key: str, value: JsonObject, *, expires_at: float | None = None) -> None:
        self._remove(key)
        size = len(key.encode()) + len(json.dumps(value, ensure_ascii=False).encode())
        if size > self._max_bytes or self._max_entries == 0 or self._ttl == 0:
            return
        expiry = expires_at if expires_at is not None else self._clock() + self._ttl
        self._entries[key] = _Entry(expiry, copy.deepcopy(value), size)
        self._bytes += size
        while len(self._entries) > self._max_entries or self._bytes > self._max_bytes:
            self._remove(next(iter(self._entries)))

    def _remove(self, key: str) -> None:
        if entry := self._entries.pop(key, None):
            self._bytes -= entry.size

    def clear(self) -> None:
        self._entries.clear()
        self._bytes = 0


class CooldownActive(Exception):
    """The provider has asked this credential boundary to stop sending requests."""


class RequestPolicy:
    """Serialize upstream work while letting callers read caches independently."""

    def __init__(
        self, interval: float, *, clock: Clock | None = None, sleep: Sleep | None = None
    ) -> None:
        if not math.isfinite(interval) or interval < 0:
            raise ValueError("Request interval must be finite and nonnegative.")
        self.lock = asyncio.Lock()
        self._interval = interval
        self._clock = clock or time.monotonic
        self._sleep = sleep or asyncio.sleep
        self._next_request_at = 0.0
        self._blocked_until = 0.0

    async def wait(self) -> None:
        """Called with lock held, immediately before sending a request."""
        now = self._clock()
        if now < self._blocked_until:
            raise CooldownActive
        delay = self._next_request_at - now
        if delay > 0:
            await self._sleep(delay)
        self._next_request_at = self._clock() + self._interval

    def block(self, value: str | None) -> None:
        self._blocked_until = max(self._blocked_until, self._clock() + retry_after(value))


def retry_after(value: str | None, *, now: datetime | None = None) -> float:
    """Retry-After seconds/date, with a conservative default and one-hour cap."""
    try:
        seconds = float(value)
        return max(0, min(seconds, 3600)) if math.isfinite(seconds) else 60
    except (ValueError, TypeError):
        try:
            target = parsedate_to_datetime(value)
            if target.tzinfo is None:
                target = target.replace(tzinfo=UTC)
            return max(0, min((target - (now or datetime.now(UTC))).total_seconds(), 3600))
        except (ValueError, TypeError, OverflowError):
            return 60


class ResponseTooLarge(Exception):
    """Decoded upstream data exceeded the configured response budget."""


async def read_bounded(response: httpx.Response, maximum: int) -> bytes:
    body = bytearray()
    async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
        if len(body) + len(chunk) > maximum:
            raise ResponseTooLarge
        body.extend(chunk)
    return bytes(body)


def validate_request_limits(operation_timeout: float, max_response_bytes: int) -> None:
    if not math.isfinite(operation_timeout) or operation_timeout <= 0 or max_response_bytes <= 0:
        raise ValueError("Operation timeout and response limit must be finite and positive.")

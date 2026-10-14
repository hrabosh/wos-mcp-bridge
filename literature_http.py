"""Bounded, cached GET requests to two fixed literature API hosts only."""

from __future__ import annotations

import asyncio
import copy
import json
import time
from collections import OrderedDict
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

JsonObject = dict[str, Any]
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_CACHE_BYTES = 16 * 1024 * 1024
MAX_CACHE_ENTRIES = 128
API_URLS = {
    "Europe PMC": "https://www.ebi.ac.uk/europepmc/webservices/rest/",
    "OpenAlex": "https://api.openalex.org/",
}


class LiteratureError(Exception):
    """Safe, actionable upstream or validation error; never includes response bodies."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class ApiClient:
    """Process-local TTL/LRU cache and serialized throttling, with no retries."""

    def __init__(
        self,
        source: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        min_interval: float = 1.1,
        cache_ttl: float = 900,
        api_key: str = "",
    ) -> None:
        if min_interval < 0 or cache_ttl < 0:
            raise ValueError("Request interval and cache TTL cannot be negative.")
        self.source = source
        self._http = httpx.AsyncClient(
            base_url=API_URLS[source],
            headers={
                "Accept": "application/json, application/xml",
                **({"Authorization": f"Bearer {api_key.strip()}"} if api_key.strip() else {}),
            },
            timeout=httpx.Timeout(30, connect=10),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )
        self._ttl = cache_ttl
        self._interval = min_interval
        self._next_request_at = 0.0
        self._blocked_until = 0.0
        self._lock = asyncio.Lock()
        self._cache: OrderedDict[str, tuple[float, bytes, JsonObject]] = OrderedDict()
        self._cache_bytes = 0

    async def aclose(self) -> None:
        await self._http.aclose()
        self._cache.clear()
        self._cache_bytes = 0

    async def _get(self, path: str, params: JsonObject | None = None) -> tuple[bytes, JsonObject]:
        # Only internally constructed relative paths. Defense in depth against URL overrides.
        if ":" in path or path.startswith("/") or ".." in path.split("/"):
            raise LiteratureError("Invalid API endpoint.")
        key = json.dumps([path, params], sort_keys=True)
        async with self._lock:
            now = time.monotonic()
            if key in self._cache:
                expiry, body, retrieval = self._cache[key]
                if now < expiry:
                    self._cache.move_to_end(key)
                    return body, {**copy.deepcopy(retrieval), "cached": True}
                self._cache_bytes -= len(body)
                del self._cache[key]
            delay = self._next_request_at - now
            if now < self._blocked_until:
                raise LiteratureError(
                    f"{self.source} rate limit cooldown active; retry later.", status=429
                )
            if delay > 0:
                await asyncio.sleep(delay)
            self._next_request_at = time.monotonic() + self._interval
            try:
                async with self._http.stream("GET", path, params=params) as response:
                    if response.status_code != 200:
                        if response.status_code == 429:
                            self._blocked_until = time.monotonic() + _retry_after(
                                response.headers.get("retry-after")
                            )
                        messages = {
                            400: "rejected the query or identifier",
                            401: "requires a valid API key in the server environment",
                            403: "denied access",
                            404: "has no matching record or accessible full text",
                            429: "rate limit reached; retry later",
                        }
                        message = messages.get(
                            response.status_code, f"returned HTTP {response.status_code}"
                        )
                        raise LiteratureError(
                            f"{self.source} {message}. No automatic retry was made.",
                            status=response.status_code,
                        )
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                            raise LiteratureError(f"{self.source} response exceeds the size limit.")
                        body.extend(chunk)
            except httpx.TimeoutException:
                raise LiteratureError(f"{self.source} timed out; retry later.") from None
            except httpx.RequestError:
                raise LiteratureError(f"{self.source} could not be reached; retry later.") from None
            retrieval = {
                "source": self.source,
                "cached": False,
                "fetched_at_utc": datetime.now(UTC).isoformat(),
            }
            raw = bytes(body)
            self._cache[key] = (time.monotonic() + self._ttl, raw, retrieval)
            self._cache_bytes += len(raw)
            while len(self._cache) > MAX_CACHE_ENTRIES or self._cache_bytes > MAX_CACHE_BYTES:
                _, (_, removed, _) = self._cache.popitem(last=False)
                self._cache_bytes -= len(removed)
            return raw, copy.deepcopy(retrieval)

    async def _json(
        self, path: str, params: JsonObject | None = None
    ) -> tuple[JsonObject, JsonObject]:
        raw, retrieval = await self._get(path, params)
        try:
            data = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            raise LiteratureError(f"{self.source} returned invalid JSON.") from None
        if not isinstance(data, dict):
            raise LiteratureError(f"{self.source} returned malformed metadata.")
        return data, retrieval


def _retry_after(value: str | None) -> float:
    try:
        return max(0, min(float(value), 3600))
    except (ValueError, TypeError):
        try:
            return max(
                0, min((parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds(), 3600)
            )
        except (ValueError, TypeError, OverflowError):
            return 60

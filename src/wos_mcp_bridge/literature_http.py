"""Validated, bounded requests to two fixed literature API hosts only."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TypedDict

import httpx

from .diagnostics import measure, record_metric
from .errors import BridgeError, upstream_error_code
from .http_support import (
    CacheExpiry,
    Clock,
    CooldownActive,
    JsonObject,
    RequestPolicy,
    ResponseCache,
    ResponseTooLarge,
    Sleep,
    decode_json,
    read_bounded,
    validate_request_limits,
)

MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_CACHE_BYTES = 16 * 1024 * 1024
MAX_CACHE_ENTRIES = 128
API_URLS = {
    "Europe PMC": "https://www.ebi.ac.uk/europepmc/webservices/rest/",
    "OpenAlex": "https://api.openalex.org/",
}


def _cache_key(path: str, params: JsonObject | None) -> str:
    return json.dumps([path, params], sort_keys=True)


class ApiOptions(TypedDict, total=False):
    transport: httpx.AsyncBaseTransport | None
    min_interval: float
    cache_ttl: float
    max_response_bytes: int
    cache_max_bytes: int
    cache_max_entries: int
    operation_timeout: float
    clock: Clock | None
    sleep: Sleep | None


class LiteratureError(BridgeError):
    """Safe upstream or validation error; never includes response bodies."""

    default_source = "literature"


class ApiClient:
    """Own a fixed-host connection pool, validated cache and upstream policy."""

    def __init__(
        self,
        source: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        min_interval: float = 1.1,
        cache_ttl: float = 900,
        api_key: str = "",
        max_response_bytes: int = MAX_RESPONSE_BYTES,
        cache_max_bytes: int = MAX_CACHE_BYTES,
        cache_max_entries: int = MAX_CACHE_ENTRIES,
        operation_timeout: float = 30,
        clock: Clock | None = None,
        sleep: Sleep | None = None,
    ) -> None:
        validate_request_limits(operation_timeout, max_response_bytes)
        self._cache = ResponseCache(cache_ttl, cache_max_entries, cache_max_bytes, clock=clock)
        self._policy = RequestPolicy(min_interval, clock=clock, sleep=sleep)
        self._operation_timeout = operation_timeout
        self._max_response_bytes = max_response_bytes
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

    async def aclose(self) -> None:
        await self._http.aclose()
        self._cache.clear()

    def cache_expiry(self, path: str, params: JsonObject | None = None) -> CacheExpiry | None:
        return self._cache.expiry(_cache_key(path, params))

    def _cached(self, key: str) -> JsonObject | None:
        result = self._cache.get(key)
        if result is not None:
            record_metric(self.source, "cache_hits")
            result["retrieval"]["cached"] = True
        return result

    async def get(
        self,
        path: str,
        params: JsonObject | None = None,
        *,
        parse: Callable[[bytes], JsonObject],
    ) -> tuple[JsonObject, JsonObject]:
        if ":" in path or path.startswith("/") or ".." in path.split("/"):
            raise LiteratureError("Invalid API endpoint.", source=self.source, code="invalid_input")
        key = _cache_key(path, params)
        if cached := self._cached(key):
            return cached["data"], cached["retrieval"]
        try:
            async with asyncio.timeout(self._operation_timeout), self._policy.acquire(self.source):
                if cached := self._cached(key):
                    return cached["data"], cached["retrieval"]
                with measure(self.source, "limiter_wait_ms"):
                    await self._policy.wait()
                record_metric(self.source, "upstream_requests")
                with measure(self.source, "network_ms"):
                    async with self._http.stream("GET", path, params=params) as response:
                        if response.status_code != 200:
                            self._raise_http_error(response)
                        raw = await read_bounded(response, self._max_response_bytes)
                with measure(self.source, "parse_ms"):
                    data = parse(raw)
                retrieval = {
                    "source": self.source,
                    "cached": False,
                    "fetched_at_utc": datetime.now(UTC).isoformat(),
                }
                self._cache.put(key, {"data": data, "retrieval": retrieval})
                return data, retrieval
        except CooldownActive as exc:
            raise LiteratureError(
                f"{self.source} rate limit cooldown active; retry later.",
                code="rate_limited",
                source=self.source,
                retry_after_seconds=exc.retry_after_seconds,
            ) from None
        except (TimeoutError, httpx.TimeoutException):
            raise LiteratureError(
                f"{self.source} timed out; retry later.",
                code="timeout",
                source=self.source,
            ) from None
        except httpx.RequestError:
            raise LiteratureError(
                f"{self.source} could not be reached; retry later.",
                code="upstream_unavailable",
                source=self.source,
            ) from None
        except ResponseTooLarge:
            raise LiteratureError(
                f"{self.source} response exceeds the size limit.",
                source=self.source,
            ) from None
        except LiteratureError as exc:
            if exc.source == LiteratureError.default_source:
                exc.source = self.source
            raise

    def _raise_http_error(self, response: httpx.Response) -> None:
        status = response.status_code
        delay = self._policy.block(response.headers.get("retry-after")) if status == 429 else None
        message = {
            400: "rejected the query or identifier",
            401: "requires a valid API key in the server environment",
            403: "denied access",
            404: "has no matching record or accessible full text",
            429: "rate limit reached; retry later",
        }.get(status, f"returned HTTP {status}")
        raise LiteratureError(
            f"{self.source} {message}. No automatic retry was made.",
            code=upstream_error_code(status),
            source=self.source,
            status=status,
            retry_after_seconds=delay,
        )

    async def get_json(
        self,
        path: str,
        params: JsonObject | None = None,
        *,
        parse: Callable[[JsonObject], JsonObject],
    ) -> tuple[JsonObject, JsonObject]:
        def decode(raw: bytes) -> JsonObject:
            try:
                data = decode_json(raw)
            except (ValueError, UnicodeDecodeError):
                raise LiteratureError(f"{self.source} returned invalid JSON.") from None
            if not isinstance(data, dict):
                raise LiteratureError(f"{self.source} returned malformed metadata.")
            return parse(data)

        return await self.get(path, params, parse=decode)

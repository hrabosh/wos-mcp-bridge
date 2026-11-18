"""Read-only Web of Science Starter client, independent of MCP."""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, urlsplit

import httpx
from pydantic import ValidationError

from .diagnostics import measure, record_metric
from .errors import BridgeError, upstream_error_code
from .http_support import (
    Clock,
    CooldownActive,
    RequestPolicy,
    ResponseCache,
    ResponseTooLarge,
    Sleep,
    decode_json,
    read_bounded,
    validate_request_limits,
)
from .models import Bibliography, WosRecord

BASE_URL = "https://api.clarivate.com/apis/wos-starter/v1/"
SOURCE = "Clarivate"
UID_PATTERN = re.compile(r"WOS:[A-Za-z0-9][A-Za-z0-9._-]{0,99}\Z")
SORT_FIELDS = {"RS+D", "RS+A", "PY+D", "PY+A", "LD+D", "LD+A", "TC+D", "TC+A"}
MAX_CACHE_ENTRIES = 128
MAX_CACHE_BYTES = 16 * 1024 * 1024
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
LIMITATIONS = (
    "Bibliographic metadata only: no abstracts, full texts, or Journal Impact Factors. "
    "Missing citation counts are unknown, not zero."
)
QUOTA_HEADERS = (
    "x-ratelimit-limit-day",
    "x-ratelimit-remaining-day",
    "x-ratelimit-limit-second",
    "x-ratelimit-remaining-second",
)
JsonObject = dict[str, Any]


class WosError(BridgeError):
    """An actionable error safe to return to an MCP caller."""

    default_source = SOURCE


class WosClient:
    """One API key, one connection pool, and a small process-local response cache.

    The caller must close the client with ``await client.aclose()``. Run one
    server process per key; neither the cache nor the limiter spans replicas.
    """

    def __init__(
        self,
        api_key: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        min_interval: float = 1.1,
        cache_ttl: float = 900.0,
        max_response_bytes: int = MAX_RESPONSE_BYTES,
        cache_max_bytes: int = MAX_CACHE_BYTES,
        cache_max_entries: int = MAX_CACHE_ENTRIES,
        operation_timeout: float = 30,
        clock: Clock | None = None,
        sleep: Sleep | None = None,
    ) -> None:
        if not api_key.strip():
            raise WosError(
                "Set WOS_API_KEY in the server environment, not in chat or source code.",
                code="auth",
            )
        validate_request_limits(operation_timeout, max_response_bytes)
        self._cache = ResponseCache(cache_ttl, cache_max_entries, cache_max_bytes, clock=clock)
        self._policy = RequestPolicy(min_interval, clock=clock, sleep=sleep)
        self._clock = clock or time.monotonic
        self._operation_timeout = operation_timeout
        self._max_response_bytes = max_response_bytes
        self._http = httpx.AsyncClient(
            base_url=BASE_URL,
            headers={"X-ApiKey": api_key.strip(), "Accept": "application/json"},
            timeout=httpx.Timeout(30.0, connect=10.0),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )
        self._ttl = cache_ttl

    async def aclose(self) -> None:
        """Release pooled connections and cached records."""
        await self._http.aclose()
        self._cache.clear()

    async def search(
        self, query: str, limit: int = 10, page: int = 1, sort: str = "RS+D"
    ) -> JsonObject:
        """Search one page using WoS advanced syntax; never paginate automatically."""
        if not isinstance(query, str):
            raise WosError(
                "query must be a Web of Science advanced-search string.", code="invalid_input"
            )
        query = query.strip()
        if not query or len(query) > 4000 or any(ord(c) < 32 for c in query):
            raise WosError(
                "Use a query of 1-4000 characters without control characters.", code="invalid_input"
            )
        if type(limit) is not int or not 1 <= limit <= 50:
            raise WosError("limit must be an integer from 1 to 50.", code="invalid_input")
        if type(page) is not int or not 1 <= page <= 2_147_483_647:
            raise WosError("page must be a positive 32-bit integer.", code="invalid_input")
        if sort not in SORT_FIELDS:
            raise WosError(
                "Unsupported sort. Use RS+D for relevance or PY+D for newest first.",
                code="invalid_input",
            )

        key = json.dumps(["search", query, limit, page, sort])
        if cached := self._cached(key):
            return cached
        async with self._operation():
            if cached := self._cached(key):
                return cached
            data, retrieval = await self._request(
                "documents",
                {"db": "WOS", "q": query, "limit": limit, "page": page, "sortField": sort},
            )
            metadata = data.get("metadata")
            if not isinstance(metadata, dict) or not isinstance(data.get("hits"), list):
                raise WosError("Unexpected search response structure from Clarivate.")
            if type(metadata.get("total")) is not int or metadata["total"] < 0:
                raise WosError("Clarivate returned an invalid total result count.")
            with measure(SOURCE, "parse_ms"):
                records = [_normalise(hit) for hit in data["hits"]]
            result = {
                "query": query,
                "database": "WOS",
                "sort": sort,
                "metadata": {"total": metadata["total"], "page": page, "limit": limit},
                "results": records,
                "retrieval": retrieval,
                "limitations": LIMITATIONS,
            }
            # All entries from this response share an expiry. Cache hits never
            # extend it, so repeated searches cannot keep stale records alive.
            expires_at = self._clock() + self._ttl
            for record in records:
                self._remember(record["id"], {"record": record, "retrieval": retrieval}, expires_at)
            self._remember(key, result, expires_at)
            return result

    async def fetch(self, uid: str) -> JsonObject:
        """Retrieve a WoS bibliographic record, not the corresponding article PDF."""
        if not isinstance(uid, str) or not UID_PATTERN.fullmatch(uid):
            raise WosError(
                "Use a WOS: accession ID returned by search, not a URL or DOI.",
                code="invalid_input",
            )
        if envelope := self._cached(uid):
            return self._fetch_result(envelope)
        async with self._operation():
            envelope = self._cached(uid)
            if envelope is None:
                data, retrieval = await self._request("documents/" + quote(uid, safe=""))
                with measure(SOURCE, "parse_ms"):
                    record = _normalise(data)
                if record["id"] != uid:
                    raise WosError("Clarivate returned a different record ID than requested.")
                envelope = {"record": record, "retrieval": retrieval}
                self._remember(uid, envelope, self._clock() + self._ttl)
            return self._fetch_result(envelope)

    @staticmethod
    def _fetch_result(envelope: JsonObject) -> JsonObject:
        record = envelope["record"]
        return {
            "id": record["id"],
            "title": record["title"],
            "url": record["url"],
            "text": (
                "Web of Science Starter bibliographic metadata, NOT article full text "
                "or abstract.\n" + json.dumps(record, ensure_ascii=False)
            ),
            "metadata": {
                **record,
                "retrieval": envelope["retrieval"],
                "limitations": LIMITATIONS,
            },
        }

    async def _request(
        self, path: str, params: JsonObject | None = None
    ) -> tuple[JsonObject, JsonObject]:
        """Called under the lock; issue one request without following redirects."""
        with measure(SOURCE, "limiter_wait_ms"):
            await self._policy.wait()
        record_metric(SOURCE, "upstream_requests")
        delay = None
        try:
            with measure(SOURCE, "network_ms"):
                async with self._http.stream("GET", path, params=params) as response:
                    if response.status_code == 429:
                        delay = self._policy.block(response.headers.get("retry-after"))
                    raw = (
                        await read_bounded(response, self._max_response_bytes)
                        if response.status_code == 200
                        else b""
                    )
        except httpx.TimeoutException:
            raise WosError("Clarivate operation timed out; retry later.", code="timeout") from None
        except ResponseTooLarge:
            raise WosError("Clarivate response exceeds the size limit.") from None
        except httpx.RequestError:
            raise WosError(
                "Clarivate could not be reached. Check connectivity or retry later.",
                code="upstream_unavailable",
            ) from None

        if response.status_code != 200:
            # Do not echo upstream error bodies, URLs, or authentication headers.
            messages = {
                400: "Clarivate rejected the query. Check the WoS field tags and syntax.",
                401: "Clarivate rejected the API key. Check the active Starter subscription.",
                403: "Clarivate denied access. Check your API and database entitlements.",
                404: "No matching Web of Science record was found.",
                429: "Clarivate rate limit reached. Stop and check the daily/per-second quota.",
            }
            raise WosError(
                messages.get(
                    response.status_code, f"Clarivate returned HTTP {response.status_code}."
                )
                + " No automatic retry was made.",
                code=upstream_error_code(response.status_code),
                status=response.status_code,
                retry_after_seconds=delay,
            )
        try:
            with measure(SOURCE, "parse_ms"):
                data = decode_json(raw)
        except ValueError:
            raise WosError("Clarivate returned an invalid JSON response.") from None
        if not isinstance(data, dict):
            raise WosError("Unexpected response structure from Clarivate.")
        return data, {
            "cached": False,
            "fetched_at_utc": datetime.now(UTC).isoformat(),
            "quota_at_fetch": {
                name: response.headers[name] for name in QUOTA_HEADERS if name in response.headers
            },
        }

    @asynccontextmanager
    async def _operation(self) -> AsyncGenerator[None]:
        try:
            async with asyncio.timeout(self._operation_timeout), self._policy.acquire(SOURCE):
                yield
        except TimeoutError:
            raise WosError("Clarivate operation timed out; retry later.", code="timeout") from None
        except CooldownActive as exc:
            raise WosError(
                "Clarivate rate limit cooldown active; retry later.",
                code="rate_limited",
                retry_after_seconds=exc.retry_after_seconds,
            ) from None

    def _cached(self, key: str) -> JsonObject | None:
        result = self._cache.get(key)
        if result is not None:
            record_metric(SOURCE, "cache_hits")
            result["retrieval"]["cached"] = True
        return result

    def _remember(self, key: str, value: JsonObject, expires_at: float) -> None:
        self._cache.put(key, value, expires_at=expires_at)


def _object(value: Any) -> JsonObject:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise WosError("Clarivate returned malformed record metadata.")
    return value


def _normalise(hit: Any) -> JsonObject:
    """Keep bibliographic fields, without inventing missing dates or citation counts."""
    hit = _object(hit)
    uid, title = hit.get("uid"), hit.get("title")
    if not isinstance(uid, str) or not UID_PATTERN.fullmatch(uid):
        raise WosError("Clarivate returned an invalid Web of Science accession ID.")
    if not isinstance(title, str) or not title.strip():
        raise WosError("Clarivate returned a record without a valid title.")
    source = _object(hit.get("source"))
    identifiers = _object(hit.get("identifiers"))
    names = _object(hit.get("names"))
    citations = hit.get("citations")
    citations = [] if citations is None else citations
    authors = names.get("authors")
    authors = [] if authors is None else authors
    if not isinstance(citations, list) or not isinstance(authors, list):
        raise WosError("Clarivate returned malformed author or citation metadata.")
    counts = {}
    for item in citations:
        if not isinstance(item, dict):
            raise WosError("Clarivate returned malformed citation metadata.")
        count = item.get("count")
        if count is None:
            continue
        database = item.get("db")
        if (
            not isinstance(database, str)
            or not database.strip()
            or type(count) is not int
            or count < 0
        ):
            raise WosError("Clarivate returned malformed citation metadata.")
        counts[database] = count
    keywords = _object(hit.get("keywords")).get("authorKeywords")
    record = {
        "id": uid,
        "title": title,
        "url": _record_url(_object(hit.get("links")).get("record"), uid),
        "authors": [
            author["displayName"]
            for author in authors
            if isinstance(author, dict) and isinstance(author.get("displayName"), str)
        ],
        "journal": source.get("sourceTitle"),
        "year": source.get("publishYear"),
        "volume": source.get("volume"),
        "issue": source.get("issue"),
        "pages": source.get("pages"),
        "doi": identifiers.get("doi"),
        "pmid": identifiers.get("pmid"),
        "types": hit.get("types") if hit.get("types") is not None else [],
        "keywords": [] if keywords is None else keywords,
        "citations": counts or None,
    }

    try:
        record["bibliography"] = Bibliography.from_metadata(
            authors=(
                record["authors"]
                if names.get("authors") is not None and len(record["authors"]) == len(authors)
                else None
            ),
            year=record["year"],
            journal=record["journal"],
        ).model_dump(mode="json")
        return WosRecord.model_validate(record).model_dump(mode="json", exclude_unset=True)
    except ValidationError:
        raise WosError("Clarivate returned malformed bibliographic metadata.") from None


def _record_url(candidate: Any, uid: str) -> str:
    """Only expose WoS record links; never follow URLs from the upstream payload."""
    if isinstance(candidate, str):
        try:
            parsed = urlsplit(candidate)
            if (
                parsed.scheme == "https"
                and parsed.hostname in {"webofscience.com", "www.webofscience.com"}
                and not parsed.username
                and not parsed.password
                and parsed.port in {None, 443}
            ):
                return candidate
        except ValueError:
            pass
    return f"https://www.webofscience.com/wos/woscc/full-record/{quote(uid, safe=':')}"

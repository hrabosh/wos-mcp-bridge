"""Read-only Web of Science Starter client, independent of MCP."""

from __future__ import annotations

import asyncio
import copy
import json
import re
import time
from collections import OrderedDict
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

BASE_URL = "https://api.clarivate.com/apis/wos-starter/v1/"
UID_PATTERN = re.compile(r"WOS:[A-Za-z0-9][A-Za-z0-9._-]{0,99}\Z")
SORT_FIELDS = {"RS+D", "RS+A", "PY+D", "PY+A", "LD+D", "LD+A", "TC+D", "TC+A"}
MAX_CACHE_ENTRIES = 128
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


class WosError(Exception):
    """An actionable error safe to return to an MCP caller."""


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
    ) -> None:
        if not api_key.strip():
            raise WosError("Set WOS_API_KEY in the server environment, not in chat or source code.")
        if min_interval < 0 or cache_ttl < 0:
            raise ValueError("Request interval and cache TTL cannot be negative.")
        self._http = httpx.AsyncClient(
            base_url=BASE_URL,
            headers={"X-ApiKey": api_key.strip(), "Accept": "application/json"},
            timeout=httpx.Timeout(30.0, connect=10.0),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )
        self._interval = min_interval
        self._ttl = cache_ttl
        self._next_request_at = 0.0
        self._lock = asyncio.Lock()
        self._cache: OrderedDict[str, tuple[float, JsonObject]] = OrderedDict()

    async def aclose(self) -> None:
        """Release pooled connections and cached records."""
        await self._http.aclose()
        self._cache.clear()

    async def search(
        self, query: str, limit: int = 10, page: int = 1, sort: str = "RS+D"
    ) -> JsonObject:
        """Search one page using WoS advanced syntax; never paginate automatically."""
        if not isinstance(query, str):
            raise WosError("query must be a Web of Science advanced-search string.")
        query = query.strip()
        if not query or len(query) > 4000 or any(ord(c) < 32 for c in query):
            raise WosError("Use a query of 1-4000 characters without control characters.")
        if type(limit) is not int or not 1 <= limit <= 50:
            raise WosError("limit must be an integer from 1 to 50.")
        if type(page) is not int or not 1 <= page <= 2_147_483_647:
            raise WosError("page must be a positive 32-bit integer.")
        if sort not in SORT_FIELDS:
            raise WosError("Unsupported sort. Use RS+D for relevance or PY+D for newest first.")

        key = json.dumps(["search", query, limit, page, sort])
        async with self._lock:
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
            expires_at = time.monotonic() + self._ttl
            for record in records:
                self._remember(record["id"], {"record": record, "retrieval": retrieval}, expires_at)
            self._remember(key, result, expires_at)
            return result

    async def fetch(self, uid: str) -> JsonObject:
        """Retrieve a WoS bibliographic record, not the corresponding article PDF."""
        if not isinstance(uid, str) or not UID_PATTERN.fullmatch(uid):
            raise WosError("Use a WOS: accession ID returned by search, not a URL or DOI.")
        async with self._lock:
            envelope = self._cached(uid)
            if envelope is None:
                data, retrieval = await self._request("documents/" + quote(uid, safe=""))
                record = _normalise(data)
                if record["id"] != uid:
                    raise WosError("Clarivate returned a different record ID than requested.")
                envelope = {"record": record, "retrieval": retrieval}
                self._remember(uid, envelope, time.monotonic() + self._ttl)
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
        delay = self._next_request_at - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)
        self._next_request_at = time.monotonic() + self._interval
        try:
            response = await self._http.get(path, params=params)
        except httpx.RequestError:
            raise WosError(
                "Clarivate could not be reached. Check connectivity or retry later."
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
                + " No automatic retry was made."
            )
        try:
            data = response.json()
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

    def _cached(self, key: str) -> JsonObject | None:
        entry = self._cache.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if time.monotonic() >= expires_at:
            del self._cache[key]
            return None
        self._cache.move_to_end(key)
        result = copy.deepcopy(value)
        result["retrieval"]["cached"] = True
        return result

    def _remember(self, key: str, value: JsonObject, expires_at: float) -> None:
        self._cache[key] = (expires_at, copy.deepcopy(value))
        self._cache.move_to_end(key)
        while len(self._cache) > MAX_CACHE_ENTRIES:
            self._cache.popitem(last=False)


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
    citations = hit.get("citations") or []
    authors = names.get("authors") or []
    if not isinstance(citations, list) or not isinstance(authors, list):
        raise WosError("Clarivate returned malformed author or citation metadata.")
    counts = {
        item["db"]: item["count"]
        for item in citations
        if isinstance(item, dict)
        and isinstance(item.get("db"), str)
        and type(item.get("count")) is int
        and item["count"] >= 0
    }
    return {
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
        "types": hit.get("types") or [],
        "keywords": _object(hit.get("keywords")).get("authorKeywords") or [],
        "citations": counts or None,
    }


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

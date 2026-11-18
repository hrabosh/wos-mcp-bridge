"""Read-only literature orchestration and bounded, normalized article responses."""

from __future__ import annotations

import copy
import json
from collections import OrderedDict
from dataclasses import dataclass

from .article_xml import validate_text
from .diagnostics import record_metric
from .europe_pmc import EuropePmcClient, identifier, safe_url
from .http_support import CacheExpiry
from .literature_http import JsonObject, LiteratureError
from .models import Bibliography
from .openalex_client import OpenAlexClient

MAX_TEXT_CHARS = 20_000
MAX_ARTICLE_CACHE_ENTRIES = 32
MAX_ARTICLE_CACHE_BYTES = 8 * 1024 * 1024

ArticleId = tuple[str, str]


@dataclass(frozen=True)
class _CachedArticle:
    article: JsonObject
    aliases: set[ArticleId]
    source_expiries: list[CacheExpiry]
    size: int


def _budget(max_chars: int) -> None:
    if type(max_chars) is not int or not 1 <= max_chars <= MAX_TEXT_CHARS:
        raise LiteratureError(
            f"max_chars must be an integer from 1 to {MAX_TEXT_CHARS}.", code="invalid_input"
        )


def _bounded(article: JsonObject, max_chars: int, *, include_text: bool) -> JsonObject:
    article = copy.deepcopy(article)
    remaining = max_chars
    total = 0
    returned = 0
    abstract = article.get("abstract")
    if abstract is not None:
        total += len(abstract)
        article["abstract"] = abstract[:remaining]
        returned += len(article["abstract"])
        remaining -= len(article["abstract"])
        article["abstract_total_chars"] = len(abstract)
        article["abstract_truncated"] = len(article["abstract"]) < len(abstract)
    for section in article["sections"]:
        text = section.pop("text")
        total += len(text)
        if include_text:
            section["text"] = text[:remaining]
            count = len(section["text"])
            remaining -= count
            returned += count
            section["truncated"] = count < len(text)
            section["next_offset"] = count if count < len(text) else None
    article["text_budget"] = {
        "max_chars": max_chars,
        "returned_chars": returned,
        "total_chars": total,
        "truncated": returned < total,
        "section_text_included": include_text,
    }
    return article


def deduplicate(records: list[JsonObject]) -> list[JsonObject]:
    """Merge DOI/PMCID-connected records, including transitive duplicate chains."""
    parents = list(range(len(records)))

    def root(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    seen: dict[tuple[str, str], int] = {}
    for index, record in enumerate(records):
        for field in ("doi", "pmcid", "id"):
            if record.get(field) is not None:
                token = (field, record[field])
                if token in seen:
                    parents[root(index)] = root(seen[token])
                seen[token] = index
    groups: dict[int, list[JsonObject]] = {}
    for index, record in enumerate(records):
        groups.setdefault(root(index), []).append(record)
    result = []
    for group in groups.values():
        # Keep the OA flag tied to the PMCID of the record that reported it.
        # A shared DOI can have multiple repository records with different permissions.
        preferred = next((r for r in group if r["access"]["full_text_xml_available"]), group[0])
        primary = copy.deepcopy(preferred)
        for extra in group:
            for key, value in extra.items():
                if primary.get(key) is None and value is not None:
                    primary[key] = copy.deepcopy(value)
            if extra["access"]["status"] == "open_access":
                primary["access"]["status"] = "open_access"
            for field in ("authors", "year", "journal"):
                if primary["bibliography"][field] is None:
                    primary["bibliography"][field] = copy.deepcopy(extra["bibliography"][field])
            for location in extra["oa_locations"]:
                if location not in primary["oa_locations"]:
                    primary["oa_locations"].append(copy.deepcopy(location))
        primary["access"]["content_status"] = (
            "abstract_only" if primary["abstract"] else "metadata_only"
        )
        result.append(primary)
    return result


class LiteratureClient:
    """Compose independent API clients; the server owns and closes their pools."""

    def __init__(
        self,
        europe_pmc: EuropePmcClient | None = None,
        openalex: OpenAlexClient | None = None,
        *,
        openalex_api_key: str = "",
    ) -> None:
        self.europe_pmc = europe_pmc or EuropePmcClient()
        self.openalex = openalex or OpenAlexClient(openalex_api_key)
        # Services creates a LiteratureClient per provider/credential boundary.
        self._articles: OrderedDict[str, _CachedArticle] = OrderedDict()
        self._article_bytes = 0

    async def aclose(self) -> None:
        self.clear_article_cache()
        try:
            await self.europe_pmc.aclose()
        finally:
            await self.openalex.aclose()

    def clear_article_cache(self) -> None:
        self._articles.clear()
        self._article_bytes = 0

    async def search(
        self, query: str, limit: int = 10, cursor: str = "*", max_chars: int = 10_000
    ) -> JsonObject:
        _budget(max_chars)
        result = await self.europe_pmc.search(query, limit, cursor)
        remaining = max_chars
        bounded = []
        for record in deduplicate(result["results"]):
            item = _bounded(record, remaining, include_text=False)
            remaining -= item["text_budget"]["returned_chars"]
            bounded.append(item)
        return {
            **result,
            "results": bounded,
            "returned_count": len(bounded),
            "max_chars": max_chars,
            "limitations": "Europe PMC page only; OA XML availability is metadata-reported. "
            "Use resolve_article for OpenAlex OA links. No automatic pagination.",
        }

    async def _resolve(
        self,
        value: str,
        *,
        source_expiries: list[CacheExpiry | None] | None = None,
        verified_aliases: set[ArticleId] | None = None,
    ) -> JsonObject:
        kind, key = identifier(value)
        retrieval = []
        warnings = []
        pmc_error: LiteratureError | None = None
        try:
            found = await self.europe_pmc.resolve(value, source_expiries=source_expiries)
            article = found["article"]
            retrieval.append(found["retrieval"])
            if verified_aliases is not None and article is not None:
                verified_aliases.add((kind, key))
                # A DOI can refer to several repository copies. Only the DOI
                # resolution itself establishes which copy that route selects.
                for field in ("pmcid", "pmid"):
                    if article[field]:
                        verified_aliases.add((field, article[field]))
        except LiteratureError as exc:
            # A DOI/PMID can still be resolved by OpenAlex when Europe PMC is down.
            if kind == "pmcid":
                raise
            pmc_error = exc
            article = None
            warnings.append(str(exc))
        lookup = (article.get("doi") or article.get("pmid")) if article else None
        if not lookup and kind in {"doi", "pmid"}:
            lookup = key
        if lookup:
            try:
                oa = await self.openalex.resolve(lookup, source_expiries=source_expiries)
                if oa["retrieval"]:
                    retrieval.append(oa["retrieval"])
                supplement = oa["article"]
                if supplement:
                    if article is None:
                        article = supplement
                    else:
                        article["source"].append("OpenAlex")
                        article["oa_locations"].extend(supplement["oa_locations"])
                        for field in ("authors", "year", "journal"):
                            if article["bibliography"][field] is None:
                                article["bibliography"][field] = supplement["bibliography"][field]
                        for field in ("doi", "pmid", "license", "url"):
                            if article.get(field) is None:
                                article[field] = supplement[field]
                        if article["access"]["status"] != "open_access":
                            article["access"]["status"] = supplement["access"]["status"]
            except LiteratureError as exc:
                if article is None:
                    if pmc_error is not None and pmc_error.retryable and not exc.retryable:
                        raise pmc_error from None
                    raise
                warnings.append(str(exc))
        if article is None and pmc_error is not None:
            raise LiteratureError(
                f"Article resolution is incomplete: {pmc_error}",
                code=pmc_error.code,
                source=pmc_error.source,
                status=pmc_error.status,
                retry_after_seconds=pmc_error.retry_after_seconds,
            )
        if article is None:
            return {
                "id": value,
                "title": None,
                "doi": key if kind == "doi" else None,
                "pmid": key if kind == "pmid" else None,
                "pmcid": key if kind == "pmcid" else None,
                "url": None,
                "license": None,
                "source": [],
                "abstract": None,
                "access": {
                    "status": "unknown",
                    "full_text_xml_available": False,
                    "content_status": "not_found",
                },
                "oa_locations": [],
                "sections": [],
                "warnings": [],
                "bibliography": Bibliography(authors=None, year=None, journal=None).model_dump(),
                "retrieval": retrieval,
            }
        article["warnings"].extend(warnings)
        article["retrieval"] = retrieval
        return article

    async def resolve(self, value: str, max_chars: int = 10_000) -> JsonObject:
        _budget(max_chars)
        return _bounded(await self._resolve(value), max_chars, include_text=False)

    def _cached_article(self, alias: ArticleId) -> JsonObject | None:
        for pmcid, entry in reversed(self._articles.items()):
            if alias not in entry.aliases:
                continue
            if any(expiry.expired() for expiry in entry.source_expiries):
                self._article_bytes -= self._articles.pop(pmcid).size
                return None
            self._articles.move_to_end(pmcid)
            record_metric("literature", "normalized_cache_hits")
            return {
                **entry.article,
                "retrieval": [{**item, "cached": True} for item in entry.article["retrieval"]],
            }
        return None

    def _cache_article(
        self, article: JsonObject, aliases: set[ArticleId], expiries: list[CacheExpiry | None]
    ) -> None:
        if article["warnings"] or not aliases or len(expiries) != len(article["retrieval"]):
            return
        source_expiries = [expiry for expiry in expiries if expiry is not None]
        if len(source_expiries) != len(expiries) or any(e.expired() for e in source_expiries):
            return
        size = len(json.dumps(article, ensure_ascii=False).encode())
        if size > MAX_ARTICLE_CACHE_BYTES:
            return

        pmcid = article["pmcid"]
        if previous := self._articles.pop(pmcid, None):
            self._article_bytes -= previous.size
        self._articles[pmcid] = _CachedArticle(article, aliases, source_expiries, size)
        self._article_bytes += size
        while (
            len(self._articles) > MAX_ARTICLE_CACHE_ENTRIES
            or self._article_bytes > MAX_ARTICLE_CACHE_BYTES
        ):
            self._article_bytes -= self._articles.popitem(last=False)[1].size

    async def _article(self, value: str) -> JsonObject:
        if cached := self._cached_article(identifier(value)):
            return cached
        source_expiries: list[CacheExpiry | None] = []
        aliases: set[ArticleId] = set()
        article = await self._resolve(
            value, source_expiries=source_expiries, verified_aliases=aliases
        )
        if not article["access"]["full_text_xml_available"]:
            return article
        try:
            full = await self.europe_pmc.full_text(
                article["pmcid"], source_expiries=source_expiries
            )
        except LiteratureError as exc:
            if exc.status not in {403, 404}:
                raise
            article["access"]["full_text_xml_available"] = False
            article["warnings"].append(str(exc))
            return article
        article["sections"] = full["sections"]
        article["license"] = full["license"] or article["license"]
        article["license_url"] = safe_url(full["license_url"])
        article["retrieval"].append(full["retrieval"])
        article["access"]["content_status"] = "full_text"
        self._cache_article(article, aliases, source_expiries)
        return article

    async def get_article(
        self, value: str, max_chars: int = 10_000, include_text: bool = True
    ) -> JsonObject:
        _budget(max_chars)
        if type(include_text) is not bool:
            raise LiteratureError("include_text must be a boolean.", code="invalid_input")
        return _bounded(await self._article(value), max_chars, include_text=include_text)

    async def get_section(
        self, value: str, section_id: str, offset: int = 0, max_chars: int = 10_000
    ) -> JsonObject:
        _budget(max_chars)
        section_id = validate_text(section_id, "section_id", 512)
        if type(offset) is not int or not 0 <= offset <= 2_147_483_647:
            raise LiteratureError(
                "offset must be a nonnegative 32-bit integer.", code="invalid_input"
            )
        article = await self._article(value)
        section = next((item for item in article["sections"] if item["id"] == section_id), None)
        if section is None:
            raise LiteratureError(
                "Section not found. Use get_article to inspect available section IDs "
                f"(content status: {article['access']['content_status']}).",
                code="not_found",
            )
        if offset > section["total_chars"]:
            raise LiteratureError("offset exceeds the section length.", code="invalid_input")
        text = section["text"][offset : offset + max_chars]
        end = offset + len(text)
        return copy.deepcopy(
            {
                "id": article["id"],
                "doi": article["doi"],
                "pmcid": article["pmcid"],
                "pmid": article["pmid"],
                "url": article["url"],
                "license": article["license"],
                "source": article["source"],
                "access": article["access"],
                "bibliography": article["bibliography"],
                "section": {
                    **section,
                    "text": text,
                    "offset": offset,
                    "truncated": end < section["total_chars"],
                    "next_offset": end if end < section["total_chars"] else None,
                },
                "retrieval": article["retrieval"],
                "warnings": article["warnings"],
            }
        )

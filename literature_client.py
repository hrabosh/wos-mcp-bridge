"""Read-only literature orchestration and bounded, normalized article responses."""

from __future__ import annotations

import copy

from article_xml import validate_text
from europe_pmc import EuropePmcClient, identifier, safe_url
from literature_http import JsonObject, LiteratureError
from openalex_client import OpenAlexClient

MAX_TEXT_CHARS = 20_000


def _budget(max_chars: int) -> None:
    if type(max_chars) is not int or not 1 <= max_chars <= MAX_TEXT_CHARS:
        raise LiteratureError(f"max_chars must be an integer from 1 to {MAX_TEXT_CHARS}.")


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

    async def aclose(self) -> None:
        try:
            await self.europe_pmc.aclose()
        finally:
            await self.openalex.aclose()

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

    async def _resolve(self, value: str) -> JsonObject:
        kind, key = identifier(value)
        retrieval = []
        warnings = []
        try:
            found = await self.europe_pmc.resolve(value)
            article = found["article"]
            retrieval.append(found["retrieval"])
        except LiteratureError as exc:
            # A DOI/PMID can still be resolved by OpenAlex when Europe PMC is down.
            if kind == "pmcid":
                raise
            article = None
            warnings.append(str(exc))
        lookup = (article.get("doi") or article.get("pmid")) if article else None
        if not lookup and kind in {"doi", "pmid"}:
            lookup = key
        if lookup:
            try:
                oa = await self.openalex.resolve(lookup)
                if oa["retrieval"]:
                    retrieval.append(oa["retrieval"])
                supplement = oa["article"]
                if supplement:
                    if article is None:
                        article = supplement
                    else:
                        article["source"].append("OpenAlex")
                        article["oa_locations"].extend(supplement["oa_locations"])
                        for field in ("doi", "pmid", "license", "url"):
                            if article.get(field) is None:
                                article[field] = supplement[field]
                        if article["access"]["status"] != "open_access":
                            article["access"]["status"] = supplement["access"]["status"]
            except LiteratureError as exc:
                if article is None:
                    raise
                warnings.append(str(exc))
        if article is None and warnings:
            raise LiteratureError("Article resolution is incomplete: " + " ".join(warnings))
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
                "retrieval": retrieval,
            }
        article["warnings"].extend(warnings)
        article["retrieval"] = retrieval
        return article

    async def resolve(self, value: str, max_chars: int = 10_000) -> JsonObject:
        _budget(max_chars)
        return _bounded(await self._resolve(value), max_chars, include_text=False)

    async def _article(self, value: str) -> JsonObject:
        article = await self._resolve(value)
        if not article["access"]["full_text_xml_available"]:
            return article
        try:
            full = await self.europe_pmc.full_text(article["pmcid"])
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
        return article

    async def get_article(
        self, value: str, max_chars: int = 10_000, include_text: bool = True
    ) -> JsonObject:
        _budget(max_chars)
        if type(include_text) is not bool:
            raise LiteratureError("include_text must be a boolean.")
        return _bounded(await self._article(value), max_chars, include_text=include_text)

    async def get_section(
        self, value: str, section_id: str, offset: int = 0, max_chars: int = 10_000
    ) -> JsonObject:
        _budget(max_chars)
        section_id = validate_text(section_id, "section_id", 512)
        if type(offset) is not int or not 0 <= offset <= 2_147_483_647:
            raise LiteratureError("offset must be a nonnegative 32-bit integer.")
        article = await self._article(value)
        section = next((item for item in article["sections"] if item["id"] == section_id), None)
        if section is None:
            raise LiteratureError(
                "Section not found. Use get_article to inspect available section IDs "
                f"(content status: {article['access']['content_status']})."
            )
        if offset > section["total_chars"]:
            raise LiteratureError("offset exceeds the section length.")
        text = section["text"][offset : offset + max_chars]
        end = offset + len(text)
        return {
            "id": article["id"],
            "doi": article["doi"],
            "pmcid": article["pmcid"],
            "pmid": article["pmid"],
            "url": article["url"],
            "license": article["license"],
            "source": article["source"],
            "access": article["access"],
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

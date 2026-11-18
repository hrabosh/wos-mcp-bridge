"""Europe PMC search, exact identifier resolution, abstracts and OA JATS XML."""

from __future__ import annotations

import re
from typing import Unpack
from urllib.parse import urlsplit

from .article_xml import extract_jats, plain_text, validate_text
from .literature_http import ApiClient, ApiOptions, JsonObject, LiteratureError


def identifier(value: str) -> tuple[str, str]:
    value = validate_text(value, "identifier", 512)
    value = re.sub(r"^(?:https://(?:dx\.)?doi\.org/|doi:)", "", value, flags=re.I)
    if re.fullmatch(r"10\.\d{4,9}/[^\s\"<>\\]+", value, flags=re.I):
        return "doi", value.lower()
    if re.fullmatch(r"PMC[1-9]\d*", value, flags=re.I):
        return "pmcid", value.upper()
    value = re.sub(r"^PMID:", "", value, flags=re.I)
    if re.fullmatch(r"[1-9]\d{0,11}", value):
        return "pmid", value
    raise LiteratureError("Use a DOI, PMID (digits or PMID:digits), or PMCID (PMCdigits).")


def safe_url(candidate: object) -> str | None:
    """External public links are returned as metadata only, never downloaded."""
    if isinstance(candidate, str) and not re.search(r"[\x00-\x20\x7f]", candidate):
        try:
            parsed = urlsplit(candidate)
            if (
                parsed.scheme in {"https", "http"}
                and parsed.hostname
                and not parsed.username
                and not parsed.password
                and parsed.port in {None, 80, 443}
            ):
                return candidate
        except ValueError:
            pass
    return None


def _optional_id(value: object, kind: str) -> str | None:
    try:
        found_kind, found = identifier(value)
        return found if found_kind == kind else None
    except LiteratureError:
        return None


def normalise(hit: object) -> JsonObject:
    if not isinstance(hit, dict) or not isinstance(hit.get("title"), str):
        raise LiteratureError("Europe PMC returned malformed article metadata.")
    title = plain_text(hit["title"])
    if not title:
        raise LiteratureError("Europe PMC returned an article without a valid title.")
    doi = _optional_id(hit.get("doi"), "doi")
    pmcid = _optional_id(hit.get("pmcid"), "pmcid")
    pmid = _optional_id(hit.get("id"), "pmid") if hit.get("source") == "MED" else None
    if not any((doi, pmcid, pmid)):
        # Europe PMC also indexes patents etc.; retain their source-qualified record IDs.
        source, record_id = hit.get("source"), hit.get("id")
        if not isinstance(source, str) or not isinstance(record_id, str):
            raise LiteratureError("Europe PMC returned an article without an identifier.")
        record_key = f"{source}:{record_id}"
    else:
        record_key = pmcid or (f"PMID:{pmid}" if pmid else f"DOI:{doi}")
    abstract = (
        plain_text(hit.get("abstractText")) if isinstance(hit.get("abstractText"), str) else None
    )
    oa = hit.get("isOpenAccess") == "Y"
    url = (
        f"https://europepmc.org/articles/{pmcid}"
        if pmcid
        else f"https://europepmc.org/article/MED/{pmid}"
        if pmid
        else f"https://doi.org/{doi}"
        if doi
        else None
    )
    links = hit.get("fullTextUrlList") or {}
    links = links.get("fullTextUrl", []) if isinstance(links, dict) else []
    locations = []
    if isinstance(links, list):
        for link in links:
            if isinstance(link, dict) and link.get("availability") == "Open access":
                if link_url := safe_url(link.get("url")):
                    locations.append({"url": link_url, "license": None, "source": "Europe PMC"})
    license_value = hit.get("license") if isinstance(hit.get("license"), str) else None
    return {
        "id": record_key,
        "title": title,
        "doi": doi,
        "pmid": pmid,
        "pmcid": pmcid,
        "url": url,
        "license": license_value,
        "source": ["Europe PMC"],
        "authors": hit.get("authorString"),
        "year": hit.get("pubYear"),
        "abstract": abstract,
        "access": {
            "status": "open_access" if oa or locations else "unknown",
            "full_text_xml_available": bool(oa and pmcid),
            "content_status": "abstract_only" if abstract else "metadata_only",
        },
        "oa_locations": locations,
        "sections": [],
        "warnings": [],
    }


class EuropePmcClient:
    """Europe PMC endpoint validation composed with a fixed-host transport."""

    def __init__(self, **options: Unpack[ApiOptions]) -> None:
        self._api = ApiClient("Europe PMC", **options)

    async def aclose(self) -> None:
        await self._api.aclose()

    async def search(self, query: str, limit: int = 10, cursor: str = "*") -> JsonObject:
        query = validate_text(query, "query", 4000)
        cursor = validate_text(cursor, "cursor", 2048)
        if type(limit) is not int or not 1 <= limit <= 50:
            raise LiteratureError("limit must be an integer from 1 to 50.")
        data, retrieval = await self._api.get_json(
            "search",
            {
                "query": query,
                "format": "json",
                "resultType": "core",
                "pageSize": limit,
                "cursorMark": cursor,
            },
            parse=lambda data: self._parse_search(data, limit),
        )
        return {"query": query, "cursor": cursor, **data, "retrieval": retrieval}

    @staticmethod
    def _parse_search(data: JsonObject, limit: int) -> JsonObject:
        results = data.get("resultList")
        if (
            not isinstance(results, dict)
            or not isinstance(results.get("result"), list)
            or type(data.get("hitCount")) is not int
            or data["hitCount"] < 0
        ):
            raise LiteratureError("Europe PMC returned malformed search results.")
        next_cursor = data.get("nextCursorMark")
        return {
            "total": data["hitCount"],
            "next_cursor": next_cursor if isinstance(next_cursor, str) else None,
            "results": [normalise(hit) for hit in results["result"][:limit]],
        }

    async def resolve(self, value: str) -> JsonObject:
        kind, key = identifier(value)
        query = {
            "doi": f'DOI:"{key}"',
            "pmid": f"EXT_ID:{key} AND SRC:MED",
            "pmcid": f"PMCID:{key}",
        }[kind]
        result = await self.search(query, limit=10)
        matches = [record for record in result["results"] if record[kind] == key]
        preferred = (
            max(
                matches,
                key=lambda record: (
                    record["access"]["full_text_xml_available"],
                    bool(record["abstract"]),
                ),
            )
            if matches
            else None
        )
        return {"article": preferred, "retrieval": result["retrieval"]}

    async def full_text(self, pmcid: str) -> JsonObject:
        kind, key = identifier(pmcid)
        if kind != "pmcid":
            raise LiteratureError("Full text XML requires a PMCID.")
        data, retrieval = await self._api.get(
            f"{key}/fullTextXML", parse=lambda raw: extract_jats(raw, expected_pmcid=key)
        )
        return {**data, "retrieval": retrieval}

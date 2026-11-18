"""OpenAlex OA discovery. Publisher/repository links are metadata, never fetch targets."""

from __future__ import annotations

from typing import Unpack
from urllib.parse import quote

from pydantic import ValidationError

from .article_xml import plain_text
from .europe_pmc import _optional_id, identifier, safe_url
from .http_support import CacheExpiry
from .literature_http import ApiClient, ApiOptions, JsonObject, LiteratureError
from .models import ArticleRecord, Bibliography


class OpenAlexClient:
    """OpenAlex identity and access validation with a fixed-host transport."""

    def __init__(self, api_key: str = "", **options: Unpack[ApiOptions]) -> None:
        self._api = ApiClient("OpenAlex", api_key=api_key, **options)

    async def aclose(self) -> None:
        await self._api.aclose()

    async def resolve(
        self, value: str, *, source_expiries: list[CacheExpiry | None] | None = None
    ) -> JsonObject:
        kind, key = identifier(value)
        if kind not in {"doi", "pmid"}:
            raise LiteratureError(
                "OpenAlex lookup requires a DOI or PMID.", code="invalid_input", source="OpenAlex"
            )
        # Encode slash/colon so even adversarial DOI suffixes cannot alter host or query.
        path = "works/" + quote(
            f"https://doi.org/{key}" if kind == "doi" else f"pmid:{key}", safe=""
        )
        try:
            data, retrieval = await self._api.get_json(
                path, parse=lambda data: self._parse_article(data, kind, key)
            )
        except LiteratureError as exc:
            if exc.status == 404:
                if source_expiries is not None:
                    source_expiries.append(None)
                return {"article": None, "retrieval": None}
            raise
        if source_expiries is not None:
            source_expiries.append(self._api.cache_expiry(path))
        return {"article": data, "retrieval": retrieval}

    @staticmethod
    def _parse_article(data: JsonObject, kind: str, key: str) -> JsonObject:
        ids = data.get("ids") or {}
        if not isinstance(ids, dict) or not isinstance(data.get("title"), str):
            raise LiteratureError("OpenAlex returned malformed article metadata.")
        title = plain_text(data["title"])
        if not title:
            raise LiteratureError("OpenAlex returned an article without a valid title.")
        doi = _optional_id(data.get("doi") or ids.get("doi"), "doi")
        pmid_url = ids.get("pmid")
        pmid = (
            _optional_id(pmid_url.rstrip("/").rsplit("/", 1)[-1], "pmid")
            if isinstance(pmid_url, str)
            else None
        )
        if (doi if kind == "doi" else pmid) != key:
            raise LiteratureError(
                "OpenAlex returned a different article identifier than requested."
            )
        locations = data.get("locations") or []
        best = data.get("best_oa_location")
        if not isinstance(locations, list) or (best is not None and not isinstance(best, dict)):
            raise LiteratureError("OpenAlex returned malformed OA locations.")
        oa_locations = []
        seen = set()
        for location in ([best] if best else []) + locations:
            if not isinstance(location, dict) or location.get("is_oa") is not True:
                continue
            landing = safe_url(location.get("landing_page_url"))
            pdf = safe_url(location.get("pdf_url"))
            url = pdf or landing
            if not url or url in seen:
                continue
            seen.add(url)
            oa_locations.append(
                {
                    "url": url,
                    "landing_page_url": landing,
                    "pdf_url": pdf,
                    "license": location.get("license"),
                    "version": location.get("version"),
                    "source": "OpenAlex",
                }
            )
        access = data.get("open_access") or {}
        if not isinstance(access, dict):
            raise LiteratureError("OpenAlex returned malformed access metadata.")
        status = (
            "open_access"
            if oa_locations or access.get("is_oa") is True
            else "closed"
            if access.get("is_oa") is False
            else "unknown"
        )
        article = {
            "id": f"DOI:{doi}" if doi else f"PMID:{pmid}",
            "title": title,
            "doi": doi,
            "pmid": pmid,
            "pmcid": None,
            "url": oa_locations[0]["url"] if oa_locations else safe_url(data.get("doi")),
            "license": oa_locations[0]["license"] if oa_locations else None,
            "year": data.get("publication_year"),
            "authors": None,
            "source": ["OpenAlex"],
            "abstract": None,
            "access": {
                "status": status,
                "full_text_xml_available": False,
                "content_status": "metadata_only",
            },
            "oa_locations": oa_locations,
            "sections": [],
            "warnings": [],
        }

        try:
            primary = data.get("primary_location")
            if primary is not None and not isinstance(primary, dict):
                raise LiteratureError("OpenAlex returned malformed source metadata.")
            source = primary.get("source") if primary is not None else None
            if source is not None and not isinstance(source, dict):
                raise LiteratureError("OpenAlex returned malformed source metadata.")
            journal = (
                source.get("display_name") if source and source.get("type") == "journal" else None
            )
            article["bibliography"] = Bibliography.from_metadata(
                authors=_author_names(data.get("authorships")),
                year=article["year"],
                journal=journal,
            ).model_dump(mode="json")
            return ArticleRecord.model_validate(article).model_dump(mode="json", exclude_unset=True)
        except ValidationError:
            raise LiteratureError("OpenAlex returned malformed bibliographic metadata.") from None


def _author_names(value: object) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise LiteratureError("OpenAlex returned malformed author metadata.")
    names: list[str] = []
    complete = True
    for authorship in value:
        if not isinstance(authorship, dict):
            raise LiteratureError("OpenAlex returned malformed author metadata.")
        author = authorship.get("author")
        if author is None:
            complete = False
            continue
        if not isinstance(author, dict):
            raise LiteratureError("OpenAlex returned malformed author metadata.")
        name = author.get("display_name")
        if name is None:
            complete = False
            continue
        if not isinstance(name, str):
            raise LiteratureError("OpenAlex returned malformed author metadata.")
        names.append(name)
    return names if complete else None

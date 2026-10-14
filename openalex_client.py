"""OpenAlex OA discovery. Publisher/repository links are metadata, never fetch targets."""

from __future__ import annotations

from urllib.parse import quote

from europe_pmc import _optional_id, identifier, safe_url
from literature_http import ApiClient, JsonObject, LiteratureError


class OpenAlexClient(ApiClient):
    def __init__(self, api_key: str = "", **kwargs) -> None:
        super().__init__("OpenAlex", api_key=api_key, **kwargs)

    async def resolve(self, value: str) -> JsonObject:
        kind, key = identifier(value)
        if kind not in {"doi", "pmid"}:
            raise LiteratureError("OpenAlex lookup requires a DOI or PMID.")
        # Encode slash/colon so even adversarial DOI suffixes cannot alter host or query.
        path = "works/" + quote(
            f"https://doi.org/{key}" if kind == "doi" else f"pmid:{key}", safe=""
        )
        try:
            data, retrieval = await self._json(path)
        except LiteratureError as exc:
            if exc.status == 404:
                return {"article": None, "retrieval": None}
            raise
        ids = data.get("ids") or {}
        if not isinstance(ids, dict) or not isinstance(data.get("title"), str):
            raise LiteratureError("OpenAlex returned malformed article metadata.")
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
        return {
            "article": {
                "id": f"DOI:{doi}" if doi else f"PMID:{pmid}",
                "title": data["title"],
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
            },
            "retrieval": retrieval,
        }

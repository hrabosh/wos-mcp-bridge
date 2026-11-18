"""Concrete wire contracts shared by provider validation and MCP discovery."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator


def _nonblank(value: str) -> str:
    if not value.strip():
        raise ValueError("Expected nonblank text.")
    return value


def _http_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.port not in {None, 80, 443}
        or not all(char.isprintable() and char != " " for char in value)
        or re.search(r'[<>"{}|\\^`]', value)
        or re.search(r"%(?![0-9A-Fa-f]{2})", value)
        or any(
            char in part for part in (parsed.path, parsed.query, parsed.fragment) for char in "[]"
        )
        or "#" in parsed.fragment
        or ("[" in parsed.netloc and "%" in parsed.netloc)
    ):
        raise ValueError("Expected an HTTP(S) link without credentials.")
    return value


def _timestamp(value: str) -> str:
    if datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is None:
        raise ValueError("Expected a timestamp with a time zone.")
    return value


Nonblank = Annotated[str, Field(min_length=1), AfterValidator(_nonblank)]
HttpLink = Annotated[str, Field(json_schema_extra={"format": "iri"}), AfterValidator(_http_url)]
Timestamp = Annotated[
    str,
    Field(
        pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])$",
        json_schema_extra={"format": "date-time"},
    ),
    AfterValidator(_timestamp),
]
Nonnegative = Annotated[int, Field(ge=0)]
Year = Annotated[int, Field(ge=1, le=9999)]
LegacyYear = Year | str
SortOrder = Literal["RS+D", "RS+A", "PY+D", "PY+A", "LD+D", "LD+A", "TC+D", "TC+A"]
LiteratureSource = Literal["Europe PMC", "OpenAlex"]
TOKEN_PATTERN = r"^[^\s\x00-\x1f\x7f](?:[^\x00-\x1f\x7f]*[^\s\x00-\x1f\x7f])?$"
CursorToken = Annotated[
    str, Field(strict=True, min_length=1, max_length=2048, pattern=TOKEN_PATTERN)
]
SectionKey = Annotated[str, Field(strict=True, min_length=1, max_length=512, pattern=TOKEN_PATTERN)]


class OutputModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", allow_inf_nan=False)


class Bibliography(OutputModel):
    """Additive common metadata; null means the authoritative value is unavailable."""

    authors: list[Nonblank] | None
    year: Year | None
    journal: Nonblank | None

    @classmethod
    def from_metadata(
        cls, *, authors: list[str] | None, year: int | str | None, journal: str | None
    ) -> Bibliography:
        numeric_year = None
        if type(year) is int and 1 <= year <= 9999:
            numeric_year = year
        elif isinstance(year, str) and re.fullmatch(r"[0-9]{4}", year) and int(year) > 0:
            numeric_year = int(year)
        if isinstance(journal, str) and not journal.strip():
            journal = None
        return cls(authors=authors, year=numeric_year, journal=journal)


class WosRetrieval(OutputModel):
    cached: bool
    fetched_at_utc: Timestamp
    quota_at_fetch: dict[str, str]


class LiteratureRetrieval(OutputModel):
    source: LiteratureSource
    cached: bool
    fetched_at_utc: Timestamp


class Pages(OutputModel):
    range: str | None = None
    begin: str | None = None
    end: str | None = None
    count: Nonnegative | None = None


class SearchHit(OutputModel):
    id: Nonblank
    title: Nonblank
    url: HttpLink


class WosRecord(SearchHit):
    authors: list[Nonblank]
    journal: str | None
    year: LegacyYear | None
    volume: str | None
    issue: str | None
    pages: Pages | str | None
    doi: str | None
    pmid: str | None
    types: list[str]
    keywords: list[str]
    citations: dict[str, Nonnegative] | None
    bibliography: Bibliography


class SearchResponse(OutputModel):
    results: list[SearchHit]


class WosSearchMetadata(OutputModel):
    total: Nonnegative
    page: Annotated[int, Field(ge=1, le=2_147_483_647)]
    limit: Annotated[int, Field(ge=1, le=50)]


class WosSearchResponse(OutputModel):
    query: Nonblank
    database: Literal["WOS"]
    sort: SortOrder
    metadata: WosSearchMetadata
    results: list[WosRecord]
    retrieval: WosRetrieval
    limitations: str


class FetchMetadata(WosRecord):
    retrieval: WosRetrieval
    limitations: str


class FetchResponse(SearchHit):
    text: str
    metadata: FetchMetadata


class Access(OutputModel):
    status: Literal["open_access", "closed", "unknown"]
    full_text_xml_available: bool
    content_status: Literal["metadata_only", "abstract_only", "full_text", "not_found"]


class OALocation(OutputModel):
    url: HttpLink
    license: str | None
    source: LiteratureSource
    landing_page_url: HttpLink | None = None
    pdf_url: HttpLink | None = None
    version: str | None = None


class SectionDirectory(OutputModel):
    id: SectionKey
    original_id: str | None
    heading: str | None
    parent_id: str | None
    kind: Literal["abstract", "sec", "ref-list", "ack", "app", "body"]
    total_chars: Nonnegative


class RawSection(SectionDirectory):
    text: str


class JatsContent(OutputModel):
    sections: list[RawSection]
    license: str | None
    license_url: HttpLink | None


class SectionText(RawSection):
    truncated: bool
    next_offset: Nonnegative | None


class SectionSlice(SectionText):
    offset: Nonnegative


class TextBudget(OutputModel):
    max_chars: Annotated[int, Field(ge=0, le=20_000)]
    returned_chars: Nonnegative
    total_chars: Nonnegative
    truncated: bool
    section_text_included: bool

    @model_validator(mode="after")
    def consistent_counts(self) -> TextBudget:
        if self.returned_chars > self.max_chars or self.returned_chars > self.total_chars:
            raise ValueError("Text budget counts are inconsistent.")
        if self.truncated != (self.returned_chars < self.total_chars):
            raise ValueError("Text budget truncation is inconsistent.")
        return self


class ArticleMetadata(OutputModel):
    id: Nonblank
    title: Nonblank | None
    doi: str | None
    pmid: str | None
    pmcid: str | None
    url: HttpLink | None
    license: str | None
    source: list[LiteratureSource]
    abstract: str | None
    access: Access
    oa_locations: list[OALocation]
    warnings: list[str]
    bibliography: Bibliography
    # These legacy fields are absent in some existing not-found responses.
    authors: str | None = None
    year: LegacyYear | None = None
    license_url: HttpLink | None = None


class ArticleRecord(ArticleMetadata):
    sections: list[RawSection]


class LiteraturePage(OutputModel):
    total: Nonnegative
    next_cursor: CursorToken | None
    results: list[ArticleRecord]


class BoundedArticle(ArticleMetadata):
    sections: list[SectionText | SectionDirectory]
    text_budget: TextBudget
    abstract_total_chars: Nonnegative | None = None
    abstract_truncated: bool | None = None


class ArticleResponse(BoundedArticle):
    retrieval: list[LiteratureRetrieval]


class LiteratureSearchResponse(OutputModel):
    query: Nonblank
    total: Nonnegative
    cursor: CursorToken
    next_cursor: CursorToken | None
    results: list[BoundedArticle]
    retrieval: LiteratureRetrieval
    returned_count: Nonnegative
    max_chars: Annotated[int, Field(ge=1, le=20_000)]
    limitations: str


class SectionResponse(OutputModel):
    id: Nonblank
    doi: str | None
    pmcid: str | None
    pmid: str | None
    url: HttpLink | None
    license: str | None
    source: list[LiteratureSource]
    access: Access
    section: SectionSlice
    retrieval: list[LiteratureRetrieval]
    warnings: list[str]
    bibliography: Bibliography

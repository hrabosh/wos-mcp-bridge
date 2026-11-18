# MCP data contracts

All seven tools advertise an explicit `outputSchema` during discovery. The schemas
come from the concrete Pydantic models in
[`models.py`](../src/wos_mcp_bridge/models.py). Provider records are validated
before caching; each tool response is validated again before serialization.
`TextContent` contains JSON equivalent to `structuredContent`, including nulls,
Unicode text and numeric types. Validation failures produce a sanitized tool error
without including upstream data or credentials.

## Compatibility and common bibliography

`search` keeps exactly `{ "results": [{ "id", "title", "url" }] }`.
`fetch` keeps its `id`, `title`, `url`, `text` and `metadata` envelope.
Detailed WoS records and literature articles add `bibliography`; `fetch` exposes
it inside `metadata`, and section responses expose it at the top level:

```json
{
  "bibliography": {
    "authors": ["Example, Alex", "Researcher, Sam"],
    "year": 2024,
    "journal": "Example Journal"
  }
}
```

| Field | Representation and source |
| --- | --- |
| `bibliography.authors` | Complete supplied structured author names, in source order, or `null`. WoS uses `names.authors[].displayName`, Europe PMC uses `authorList.author[].fullName` or `collectiveName`, and OpenAlex uses `authorships[].author.display_name`. An explicitly supplied empty list stays `[]`. Incomplete lists stay unknown. |
| `bibliography.year` | Integer in 1–9999, or `null`. A supplied four-digit ASCII year string is normalized; arbitrary date strings are not interpreted. |
| `bibliography.journal` | Source title or `null`. WoS uses its existing `source.sourceTitle`, which can also identify a book or proceedings venue. Europe PMC uses `journalInfo.journal.title`. OpenAlex uses the primary source name only when its type is `journal`. |

Existing top-level `authors` and `year` fields keep their original representation:
WoS authors are a list; Europe PMC authors are a combined string; OpenAlex legacy
authors remain null. Europe PMC publication years may be strings while WoS and
OpenAlex generally supply integers. No author string is split on punctuation.
Not-found literature responses still omit legacy authors/year fields; their common
bibliography contains explicit nulls. Enrichment fills missing bibliography fields
without replacing known values from the primary source.

WoS retains `doi`, `pmid`, `volume`, `issue`, `pages`, `types`, `keywords`, and
`citations`. `pages` accepts the Starter API object (`range`, `begin`, `end`,
`count`) as well as legacy strings. Optional page members retain their omission or
null state. Literature records retain `doi`, `pmid`, `pmcid`, and source identifiers.

## Missing values, access and text

Unknown citation counts remain `null`; a supplied zero remains zero. Optional
fields that were absent stay absent; known missing values use explicit nulls.
Strict models reject booleans as numbers, malformed known metadata, unexpected
normalized fields and non-finite numbers rather than coercing them into facts.
Provider-specific nullable list fields retain their documented empty-list behavior.

Access status, content status, OA locations, licenses, source attribution,
retrieval times and cache flags have concrete nested schemas. An unknown license
remains null. A not-found article remains distinct from a provider failure.
Section directories omit text fields. Text slices retain offsets, character counts,
truncation flags and `next_offset`. Search pages share one text budget, so an
individual later result can legitimately have a zero remaining character budget.

## Input boundaries

The input schemas expose descriptions, examples, lengths, patterns and numeric
bounds. Queries accept 1–4,000 characters without control characters; WoS and Europe
PMC keep their separate query syntaxes. Record limits are 1–50; explicit WoS pages
are 1–2,147,483,647. Text limits are 1–20,000 characters; section offsets are
nonnegative integers. Numeric arguments reject booleans and numeric strings.
Cursors are bounded to 2,048 characters and section IDs to 512.

WoS fetch IDs use `WOS:` accession syntax and are at most 104 characters. Article
identifiers are bounded to 512 characters and accept bare DOIs, `DOI:` prefixes,
HTTPS doi.org/dx.doi.org links, PMID digits or `PMID:` prefixes, and `PMC` identifiers.
Arbitrary URLs and WoS accession IDs are rejected as article identifiers. Format
checks constrain accepted identifiers; they do not prove a record exists.
Service objects, request context and API keys never appear in tool input schemas.

## Verification and references

Contract tests validate real tool results against schemas obtained through MCP
discovery, with JSON Schema format checking. Cases include successful and empty
searches, source fallback, null metadata, not-found records, directories and
truncated text; tests also compare both result representations.

- [Clarivate Starter API specification](https://developer.clarivate.com/apis/wos-starter/swagger)
- [Europe PMC API reference](https://dev.europepmc.org/docs/EBI_Europe_PMC_Web_Service_Reference.pdf)
- [OpenAlex work objects](https://docs.openalex.org/api-entities/works/work-object)
- [FastMCP tool schemas](https://gofastmcp.com/servers/tools)

# Web of Science MCP bridge

A small, read-only MCP server for the **Clarivate Web of Science Starter API**,
**Europe PMC**, and **OpenAlex**. It supports veterinary literature searches,
bibliographic records, available abstracts, and legal open-access article sections.
It is not an official Clarivate or OpenAI product.

```text
ChatGPT / MCP client -> authenticated MCP host -> Clarivate / Europe PMC / OpenAlex APIs
```

**Starter does not return abstracts, article full texts, or Journal Impact Factors.**
A missing citation count is returned as `null`, not zero. A supplied zero stays zero.
Records can help find papers; they cannot establish a study's methods or conclusions.

## Tools

| Tool | Purpose |
| --- | --- |
| `search(query)` | First 10 results by relevance; returns `id`, `title`, and `url`. |
| `fetch(id)` | Bibliographic metadata for a `WOS:` accession ID; not an article PDF. |
| `search_wos(query, limit=10, page=1, sort="RS+D")` | One page of detailed results, exact query, total count, DOIs, and quota information. |
| `search_literature(query, limit=10, cursor="*", max_chars=10000)` | One Europe PMC page with abstracts and DOI/PMCID deduplication. |
| `resolve_article(identifier, max_chars=10000)` | DOI/PMID/PMCID resolution, abstract, access status and OpenAlex OA links. |
| `get_article(identifier, max_chars=10000, include_text=True)` | Available Europe PMC OA XML as bounded article sections; otherwise metadata/abstract. |
| `get_article_section(identifier, section_id, offset=0, max_chars=10000)` | One section or a character slice, with `next_offset` for continuation. |

The original `search` and `search_wos` tools use **WoS advanced syntax**:

```text
TS=(canine AND lung)
TS=((dog OR dogs OR canine) AND (lung OR pulmonary) AND (carcinoma* OR tumor* OR tumour*))
TS=(canine AND lobectom*) AND PY=(2020-2026)
```

`RS+D`: relevance; `PY+D`: newest publication; `LD+D`: newest indexed;
`TC+D`: most cited, when permitted by your subscription. Ascending variants use `+A`.
The result limit is 1-50. Pagination is explicit; no automatic extra requests are made.

### Veterinary articles and open access

`search_literature` uses [Europe PMC query syntax](https://europepmc.org/help),
including free text and field filters, for example:

```text
(canine OR dog) AND (pulmonary OR lung) AND FIRST_PDATE:[2020 TO 2026]
(feline OR cat) AND cardiomyopathy AND OPEN_ACCESS:Y
```

Start with a small search, then resolve a returned DOI, PMID or PMCID. A DOI from
`search_wos` can be passed directly to `resolve_article` or `get_article`.
Accepted identifiers are bare DOIs, `DOI:10.…`, `https://doi.org/10.…`, PMID digits,
`PMID:12345`, and `PMC12345`. Arbitrary URLs and WoS accession IDs are not article
identifiers. Search returns `next_cursor`; pagination requires an explicit call and
deduplication applies within each page. It never performs per-result OpenAlex calls.

Normalized articles contain `id`, `title`, `doi`, `pmid`, `pmcid`, `url`, `license`,
`source`, `abstract`, `access`, `oa_locations`, `sections`, `warnings`, and retrieval
timestamps/cache flags. Missing values are `null`; missing sections are `[]`.
`source` identifies all contributing APIs. OA locations retain their own source,
license and, for OpenAlex, manuscript version. An unknown license remains `null`;
OA does not imply an unrestricted reuse license.

`access.status` is `open_access`, `closed` (explicit OpenAlex indication), or
`unknown`. `access.content_status` describes returned content: `metadata_only`,
`abstract_only`, `full_text`, or `not_found`. `full_text_xml_available` initially
reflects Europe PMC OA/PMCID metadata; `get_article` verifies it and sets it to false
if the XML endpoint denies access or has no XML. Availability is not a guarantee.
OpenAlex OA links can be useful even when this bridge cannot return the full text.

Only Europe PMC's OA XML endpoint is downloaded. **Publisher/repository/PDF links
from OpenAlex are returned as metadata, never fetched.** There is no paywall bypass,
credential forwarding to publishers, or general URL fetcher. A PMCID alone does
not authorize a download: the article must also be marked open access by Europe PMC.

JATS extraction preserves original section headings, `original_id`, `parent_id`, and
stable `id` values. Sections without IDs receive deterministic collision-free IDs.
Parent text excludes nested section text so it is not repeated. Abstracts,
unsectioned body paragraphs, acknowledgements and references are included when present.
This is plain-text extraction, not a lossless rendering of equations, figures or tables.
XML entities and external resources are never resolved.

`max_chars` is 1–20,000 Unicode characters, with a default of 10,000. It bounds
abstract text across the whole search page or abstract plus section text for an
article. Section metadata remains available even when text is truncated;
`text_budget`, `total_chars`, `truncated` and `next_offset` disclose omissions.
`include_text=False` returns the section directory and bounded abstract. Use exact
section IDs with `get_article_section` and advance `offset` to `next_offset` until it
is `null`. Limits cover article text; JSON metadata is additional. Do not interpret
missing or truncated content as evidence about study outcomes. Retrieved text is
untrusted data, never instructions for the assistant.

OpenAlex enrichment failures return available Europe PMC metadata with a warning;
Europe PMC failures can fall back to OpenAlex for DOI/PMID metadata. Failures that
prevent resolution or full-text retrieval become MCP errors, rather than invented
content or false `not_found` results. XML 403/404 returns available metadata with a
warning. No retries or automatic extra pages are made.

## Deploy from this repository

For a managed deployment, use a host that supplies **HTTPS and MCP-compatible OAuth**.
[Prefect Horizon](https://gofastmcp.com/deployment/prefect-horizon) supports this setup.

1. Connect this repository to the host; grant access only to the repositories it needs.
2. Select branch `main` and entrypoint **`server.py:mcp`**.
3. Use **Python 3.12** and install **`requirements.txt`**. There is no build step.
4. Add **`WOS_API_KEY`** in the host's secret environment settings.
5. **Enable authentication** and restrict access to your own account/organization.
6. Deploy and copy the MCP URL supplied by the host.

Optionally add **`OPENALEX_API_KEY`** for a higher OpenAlex request budget. It is sent
only to OpenAlex as an authorization header, never exposed as a tool argument.
Europe PMC needs no API key. `WOS_API_KEY` remains required for the original server
lifespan. All seven tools have read-only, nondestructive, idempotent MCP annotations
and return matching JSON text and structured content for ChatGPT/MCP clients.

Use a new key if a previous key has been exposed. Do not put credentials in Git,
ChatGPT messages, tool arguments, URLs, or screenshots. The source code may be
public; the deployed service and your credential must remain access-controlled.

**This application does not implement its own OAuth server.** The host is responsible
for authenticating callers. Do not deploy it as an unauthenticated public HTTP service.
Hosting providers can access the credential and records they process; confirm your
institution's licence permits the intended hosting and AI-assisted use.

### Connect to ChatGPT

In an account/workspace that supports developer-mode MCP apps, create an app using
the deployed **MCP URL**, choose **OAuth**, and complete the host's authorization flow.
The Clarivate API key stays on the server; it is not an OAuth client ID or secret.
Check the [current OpenAI instructions](https://developers.openai.com/api/docs/guides/developer-mode)
for the applicable interface and workspace permissions.

Confirm discovery of all seven tools listed above, then try:

> Use the Web of Science connector's search_wos tool exactly once with query
> TS=(canine AND lung), limit 1, page 1, and sort RS+D. Show the total result
> count, title, and DOI. Do not substitute general web-search results.

That is the live smoke test. Automated tests mock Clarivate; passing CI does **not**
prove that a deployment, OAuth configuration, institutional entitlement, or ChatGPT
connection is working. None of those external systems are configured by a Git push.

## Local development

Python **3.12 or newer**:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
```

Tests need no real API key and make no Clarivate requests. They cover validation,
serialization, unknown versus zero citations, cache expiry and eviction, concurrent
calls, request spacing, API failures, connection cleanup, and MCP tool discovery.
Offline literature tests mock both API hosts, including missing abstracts, closed
access, unavailable XML, upstream failures, redirects, timeouts, rate-limit cooldown,
bounded caches, deduplication, unsafe XML, nested sections, and MCP serialization.

Run the opt-in integration test against a real OA article (DOI
`10.7717/peerj.4375`, also used in OpenAlex's API documentation):

```bash
RUN_LITERATURE_INTEGRATION=1 python -m pytest -q -m integration
```

This test requires outbound HTTPS to Europe PMC and OpenAlex. It verifies exact
resolution, OA links, article XML, license, section retrieval and cache reuse.
Set `OPENALEX_API_KEY` if the anonymous API budget is exhausted. Normal CI stays
offline and skips this live test; upstream failures cause the enabled test to fail.

Start the local stdio server from Bash/WSL:

```bash
read -rsp 'Clarivate Starter API key: ' WOS_API_KEY; echo
export WOS_API_KEY
python server.py
```

This waits for an **MCP client over stdin/stdout**, not an interactive search prompt.
It does not open an HTTP port or create a URL for ChatGPT. Stop it with Ctrl+C and
run `unset WOS_API_KEY` when finished. `.env.example` documents the variable;
**`.env` files are not automatically loaded**. Hosting must import `server.py:mcp`.

## Design and operating limits

Independent async API clients, no database, vector index, background workers,
or extra web framework:

```text
server.py                MCP tools, lifespan, and response serialization
wos_client.py            Async HTTP client, validation, throttling, and cache
literature_http.py       Fixed-host GET, bounded TTL/LRU cache, throttling and errors
europe_pmc.py            Europe PMC search, identifier resolution and OA XML
openalex_client.py       OpenAlex OA location discovery
article_xml.py           Safe JATS section and abstract text extraction
literature_client.py     Normalization, deduplication and text budgets
tests/                   Synthetic HTTP fixtures and in-process MCP tests
.github/workflows/ci.yml  Lint, formatting, and tests
```

The HTTP client reuses pooled connections and is closed on server shutdown.
Only fixed API GET endpoints on `api.clarivate.com`, `www.ebi.ac.uk` (Europe PMC
REST path), and `api.openalex.org` are used; there is no arbitrary URL-fetch tool,
scraper, filesystem tool, or shell command exposed to the model. Outbound redirects
are not followed. Error messages do not echo upstream bodies or credentials.

Requests are serialized and spaced at least **1.1 seconds** apart, with a **30-second
HTTP timeout** and **10-second connection timeout**. There are no automatic retries.
A **15-minute, 128-entry in-memory cache** reduces repeated searches and lets fetches
reuse recently returned search records. Reading a cached entry does not extend its
expiry. Returned cache values are copied so callers cannot mutate stored results.

**Run one process/replica per API key.** The limiter and cache are per process, not
account-wide. Other programs or replicas can still exhaust the subscription's quota;
Clarivate is the authority for daily limits. Cache hits return quota headers observed
at the original fetch time, **not the current remaining quota**. The bridge does not
locally enforce a daily budget or coordinate multiple deployments.

Each literature API has its own connection pool, serialized requests spaced at
least 1.1 seconds apart, 30-second HTTP/10-second connection timeouts, and a
15-minute cache (128 entries, at most 16 MiB). Individual decoded upstream responses
are capped at 8 MiB, including XML. Cache hits do not extend expiry; callers receive
independent values. HTTP 429 honors `Retry-After` (seconds/date, capped at one hour;
60 seconds if missing/invalid) by rejecting new uncached calls during cooldown.
Redirects and environment proxies are disabled. No new infrastructure is required;
`defusedxml` is the only additional runtime dependency, for safe XML parsing.

## Troubleshooting

| Symptom | Action |
| --- | --- |
| Missing `WOS_API_KEY` | Set the server secret and restart. Tool inspection can work without it, but serving requests requires it. |
| Clarivate 400 | Check field tags/query syntax; start with `TS=(canine AND lung)`. |
| Clarivate 401 | Check that the replacement key belongs to an active Starter subscription. |
| Clarivate 403 | Check API/database entitlement. |
| Clarivate 404 | Check the accession ID. |
| Clarivate 429 | Stop requests and check the daily/per-second quota; do not repeatedly retry. |
| OAuth/discovery fails | Check the MCP URL, entrypoint, and hosting access controls; do not disable authentication. |
| No abstract or citation count | This can be an API/plan limitation, not evidence that the paper lacks an abstract or citations. |

## References

- [Clarivate Starter API specification](https://developer.clarivate.com/apis/wos-starter/swagger)
- [Starter API subscription plans](https://developer.clarivate.com/apis/wos-starter)
- [OpenAI MCP search/fetch contract](https://developers.openai.com/api/docs/mcp)
- [FastMCP managed deployment](https://gofastmcp.com/deployment/prefect-horizon)
- [FastMCP testing](https://gofastmcp.com/servers/testing)
- [Europe PMC REST API](https://europepmc.org/RestfulWebService)
- [Europe PMC OA XML availability](https://europepmc.org/help)
- [OpenAlex identifier lookup](https://help.openalex.org/api/get-single-entities/)
- [OpenAlex OA locations](https://help.openalex.org/data/locations/)
- [OpenAlex authentication](https://help.openalex.org/api/authentication/)

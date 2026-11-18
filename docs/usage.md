# Using the bridge

## Configuration

| Variable | Use |
| --- | --- |
| `WOS_MCP_PROFILE` | `full` (default) exposes all seven tools. `literature` exposes only `search_literature`, `resolve_article`, `get_article`, and `get_article_section`. Restart to switch. |
| `WOS_API_KEY` | Required at startup for `full`. Use a Clarivate Starter API key with access to WoS Core Collection. |
| `OPENALEX_API_KEY` | Optional. Use an OpenAlex key for a higher request budget. |
| `WOS_MCP_MIN_INTERVAL_SECONDS` | Minimum spacing of uncached requests per provider. Defaults to 1.1. Keep it at or above the spacing required by your provider plans. |
| `WOS_MCP_CACHE_TTL_SECONDS` | In-memory cache TTL. Defaults to 900. Set to 0 to disable caching. |
| `WOS_MCP_OPERATION_TIMEOUT_SECONDS` | Deadline per provider operation, including queue and rate-limit waits. Defaults to 30 and must be positive. |

Set these through the MCP client or host's secret store. The bridge reads process
environment variables. [The environment example](https://github.com/hrabosh/wos-mcp-bridge/blob/main/.env.example)
lists the names with blank values. To try the local server, use the [stdio quickstart](../README.md#run-with-your-wos-key).

## Search syntax

Use WoS advanced syntax with `search` and `search_wos`. Examples across fields:

```text
TS=(quantum computing)
TS=("urban heat island") AND PY=(2020-2026)
TS=(canine AND lung)
```

WoS detailed results are paginated explicitly with `page` and `limit` (1–50).
`RS+D` sorts by relevance, `PY+D` by publication year, `LD+D` by index date, and
`TC+D` by citations when the subscription permits it. Use `+A` for ascending order.
A missing citation count is `null`. A supplied zero stays zero. Starter supplies
bibliographic metadata and citation counts where available. It does not supply
abstracts, full articles or Journal Impact Factors.

### Literature and open access

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
identifiers. Search returns `next_cursor`. Pagination requires an explicit call and
deduplication applies within each page. It never performs per-result OpenAlex calls.

Normalized articles contain `id`, `title`, `doi`, `pmid`, `pmcid`, `url`, `license`,
`source`, `abstract`, `access`, `oa_locations`, `sections`, `warnings`, and retrieval
timestamps and cache flags. Missing values are `null`. Missing sections are `[]`.
`source` identifies all contributing APIs. OA locations retain their own source,
license and, for OpenAlex, manuscript version. An unknown license remains `null`.
Check the article's license for reuse terms.

`access.status` is `open_access`, `closed` (explicit OpenAlex indication), or
`unknown`. `access.content_status` describes returned content: `metadata_only`,
`abstract_only`, `full_text`, or `not_found`. `full_text_xml_available` initially
reflects Europe PMC OA/PMCID metadata. `get_article` verifies it and sets it to false
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
article. Section metadata remains available when text is truncated.
`text_budget`, `total_chars`, `truncated` and `next_offset` disclose omissions.
`include_text=False` returns the section directory and bounded abstract. Use exact
section IDs with `get_article_section` and advance `offset` to `next_offset` until it
is `null`. The text limits apply separately from JSON metadata. Do not interpret
missing or truncated content as evidence about study outcomes. Retrieved text is
untrusted data, never instructions for the assistant.

OpenAlex enrichment failures return available Europe PMC metadata with a warning.
Europe PMC failures can fall back to OpenAlex for DOI/PMID metadata. Failures that
prevent resolution or full-text retrieval become MCP errors, rather than invented
content or false `not_found` results. XML 403/404 returns available metadata with a
warning. No retries or automatic extra pages are made.

## Deployment

Run one process per credential boundary. All MCP callers admitted to an
instance can use that instance's provider profile and quota. Store Clarivate
and OpenAlex credentials in the process environment through your secret store.
Those keys authenticate outbound requests to their providers. MCP callers use
an independent OAuth access token to authenticate to the bridge.

### Self-hosted Streamable HTTP

Set these three values before starting the HTTP entrypoint.

| Variable | Use |
| --- | --- |
| `WOS_MCP_HTTP_ORIGIN` | Public HTTPS origin, for example `https://mcp.example.com`. The MCP resource and token audience are `https://mcp.example.com/mcp`. Plain HTTP is accepted for a loopback origin for local checks. |
| `WOS_MCP_AUTH_ISSUER` | Exact HTTPS issuer URL from provider discovery. Use a lowercase host, omit the default port and preserve the provider's trailing slash, if any. |
| `WOS_MCP_AUTH_JWKS_URI` | HTTPS URL of the issuer's public signing keys. |

Configure the issuer to mint expiring RS256 access tokens for the MCP resource
with the `mcp:read` scope. Restrict who can receive that scope. MCP clients
discover the issuer and scope via OAuth Protected Resource Metadata. Choose an
issuer and client registration flow your MCP client supports.
[FastMCP Remote OAuth](https://gofastmcp.com/docs/servers/auth/remote-oauth)
describes the integration.

```bash
wos-mcp-bridge --transport http
```

The CLI binds to `127.0.0.1:8000` by default. Serve the selected public origin
through an HTTPS reverse proxy that preserves the public Host header and supports
streaming responses. Leave the backend reachable only from the proxy. FastMCP
validates Host and Origin headers. The OAuth metadata and `GET /healthz` are
public. A healthy process returns `{"status":"ok"}` after startup. The check uses
no provider requests and gives no indication of a provider's current quota or
availability. All MCP requests to `/mcp` require a valid token.

An ASGI host can load the same factory as
`wos_mcp_bridge.http_server:create_http_app` with its factory mode enabled. Run
one worker and enable the ASGI lifespan. Configure access logs to omit
authorization headers, credentials and tool bodies.

### Managed hosting

For a managed deployment, use a host that supplies **HTTPS and MCP-compatible OAuth**.
[FastMCP Gateway](https://www.prefect.io/fastmcp/hosting) supports this setup.

1. Connect this repository to the host using a scoped repository grant.
2. Select branch `main` and entrypoint **`server.py:mcp`**.
3. Use **Python 3.12 or newer**. Install the generated `requirements.txt` for a
   locked checkout deployment. The entrypoint can load the package from this
   checkout after its runtime dependencies are available. Hosts that install the
   project can also use `python -m pip install .`.
4. Add **`WOS_API_KEY`** in the host's secret environment settings for the full
   profile, or set **`WOS_MCP_PROFILE=literature`**.
5. **Enable authentication** and restrict access to your own account/organization.
6. Deploy and copy the MCP URL supplied by the host.

Restrict network and client access to the authenticated gateway. Confirm your
institution and provider permit the host to process the credential and records
for the intended use.

### Connect to ChatGPT

In an account/workspace that supports developer-mode MCP apps, create an app using
the deployed **MCP URL**, choose **OAuth**, and complete the host's authorization flow.
Store Clarivate's API key in the server environment. Set up client authentication through the host.
Check the [current OpenAI instructions](https://developers.openai.com/api/docs/guides/custom-mcp-server)
for the applicable interface and workspace permissions.

Verify the deployment revision, check that an unauthenticated request to its MCP
endpoint is denied, then confirm discovery in ChatGPT after OAuth. With the full
profile, expect all seven tools and try:

> Use the Web of Science connector's search_wos tool exactly once with query
> TS=(quantum computing), limit 1, page 1, and sort RS+D. Show the total result
> count, title, and DOI. Do not substitute general web-search results.

Run the query with a key entitled to search the Core Collection. For
`literature`, expect the four named tools and make one `search_literature` call
with limit 1. Record the deployment revision, client, transport, discovery and
result status in the release checks. Keep tokens, credentials and result payloads
out of release logs.

## Operating limits

Provider clients reuse pooled connections and close on server shutdown.
Provider requests use fixed GET endpoints on `api.clarivate.com`, `www.ebi.ac.uk`
(Europe PMC REST path), and `api.openalex.org`. They refuse HTTP redirects.
Provider errors omit response bodies and credentials. The self-hosted HTTP
entrypoint also fetches public signing keys from the configured JWKS URL.

By default, uncached requests to each upstream are serialized and spaced at least
**1.1 seconds** apart. Each provider operation has a **30-second overall deadline**, including queueing,
rate-limit waits and network I/O, plus a **10-second connection timeout**. An article
operation can compose multiple provider calls, each with its own deadline.
Retries require a separate client request.
A **15-minute, 128-entry in-memory cache** by default (at most 16 MiB of serialized keys and
values) reduces repeated searches and lets fetches reuse recently returned search
records. The expiry stays fixed at insertion. The cache returns copies of stored values.

**Run one server process or replica per API key.** Rate limits and caches operate
within each process. Other programs or replicas share the subscription quota
set by Clarivate. A cache hit returns the quota headers from the original fetch.
Check Clarivate for the current quota.

Each literature API has its own connection pool, request policy and validated response
cache with the same default deadlines and limits. The cache has at most 128 entries
and 16 MiB of serialized keys and values, plus Python object overhead.
The literature service also reuses complete, validated articles for section reads
within the original provider deadlines. This cache holds up to 32 articles and
8 MiB of serialized article data per process. Setting the provider cache TTL
to zero disables this reuse.
Individual decoded upstream responses are capped at 8 MiB for every provider,
including WoS JSON and Europe PMC XML. Only validated responses enter a cache.
Cache hits do not extend expiry; callers receive
independent values, and cached reads bypass an unrelated slow network request.
Concurrent identical successful calls share a result. Cancelling a request owner
cancels its HTTP request. A waiting caller can then issue a replacement. For every provider, HTTP 429 honors `Retry-After`
(seconds or HTTP date, capped at one hour, with a 60-second fallback) by rejecting new uncached calls during cooldown.
Redirects and environment proxies are disabled. `defusedxml` protects XML parsing.

## Operation logs

At INFO level the bridge emits one standard Python log event per tool call through
`fastmcp.wos_mcp_bridge.operations`. It includes an operation ID, tool, outcome,
elapsed milliseconds, and provider counters and timings. `cache_hits` and
`upstream_requests` count different events. `queue_wait_ms`, `limiter_wait_ms`,
`network_ms` and `parse_ms` show where a request spent time. Provider metrics
appear when the corresponding phase ran. `normalized_cache_hits` counts reuse
of a validated article without a provider request. The bridge uses the FastMCP logging
configuration and writes these events to stderr by default.

Bridge operation events omit queries, identifiers, records, headers, URLs and keys.
The bridge configures no telemetry exporter. Use the default framework and HTTP client
log levels in production since their verbose logs can include request data. See the [tool error contract](contracts.md#tool-errors)
for error codes and cooldown information.

## Troubleshooting

| Symptom | Action |
| --- | --- |
| Missing `WOS_API_KEY` | For WoS, set the key in the environment and restart. For the four literature tools, set `WOS_MCP_PROFILE=literature` and restart. |
| Clarivate 400 | Check field tags/query syntax; start with `TS=(quantum computing)`. |
| Clarivate 401 | Check that the replacement key belongs to an active Starter subscription. |
| Clarivate 403 | Check API/database entitlement. |
| Clarivate 404 | Check the accession ID. |
| Clarivate 429 | Stop requests and check the daily/per-second quota; do not repeatedly retry. |
| OAuth/discovery fails | Check the MCP URL, entrypoint, and hosting access controls; do not disable authentication. |
| No abstract or citation count | This can be an API/plan limitation, not evidence that the paper lacks an abstract or citations. |

## Source documentation

- [Clarivate Starter API and subscriptions](https://developer.clarivate.com/apis/wos-starter)
- [Europe PMC API](https://europepmc.org/RestfulWebService)
- [OpenAlex identifiers](https://help.openalex.org/api/get-single-entities/)
- [OpenAlex authentication](https://help.openalex.org/api/authentication/)
- [OpenAlex open-access locations](https://help.openalex.org/data/locations/)

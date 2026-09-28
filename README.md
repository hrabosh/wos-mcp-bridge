# Web of Science MCP bridge

A small, read-only MCP server for the **Clarivate Web of Science Starter API**.
It lets an MCP client search the Core Collection and retrieve bibliographic records.
It is not an official Clarivate or OpenAI product.

```text
ChatGPT / MCP client -> authenticated MCP host -> Clarivate Starter API
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

All searches use **WoS advanced syntax**, not free-form semantic search:

```text
TS=(canine AND lung)
TS=((dog OR dogs OR canine) AND (lung OR pulmonary) AND (carcinoma* OR tumor* OR tumour*))
TS=(canine AND lobectom*) AND PY=(2020-2026)
```

`RS+D`: relevance; `PY+D`: newest publication; `LD+D`: newest indexed;
`TC+D`: most cited, when permitted by your subscription. Ascending variants use `+A`.
The result limit is 1-50. Pagination is explicit; no automatic extra requests are made.

## Deploy from this repository

For a managed deployment, use a host that supplies **HTTPS and MCP-compatible OAuth**.
[Prefect Horizon](https://gofastmcp.com/deployment/prefect-horizon) supports this setup.

1. Connect this repository to the host; grant access only to the repositories it needs.
2. Select branch `main` and entrypoint **`server.py:mcp`**.
3. Use **Python 3.12** and install **`requirements.txt`**. There is no build step.
4. Add **`WOS_API_KEY`** in the host's secret environment settings.
5. **Enable authentication** and restrict access to your own account/organization.
6. Deploy and copy the MCP URL supplied by the host.

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

Confirm discovery of `search`, `fetch`, and `search_wos`, then try:

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

Two application modules, no database, background workers, or extra web framework:

```text
server.py                MCP tools, lifespan, and response serialization
wos_client.py            Async HTTP client, validation, throttling, and cache
tests/                   Synthetic HTTP fixtures and in-process MCP tests
.github/workflows/ci.yml  Lint, formatting, and tests
```

The HTTP client reuses pooled connections and is closed on server shutdown.
Only fixed Clarivate GET endpoints are used; there is no arbitrary URL-fetch tool,
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

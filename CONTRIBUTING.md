# Contributing

The fastest way to review a change is to run the same offline checks as CI.
Install Python 3.12 and uv 0.13.9, then run these commands from the checkout:

```bash
uv sync --locked --python 3.12
uv run --locked python -m pytest -q
uv run --locked python -m ruff check .
uv run --locked python -m ruff format --check .
uv run --locked ty check --error-on-warning
```

The offline tests use synthetic HTTP responses and require no provider keys.
For a working MCP example, run the [offline demo](README.md#try-it-without-credentials).
The [release guide](docs/releasing.md) describes artifact checks and dependency
updates.

The optional live integration test queries Europe PMC and OpenAlex for a small
open-access example. It requires outbound HTTPS and uses provider quota. Run
it only when you intend to test those services:

```bash
RUN_LITERATURE_INTEGRATION=1 uv run --locked python -m pytest -q -m integration
```

Set `OPENALEX_API_KEY` in the test environment if the anonymous budget is exhausted.

- `src/wos_mcp_bridge/server.py` assembles the server and owns its lifespan.
- `tools.py` and `models.py` in that package define the MCP adapters and wire contracts.
- Provider clients and the literature service handle requests, normalization and
  caching. [Architecture decision](docs/decisions/0001-application-composition.md)
- `tests/` covers the providers and MCP behavior. `scripts/` contains the offline
  demo and release checks.

Keep changes focused. For behavior fixes, include a test that reproduces the
problem with synthetic data. For MCP changes, describe the client-visible effect
and include the checks you ran in the pull request. Keep API keys, private logs,
provider responses and article text out of examples and fixtures.

Use [GitHub Issues](https://github.com/hrabosh/wos-mcp-bridge/issues) for bugs,
questions and feature requests. Include the version, transport and a sanitized
example when relevant. Support is best effort through the repository. See the
[security policy](SECURITY.md) for vulnerabilities.

# Changelog

## Unreleased

- Add authenticated Streamable HTTP, OAuth resource discovery and a process health
  endpoint for self-hosted deployments.
- Add an explicit literature profile for Europe PMC and OpenAlex without a
  Web of Science key. Keep stdio and the seven-tool full profile as defaults.
- Test real stdio and loopback HTTP with synthetic upstream responses.

## 0.1.0 — 2026-11-18

The first packaged release combines Web of Science Core Collection search with
literature resolution from Europe PMC and OpenAlex.

- Package seven read-only MCP tools for WoS, Europe PMC and OpenAlex. The
  application factory owns client startup, cleanup and provider configuration.
- Publish input and output schemas. Detailed records add common bibliography
  while preserving legacy fields, unknown citation counts and text limits.
- Honor upstream cooldowns, validate responses before caching, and isolate
  provider caches and request limits. Cache hits can complete while another
  request waits on a provider.
- Verify Python 3.12 and 3.14, type checks and installed stdio. The wheel and
  source distribution are checked outside the repository.
- License the program under Apache-2.0. Add a synthetic offline demo and
  document provider entitlements, data rights and operational limits.

### Migrate from a source checkout

Managed hosts can keep loading `server.py:mcp` after installing the runtime
requirements. To use the installed module or console command, run
`uv sync --locked --no-dev` or follow the
[pip installation steps](docs/releasing.md#development-and-deployment). Use
`python -m wos_mcp_bridge` or `wos-mcp-bridge` for stdio. Python integrations
that imported the old root modules should import from `wos_mcp_bridge` instead.
The [contract guide](docs/contracts.md) describes the MCP response fields.

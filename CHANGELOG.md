# Changelog

## 0.1.0 release candidate

This entry describes the first planned public package release. The package and
CLI report version `0.1.0`. Use the [release guide](docs/releasing.md) to verify
the built artifacts.

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

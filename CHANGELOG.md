# Changelog

No public release has been published by this refactoring work.

## Unreleased

- Package the seven read-only WoS, Europe PMC and OpenAlex MCP tools with module
  and console entrypoints; retain the source hosting entrypoint `server.py:mcp`.
- Separate application composition, resource ownership, provider clients and MCP adapters.
- Publish validated input/output contracts and common bibliography while preserving
  legacy fields, missing-versus-zero counts and bounded text semantics.
- Validate provider responses before caching, honor rate-limit cooldowns and preserve
  cancellation/cleanup behavior with bounded response and cache sizes.
- Add reproducible dependency resolution, distribution checks and release preparation.
- License the program under Apache-2.0; provider data retains its own terms.

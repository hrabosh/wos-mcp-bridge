# Reliability and application composition implementation plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task by task, with regression tests before behavior changes.

**Goal:** Deliver the first refactoring stage from GitHub issues #5 and #3 without changing the seven MCP tools or their payloads.

**Architecture:** Compose small process-local cache and request-policy components into the existing API clients. Move the implementation into one Python package and assemble its clients and services through a typed application factory with explicit resource ownership. Keep pure transformations as functions.

**Tech stack:** Python 3.12, existing pinned FastMCP, HTTPX, Pydantic, defusedxml, pytest and Ruff. No new runtime dependencies.

**Spec:** https://github.com/hrabosh/wos-mcp-bridge/issues/5 and https://github.com/hrabosh/wos-mcp-bridge/issues/3; the roadmap's compatibility and simplicity requirements apply.

## Global constraints

- Preserve the seven tool names and response formats, including historical quota information.
- Keep fixed upstream hosts, no redirects/proxies, no automatic retries/pagination, bounded text and safe XML.
- One process per credential boundary; no database, distributed limiter, billing, plugin framework or DI container.
- Keep `server.py:mcp` and `python server.py` working during migration.
- Typed domain output schemas remain issue #4; package publishing/release automation remains issue #7.

## Review focus

- Invalid JSON, metadata and XML must not poison the cache.
- Cancellation of a request owner must not strand followers or leak background work.
- Cache hits must work during an unrelated network stall or rate-limit cooldown.
- Failed application startup must close already-created clients; supplied clients remain caller-owned.
- Packaging/import changes must not mix global application state across server instances.

## Task 1: HTTP and cache reliability

**Files:** Add `http_support.py` and `tests/test_http_reliability.py`; modify the four API transport/provider modules and their existing tests.

**Interfaces:** `ResponseCache` owns TTL/LRU copying and serialized-byte accounting; `RequestPolicy` owns upstream serialization, request spacing and cooldown. Clients own parsing and domain errors. Add keyword constructor limits (`max_response_bytes`, `cache_max_bytes`, `cache_max_entries`, `operation_timeout`) and optional clock/sleep injection for deterministic tests.

- [x] Write failing regressions for Retry-After, invalid JSON/metadata/XML recovery, warm-cache availability, response/cache bounds, total deadline and cancellation.
- [x] Run the new tests and confirm failures match the known bugs.
- [x] Implement the shared components and integrate them; only validated results enter caches. Serialize upstream work with a double-checked cache; owner cancellation cancels its HTTP operation and a follower may issue a replacement.
- [x] Verify the full offline suite and Ruff. Commit the reliability change separately from the move.

## Task 2: Package and factory

**Files:** Move implementation to `src/wos_mcp_bridge/`; add `config.py`, `application.py`, package initializer, `__main__.py` and factory tests; update imports, `pyproject.toml`, README and root compatibility entrypoint.

**Interfaces:** Immutable `Settings` loads environment explicitly; `Services(wos, literature)` contains concrete services; `create_server(settings=None, *, services=None)` creates independent FastMCP servers. Factory-owned resources are registered immediately with `AsyncExitStack`. Supplied services are borrowed and never closed by the server. Environment defaults are resolved at startup, not import.

- [x] Write failing tests for independent server instances, borrowed ownership, startup failure cleanup and environment loading without leaked secrets.
- [x] Implement package layout and factory without wrapping pure helpers in classes or duplicating the literature service.
- [x] Keep root `server.py` a thin compatibility wrapper. Add minimal local build metadata so the src package can be installed; full release engineering remains #7.
- [x] Run the complete offline suite, Ruff, and install/import smoke checks outside the checkout.
- [x] Add a short ADR and architecture diagram describing the implementation. Commit the structural change separately.

## Task 3: Review and handoff

- [x] Review the combined diff against #5 and #3, including each item in Review focus.
- [x] Fix material findings with failing regression tests, rerun the full checks and report any remaining scope explicitly.
- [x] Keep the branch available for review. Do not merge, publish, change repository visibility or activate billing.

## Execution record

- Baseline: 134 tests passed, 1 live test skipped on 2026-11-18.
- Workspace: managed worktree on `refactor/reliability-and-composition`.
- Authorization: user asked to start the refactoring after reviewing the roadmap; proceed inline without another design-selection round.

- Final verification: 196 offline tests passed, 1 opt-in live test skipped; Ruff checks and formatting passed.
- An independent review found non-finite/invalid-Unicode JSON, malformed Retry-After, empty normalized titles and delayed streaming-overflow detection. Twenty-two regressions failed before the fixes; all review regressions and the complete suite passed afterward.
- The final wheel was built, installed outside the checkout and imported without credentials or constructing HTTP clients. Module, console and legacy entrypoints passed real stdio discovery of all seven tools, readable validation errors and subprocess cleanup. The smoke client explicitly used handshake mode; broader auto-negotiation/HTTP interoperability remains part of issue #10.
- The implementation remains on the named feature branch for review; no merge, public package release, repository-visibility change or billing activation was performed.

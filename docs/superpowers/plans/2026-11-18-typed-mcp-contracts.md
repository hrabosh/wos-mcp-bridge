# Typed MCP contracts implementation plan

> **For agentic workers:** Use superpowers:executing-plans inline and superpowers:test-driven-development. Finish with one independent whole-change review.

**Goal:** Implement issue #4: strict typed outputs, discoverable input/output schemas and compatible bibliography metadata for all seven tools.

**Architecture:** Keep dictionary wire payloads for existing consumers, validate them with concrete Pydantic models before caching/serialization, and derive outputSchema from the same models. Add one common bibliography object while preserving existing authors/year fields and the exact small search/fetch core contracts. Use standard Annotated/Field constraints for MCP inputs.

**Tech stack:** Existing Python 3.12, Pydantic 2.13.4 and FastMCP 4.0.10; jsonschema as an explicit development dependency for wire-contract tests. No runtime dependencies added.

**Spec:** https://github.com/hrabosh/wos-mcp-bridge/issues/4

## Global constraints

- Preserve seven tool names, text JSON/structuredContent equivalence, missing-versus-zero semantics, provenance, safe errors, and text truncation.
- Preserve legacy authors and year types; new bibliography has authors list or null, numeric year or null and journal or null. Do not split combined author strings or infer metadata from titles.
- Preserve absent optional fields with exclude_unset; use explicit null for known missing values. Not-found records remain distinct from failures.
- Reject malformed typed data before cache insertion; do not expose validation input values in errors.
- Keep real WoS pages objects (range/begin/end/count) and source-specific identifier/cursor behavior.
- No generic response framework, new API, automatic pagination, or version-breaking renames.

## Review focus

- Nullable, not-found, fallback and directory-only variants must match the published schemas without added false defaults.
- Strict validation must reject booleans/numeric strings where a numeric field is required and recover on a subsequent healthy request.
- Legacy year/author representations and WoS page objects must survive unchanged.
- Both result representations must describe exactly the same validated object.
- Invalid/overlong input must fail before upstream calls and schema metadata must exclude Context, services and credentials.

## Task 1: Model and provider contracts

**Files:** New src/wos_mcp_bridge/models.py; provider normalization and literature response assembly; tests/test_contracts.py.
**Interfaces:** Concrete Pydantic models for bibliography, records, retrieval, access, locations, raw/bounded sections and the tool-specific response envelopes. Provider normalizers return validated JSON-compatible dictionaries and translate ValidationError into existing safe domain errors.

- [x] Add failing tests for schemas, actual payloads, legacy fields, typed metadata recovery, nullable and truncated variants.
- [x] Implement strict models and additive bibliography using authoritative author-list/journal fields only.
- [x] Validate provider records before caching; preserve valid legacy representations and unknown metadata.
- [x] Run provider tests and full offline suite.

## Task 2: MCP schema and serialization boundary

**Files:** src/wos_mcp_bridge/tools.py and contract tests.
**Interfaces:** Annotated text input constraints and explicit model-derived output_schema per tool. One small serialization helper validates the concrete response model, dumps with exclude_unset, and emits matching JSON/structuredContent. Invalid responses become sanitized tool errors.

- [x] Verify contract tests fail on missing schemas and incomplete input metadata before implementation.
- [x] Publish concrete schemas for all tools and add query/id/cursor/section length bounds, patterns, descriptions and examples.
- [x] Exercise successful, empty, not-found, directory, fallback and error responses against the discovered JSON Schema.
- [x] Run full offline checks and document compatibility/migration semantics.

## Task 3: Review and verification

- [x] Run an independent code review, fix material findings with regressions and verify all checks.
- [x] Build/install a wheel outside the checkout and verify schema discovery over real stdio in handshake mode.
- [x] Commit this issue separately on the existing managed refactoring branch; preserve it locally for review.

## Baseline

196 tests passed, 1 opt-in live integration skipped; worktree clean at 4532080.

## Execution record

- Tasks 1 and 2 complete: observed failing discovery/provider/input regressions before implementation; all 230 offline tests now pass (1 opt-in live test skipped). Ruff checks, formatting and diff whitespace checks pass.
- Compatibility regression checks preserve WoS page objects and null keywords; incomplete author lists remain unknown in common metadata.
- No new runtime dependencies; jsonschema is an explicit test dependency.

- Independent review of 4532080..5c1190b found four material gaps; all fixed with observed failing regressions followed by passing checks:
  - JATS typed section validation now precedes cache insertion; blank, overlong, control-containing and padded IDs cannot poison the cache.
  - Supplied malformed citation counts are rejected; absent/null counts remain unknown and zero remains zero.
  - Continuation cursors use the same supported constraints before caching and during MCP input validation.
  - Runtime HTTP IRI/timestamp checks agree with advertised formats; test-only format checkers are installed and their registration/rejection behavior is asserted.
- Final review fix suite: 244 passed, 1 opt-in live integration skipped. Ruff lint and formatting and git diff whitespace checks passed.
- Rebuilt and installed the reviewed wheel outside the checkout. Module, console and compatibility entrypoints all passed real stdio discovery of seven concrete schemas, invalid-input rejection and subprocess cleanup with synthetic credentials and no live provider calls.
- No deferred review findings. Original main checkout remains clean; the implementation branch/worktree is preserved locally.

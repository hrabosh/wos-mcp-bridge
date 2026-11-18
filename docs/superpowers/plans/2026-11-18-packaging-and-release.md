# Packaging and reproducible release implementation plan

> **For agentic workers:** Execute inline with superpowers:executing-plans and superpowers:test-driven-development; finish with one independent whole-change review.

**Goal:** Complete issue #7's packaging, dependency and release preparation without publishing.

**Architecture:** Retain setuptools and src layout. pyproject.toml owns version/dependency metadata; uv.lock owns resolved dependencies; requirements files are generated compatibility exports. Standard scripts check and smoke-test built distributions; a separate workflow gates OIDC publication.

**Tech stack:** Python >=3.12, setuptools 84.2.1, uv 0.13.9, existing FastMCP 4.0.10 and provider clients. No runtime feature/framework added.

**Spec:** ../specs/2026-11-18-packaging-design.md and https://github.com/hrabosh/wos-mcp-bridge/issues/7

## Global constraints

- Preserve seven tools, default stdio and server.py:mcp compatibility.
- Apply user-approved Apache-2.0; code license does not license provider data.
- Version and dependency intent have one canonical source; requirements exports are generated and checked for drift.
- Validate/install wheel and sdist outside checkout and with no live provider keys or quota consumption.
- Publication remains explicit, tag/version checked and separate from PR jobs. Do not publish, push or configure external accounts.
- No npm wrapper, second build backend, custom registry or broad #10 CI expansion.

## Review focus

- Source distributions must build the same package without omitted modules or root-checkout imports.
- Cached build metadata and local secrets/cache files must not enter either archive.
- Tag/version mismatches or failed artifact checks must prevent publication.
- Generated exports and lock must stay aligned with metadata without circular project dependencies.
- CLI version/discovery must work from installed artifacts and pipx outside checkout.

## Task 1: Metadata, license, CLI and locked dependencies

**Files:** pyproject.toml, src/wos_mcp_bridge/__main__.py, LICENSE, NOTICE, uv.lock, generated requirements files, tests/test_cli.py, .github/workflows/ci.yml.
**Interfaces:** Existing console/module commands plus credential-free --version. uv sync --locked provisions development; exports retain pip compatibility.

- [ ] Add a subprocess regression for --version without credentials; run it and observe failure.
- [ ] Add metadata, license, single-source CLI version and dependency ranges/groups. Seed/resolve uv.lock at tested runtime lower bounds; generate requirements exports.
- [ ] Update CI installation to use the lock and reject stale exports; keep existing offline checks.
- [ ] Run the CLI regression, uv lock --check and full offline tests; commit.

## Task 2: Distribution validation and installed smoke checks

**Files:** MANIFEST.in, scripts/check_dist.py, scripts/smoke_dist.py, tests/test_release.py.
**Interfaces:** check_dist.py DIST [--tag vVERSION] [--project PATH] validates exactly one wheel/sdist, metadata/version, licenses, module completeness and archive content. smoke_dist.py DIST creates isolated installed environments and verifies stdio discovery/errors and module/console version.

- [ ] Add executable checker tests using minimal real archives: valid pair, mismatched tag/version, leaked .env/cache and omitted module. Observe missing-checker failure.
- [ ] Implement bounded archive checks with standard zip/tar/email parsing and packaging metadata parsing; add explicit distribution manifest.
- [ ] Build wheel and sdist, check metadata/content and install each outside checkout; stdio discovery must publish all seven schemas and invalid fetch must fail without upstream requests.
- [ ] Verify pipx runs the actual local wheel with --version; commit.

## Task 3: Release workflow, documentation and review

**Files:** .github/workflows/release.yml, docs/releasing.md, README.md, CHANGELOG.md and plan execution record.
**Interfaces:** Explicit manual publication on matching version tag; separate OIDC-only publish job consumes checked build artifacts. Protected pypi environment configuration remains owner setup.

- [ ] Add pinned-action workflow build/check/install stages and separated publication job. Check workflow syntax and version/tag rejection through the checker.
- [ ] Document locked setup, export regeneration, build/install, pipx, source/host compatibility, first release and bad-version recovery. Record PyPI name availability as dated, not reserved.
- [ ] Run full offline suite, lint/format, build/archive checks and installed smoke; request one read-only independent review.
- [ ] Fix material review findings with regressions, commit and preserve the local branch/worktree.

## Execution record

Base: 8fc07b1. User approved Apache-2.0 during implementation planning. No applicable AGENTS.md found. Existing linked worktree and branch reused.

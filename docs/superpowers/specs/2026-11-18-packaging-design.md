# Packaging and release design (#7)

Implement https://github.com/hrabosh/wos-mcp-bridge/issues/7 on the existing isolated refactoring branch. The user approved continuation and Apache-2.0. Keep all seven MCP contracts, default stdio, and the hosting compatibility entrypoint.

Retain setuptools rather than introducing another backend. Make pyproject.toml the source for version, runtime dependency ranges and development dependencies; use one uv.lock for exact transitive resolutions. Generate requirements.txt and requirements-dev.txt from it for existing pip-based hosting/development; never hand-edit those exports. Pin uv and the build backend for repeatable tooling. Seed the lock at the already-tested direct runtime versions so this packaging task does not also upgrade the SDK.

Alternatives considered: pip-tools would preserve the requirements workflow but add a separate dependency-input layer; removing requirements files would simplify files but impose an avoidable hosting migration. Retaining setuptools with uv and generated compatibility exports is the implementer's choice.

Add standard repository URLs, Apache-2.0 SPDX metadata and license files. Read CLI --version from installed distribution metadata; do not add another version constant. Add distribution content/metadata checks and exercise wheel and sdist installations in separate environments outside the checkout. Test pipx against the built wheel without using a public PyPI release.

A separate release workflow builds and checks artifacts for PRs, version tags and manual runs. Publishing requires an explicit manual request on a matching version tag, a dedicated pypi environment and Trusted Publishing/OIDC; PR jobs have no publishing permission. The workflow must publish the same artifacts it checked. Do not create a PyPI project, push a tag, change GitHub visibility or publish anything during implementation.

Document clean setup, generated exports, host migration, package name preflight, initial release/tag/changelog, PyPI environment setup, and recovery by yanking/releasing a new version. Code licensing does not license upstream data. Remaining open-source readiness requirements stay in #11; broader CI/type/HTTP coverage stays in #10.

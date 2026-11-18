# Packaging and releases

The version, runtime dependency ranges, development dependencies, URLs and license
are declared in `pyproject.toml`. The build backend is setuptools 84.2.1; uv 0.13.9
resolves and runs the development/deployment environment. `uv.lock` records exact
transitive versions, markers and artifact hashes. The tested lock uses
FastMCP 4.1.7 and MCP Python SDK 2.3.0.

Package dependency ranges allow downstream environments to resolve compatible
versions. The lock is the tested resolution for this repository; it is not a claim
that every possible dependency combination or future Python version was tested.
Reproducibility here means resolved dependency versions and pinned build tooling,
not byte-identical archives or a guarantee about external APIs.

## Development and deployment

Install uv 0.13.9 using its official installer or an isolated tooling environment,
then run from the checkout:

```bash
uv sync --locked --python 3.12
uv run --locked python -m pytest -q
uv run --locked python -m ruff check .
uv run --locked python -m ruff format --check .
uv run --locked ty check --error-on-warning
uv run --locked wos-mcp-bridge --version
```

For a deployment using this checkout and its lock:

```bash
uv sync --locked --no-dev --python 3.12
uv run --locked --no-dev wos-mcp-bridge
```

The latter starts stdio and needs `WOS_API_KEY`. It does not open an HTTP endpoint.
Managed hosting keeps `server.py:mcp`. A pip-based host can load it from a
checkout after installing the generated runtime dependencies:

```bash
python -m pip install -r requirements.txt
```

To install the package and its CLI in that same environment, then run
`python -m pip install --no-deps .`. A regular `pip install .` or wheel installation
resolves the package's declared dependency ranges. For development with pip,
install `requirements-dev.txt` and then run `python -m pip install --no-deps -e .`.

## Updating dependencies

Edit dependency intent only in `pyproject.toml`. For a focused update, run
`uv lock --upgrade-package PACKAGE`; use `uv lock --upgrade` for an intentional
full refresh. Then regenerate both compatibility files with exactly these commands:

```bash
uv export --locked --no-dev --no-emit-project --no-hashes --output-file requirements.txt
uv export --locked --no-emit-project --no-hashes --output-file requirements-dev.txt
uv sync --locked --python 3.12
```

The requirements files are generated exports, not separate inputs or lockfiles.
Their pinned versions and environment markers support existing pip deployments;
they omit hashes for compatibility. uv is the authoritative hash-bearing lock path.
CI regenerates both files and rejects drift. Run the offline suite and artifact
checks after an update. Update the explicit uv/tool pins together when upgrading
build tooling.

On a dependency update and before release, audit the runtime export for known
PyPI advisories. Run the command with each CI interpreter to include its
platform and version markers. Review reported fixes, update the lock and rerun
verification. [pip-audit documentation](https://github.com/pypa/pip-audit)

```bash
uvx --python 3.12 pip-audit --no-deps --disable-pip -r requirements.txt
uvx --python 3.14 pip-audit --no-deps --disable-pip -r requirements.txt
```

## Build and verify the actual artifacts

Start with an empty output directory (archive checks reject extra distributions):

```bash
uv build --no-sources
uv run --locked python -m twine check --strict dist/*.whl dist/*.tar.gz
uv run --locked python scripts/check_dist.py dist
uv run --locked python scripts/smoke_dist.py dist
```

`uv build` creates a source distribution and builds the wheel from it. The checker
compares metadata, dependency ranges, all tracked package modules and license files
with the checkout. Validation requires the project Git checkout and uses Git's index
as the intended file inventory. It rejects untracked local Python/Markdown files as
well as unintended archive paths, local secrets/caches and links. A raw local build
is not a verified release until this check passes. Source distributions still build
and install without Git; release validation is deliberately performed in the checkout.
The source archive includes synthetic tests, release scripts and user documentation;
it excludes agent planning files. The wheel contains the package and distribution
metadata/license files.

The smoke check creates separate temporary environments outside the checkout,
installs the locked runtime dependencies, and installs each wheel/source artifact
without re-resolving its dependencies. It also loads `server.py:mcp` from a
separate working directory in an environment containing only runtime
requirements. It verifies installed package location,
credential-free module/console versions, import without constructing HTTP clients,
and stdio discovery of all seven output schemas. The MCP SDK negotiates protocol
revision `2025-11-25` in the initialize handshake. A synthetic HTTP response
exercises a successful WoS call and schema validation, and an invalid accession
ID must produce a tool error. The smoke check validates every stdout line
through subprocess exit. The fixture supplies synthetic credentials and blocks
network access at the provider HTTP boundary for the successful path.

This verifies installed subprocess interoperability with the locked MCP SDK.
Run the [hosted transport checks in #8](https://github.com/hrabosh/wos-mcp-bridge/issues/8)
before claiming compatibility with a particular hosted client and its OAuth flow.

A pipx installation was also checked against the local wheel, outside the checkout:

```bash
pipx run --python python3.12 --spec /absolute/path/to/dist/wos_mcp_bridge-0.1.0-py3-none-any.whl wos-mcp-bridge --version
```

Use the actual built filename when the version changes. Omit `--version` to start
stdio with credentials supplied through the environment. This local-wheel command
works before a PyPI release; it does not imply that a public package exists.

## First public release checklist

- Review the README, release notes, contribution guide and security policy.
  Confirm the GitHub **Report a vulnerability** form is available and reaches a
  maintainer. Scan the exact release source and all reachable history for
  credentials and review stored fixtures. Revoke any confirmed exposed
  credential at its provider. Check that LICENSE, NOTICE and package metadata
  agree on Apache-2.0. Provider data and article text carry separate terms.
- Confirm ownership and availability of `wos-mcp-bridge` on PyPI immediately before
  release. The JSON API returned 404 on 2026-11-18; this was only a dated lookup,
  not a reservation or approval to publish. Rename metadata/entrypoints/docs together
  if the name cannot be used.
- Set the release version in `pyproject.toml`, refresh the lock and exports,
  and replace the release-candidate heading in the changelog with the released
  version and date. Check that the README describes the published installation
  path only after the package is available.
- Complete review, the offline suite and all artifact checks. Record the commit,
  interpreter, lock, workflow result and artifact hashes with the release notes.
- Verify that the GitHub `pypi` environment requires maintainer approval and
  permits only release tags. Confirm the PyPI Trusted Publisher (or pending
  publisher for the first release) uses owner `hrabosh`, repository
  `wos-mcp-bridge`, and workflow `release.yml`. Set the publisher's environment
  to `pypi` to bind it to the approval gate.
- Create the exact `vVERSION` tag on the reviewed commit. Pushing a tag only builds
  and verifies artifacts; it does not publish. `check_dist.py dist --tag vVERSION`
  rejects a tag that differs from the artifacts/project version.
- To publish, explicitly dispatch **Release artifacts** on that tag with `publish`
  set to true, for example `gh workflow run release.yml --ref vVERSION -f publish=true`.
  A request on a branch fails. The `pypi` environment review then gates the separate
  publishing job. Review protections must be configured by the owner beforehand;
  declaring an environment in YAML does not create its protection rules.
- The publication run builds and verifies its artifacts, uploads them as one
  immutable workflow artifact, and passes that same pair to the PyPA publishing
  action. Only the publishing job has `id-token: write`; no long-lived PyPI token
  is used and PR jobs cannot enter it.
- Inspect the resulting PyPI metadata and licenses, install the exact published
  version outside the checkout. For a hosted release, verify authentication with
  missing, invalid and valid tokens, discover the expected tools in the intended
  client, and confirm one permitted call in the chosen deployment. Record the client,
  transport, protocol revision and outcome with the release notes, tag and hashes.

## MCP Registry

Keep the version in `server.json` and its PyPI package entry aligned with
`pyproject.toml`. The `mcp-name` marker in the README must be present in the PyPI
project description before Registry publication. The manifest advertises locally
installed stdio with a user-supplied WoS key. Check the published build before
listing it. [PyPI ownership verification](https://modelcontextprotocol.io/registry/package-types#pypi-packages)

From the release checkout, authenticate as the owner of the GitHub namespace and
publish the manifest with the official publisher:

```bash
mcp-publisher login github
mcp-publisher publish
```

Confirm the public record lists the PyPI package and released version. The
[Registry quickstart](https://modelcontextprotocol.io/registry/quickstart) covers
installing the publisher and checking the record.

## Recovery

Before upload, fix a failed build/check and rerun it. After upload, do not replace
or reuse an existing version: yank an unsuitable release on PyPI, document the
reason, fix the regression, increment the version and publish newly verified
artifacts under a new tag. Pin deployments to the prior known-good version/lock
while the fix is reviewed. Yanking is not deletion and existing exact pins may
still install the yanked files; communicate the replacement version.

## Primary references

- [uv project locking](https://docs.astral.sh/uv/concepts/projects/sync/)
- [uv lockfile exports](https://docs.astral.sh/uv/concepts/projects/export/)
- [uv building distributions](https://docs.astral.sh/uv/concepts/projects/build/)
- [Python package metadata](https://packaging.python.org/en/latest/guides/writing-pyproject-toml/)
- [PyPI Trusted Publishing](https://docs.pypi.org/trusted-publishers/using-a-publisher/)
- [PyPI yanking](https://docs.pypi.org/project-management/yanking/)
- [Apache-2.0](https://www.apache.org/licenses/LICENSE-2.0)

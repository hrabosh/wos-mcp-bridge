# Packaging and releases

The version, runtime dependency ranges, development dependencies, URLs and license
are declared in `pyproject.toml`. The build backend is setuptools 84.2.1; uv 0.13.9
resolves and runs the development/deployment environment. `uv.lock` records exact
transitive versions, markers and artifact hashes. The initial lock retains the
previously tested direct runtime versions, including FastMCP 4.0.10.

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
uv run --locked wos-mcp-bridge --version
```

For a deployment using this checkout and its lock:

```bash
uv sync --locked --no-dev --python 3.12
uv run --locked --no-dev wos-mcp-bridge
```

The latter starts stdio and needs `WOS_API_KEY`. It does not open an HTTP endpoint.
Managed hosting keeps `server.py:mcp`. For a pip-based host, install the generated
runtime dependencies first, then the package:

```bash
python -m pip install -r requirements.txt
python -m pip install --no-deps .
```

Installing `requirements.txt` alone does not install the server package. A regular
`pip install .` or wheel installation instead resolves the package's declared
ranges. `requirements-dev.txt` is the corresponding generated development export;
install the project with `python -m pip install --no-deps -e .` after that export.

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
build tooling; do not add another dependency manager.

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
without re-resolving its dependencies. It verifies installed package location,
credential-free module/console versions, import without constructing HTTP clients,
and real stdio discovery of all seven output schemas plus an input-validation error.
It uses synthetic credentials and disables the SDK's update check; no live provider
calls are made. These checks do not establish a working hosted OAuth deployment.

A pipx installation was also checked against the local wheel, outside the checkout:

```bash
pipx run --python python3.12 --spec /absolute/path/to/dist/wos_mcp_bridge-0.1.0-py3-none-any.whl wos-mcp-bridge --version
```

Use the actual built filename when the version changes. Omit `--version` to start
stdio with credentials supplied through the environment. This local-wheel command
works before a PyPI release; it does not imply that a public package exists.

## First public release checklist

- Finish the remaining open-source readiness items in #11: public documentation,
  contribution/security reporting, content/history review and release notes.
  Apache-2.0 was approved by the owner; LICENSE, NOTICE and package metadata agree.
  This licenses the code, not Clarivate/Europe PMC/OpenAlex data or article text.
- Confirm ownership and availability of `wos-mcp-bridge` on PyPI immediately before
  release. The JSON API returned 404 on 2026-11-18; this was only a dated lookup,
  not a reservation or approval to publish. Rename metadata/entrypoints/docs together
  if the name cannot be used.
- Set the release version once in `pyproject.toml`, refresh the lock and exports,
  and move the relevant Unreleased changelog entries into that version's section.
- Complete review, the offline suite and all artifact checks. Record the commit,
  interpreter, lock, workflow result and artifact hashes with the release notes.
- Before enabling publication, create the GitHub `pypi` environment with required
  reviewers and restrict deployment to release tags. Configure a PyPI Trusted
  Publisher (or pending publisher for the first release) for owner `hrabosh`, repo
  `wos-mcp-bridge`, workflow `release.yml`, environment `pypi`. This task has not
  configured either account or environment.
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
  version outside the checkout, and repeat the client/deployment checks appropriate
  to the intended release. Publish release notes with the matching tag and hashes.

The workflow is prepared; no public release, tag push, PyPI project or deployment
has been created by implementing #7.

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

# Application composition and provider request policies

Date: 2026-11-18
Status: implemented for the first refactoring stage
Related: GitHub issues #3 and #5

## Context

Seven read-only MCP tools use three external APIs. The API clients already encapsulate
HTTP operations; the problems were global assembly, implicit client ownership,
cache poisoning, network work blocking cached reads, and inconsistent request limits.
The project needs independent, testable servers and an installable source layout.

## Decision

Keep one Python package with a small number of focused modules. The application
factory assembles a `Services` dataclass containing the concrete WoS client and the
existing literature orchestration service. MCP adapters obtain this typed container
at one framework boundary. Services and provider clients do not import FastMCP.

`Settings` is immutable, loads environment values explicitly and excludes keys from
its representation. No HTTP clients are created by importing the package or building
a server. The lifespan immediately registers each created client's cleanup in an
`AsyncExitStack`. Explicitly supplied services are borrowed; the caller closes them.

Europe PMC and OpenAlex compose a fixed-host literature transport. WoS keeps its
own request/error handling because its response, quota and record-cache contracts
differ. All providers compose the same copying TTL/LRU cache and request-spacing/
cooldown policy. These components have two real consumers, rather than hypothetical
provider extension points. XML parsing and other pure transformations remain functions.

Endpoint-specific parsing and validation finish before cache insertion. Cache capacity
is measured in serialized UTF-8 keys and values, not total Python heap usage. Cache
reads execute without awaiting the upstream lock. Uncached operations double-check
under that lock, then share the first successful response. Cancelling the request
owner cancels its network request; followers can make a replacement request. There
are no detached background tasks to drain or shield at shutdown.

Each provider operation has one deadline covering queueing, request spacing and I/O.
A multi-provider article operation can take longer; an end-to-end MCP deadline belongs
to the later transport/deployment work. Policies remain local to one process and one
credential boundary. They do not coordinate replicas or enforce institutional daily budgets.

## Alternatives and consequences

Patching only the root scripts would keep deployment simple but retain implicit assembly
and brittle constructor monkeypatching. A generic provider interface would hide differences
between WoS and Europe PMC query syntax. A DI container, repository layer or event bus
would add lifecycle and extension mechanisms without current consumers. We use ordinary
constructors, concrete types and standard-library context managers instead.

The `src` layout requires installing the project. Existing `server.py:mcp` hosting remains
supported after adding `pip install .`; direct imports of old internal modules must migrate.
Minimal build metadata and a stdio command are included because the layout requires them;
public release automation, licensing and complete output schemas remain separate issues.

Parsed endpoint results are cached to validate once before storing. This also avoids
reparsing a cached JATS response, but it is not a cross-identifier article cache: resolving
DOI, PMID and PMCID still uses separate provider request keys. That optimization remains #6.

## Evidence

Regression tests exercise invalid JSON/metadata/XML recovery, cooldown, cache independence,
request sharing, cancellation, byte budgets and deadlines. Application tests exercise
independent servers, borrowed ownership, cleanup during partial startup and cancellation,
and delayed environment loading. Existing protocol tests protect all seven tool names,
annotations, validation and JSON/structured-content compatibility.

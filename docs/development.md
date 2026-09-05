# Development

[Documentation index](index.md) · [Contributing](../CONTRIBUTING.md)

## Stack

- **Python ≥3.11**, managed with [uv](https://docs.astral.sh/uv/). The project supports every
  stable, non-EOL CPython release at or above that floor.
- **MCP server**: [FastMCP](https://gofastmcp.com/) (stdio or streamable HTTP)
- **CLI**: [Typer](https://typer.tiangolo.com/) + Rich tables
- **REST transport**: httpx (async), XML parsed with defusedxml
- **CLI passthrough**: [asyncssh](https://asyncssh.readthedocs.io) — tools that
  shell out to HMC CLI commands (`lssyscfg`, `lshwres`, `chsyscfg`, ...) over
  SSH

## Setup

From a source checkout, run `just setup` to install the locked development environment
and repository hooks. Use `uv run --no-sync` before CLI commands in this environment.
See [Contributing](../CONTRIBUTING.md) for the full change and verification workflow.

## Testing

### 1. Unit tests (no HMC needed)

The client and XML parser are tested against an HMC mocked with
[respx](https://lundberg.github.io/respx/) — no real hardware required. The
default command reports only the configured test and coverage result:

```bash
just test
# test: passed; configured coverage gate passed
```

Use `just test-verbose` for native pytest diagnostics and missing-lines coverage.

### 2. MCP protocol smoke test (no HMC needed)

Verifies the MCP handshake in process with a real FastMCP client and reports
the exposed tool count:

```bash
just smoke
# Connected. <N> tools exposed.
```

Use `just smoke-verbose` to list every exposed tool when diagnosing the registry.

### 3. Live check against a real HMC

With credentials configured (TOML profile, env vars, or flags), the cheapest end-to-end check
is `console info` — one Logon + one ManagementConsole GET:

```bash
hmc-mcp console info
hmc-mcp systems list      # should print a table of your Power servers
```

If `console info` prints the HMC version, auth, TLS and the session
lifecycle all work; everything else uses the same path.

## Layout

```
src/hmc_mcp/
  __init__.py    # package version and the `hmc-mcp` console-script entry point
  api.py         # supported reusable-library facade (ADR 0029)
  config.py      # pydantic-settings config (TOML profile + env vars + CLI flags)
  xmlutil.py     # defusedxml Atom-feed -> dict parsing
  errors.py      # HMCError (shared by client and its mixins)
  client/        # HMCClient, domain mixins, response parsing, and PCM payload builders
  resource_identity.py      # managed-system, partition, and VIOS name/UUID resolution
  operations/    # shared workflows; ownership.py owns protocol and name resolution
    lpar/         # LPAR lifecycle, configuration, and DLPAR operations
  server_tools/  # MCP tool adapters grouped by resource family
  cli_commands/  # Typer command groups, CLI policy generation, and shared application state
  snapshots/     # portable LPAR snapshot models, affinity assessment, and operations
  ssh/            # asyncssh transport plus HMC CLI operations by resource family
    ssh/*.py       # transport, shared parsing, and resource-specific commands
  ssh/console.py             # bounded, non-interactive LPAR console capture (mkvterm)
  documents/     # domain XML request builders with shared primitives
  documents/common.py # shared HMC XML envelope helpers and document vocabulary
  jobs.py        # job outcomes, lifecycle helpers, and named job builders
  jobs_requests.py # shared JobRequest XML serialization boundary
  authorization/             # access policy and dispatch-time scope enforcement
  audit/         # audit records plus non-blocking diagnostic transport
  tool_registry.py           # local MCP tool collection, each tool carrying ToolSecurity
  _app.py        # FastMCP factory, sync-run and SSH execution helpers
  server.py      # MCP composition, startup validation, logging, and serving bootstrap
  cli.py         # thin aggregator importing every cli_commands/ registration module
tests/           # pytest + respx, no real HMC needed
scripts/         # repository guardrails, generators, test runners, smoke checks,
                 # and live-test harnesses
```

## Notes on the HMC API

- Auth: `PUT /rest/api/web/Logon` with a LogonRequest XML body returns an
  `X-API-Session` token, sent as a header on every subsequent call;
  `DELETE /rest/api/web/Logon` logs off.
- Resources are Atom feeds of vendor media type
  `application/vnd.ibm.powervm.uom+xml; type=<ResourceType>`.
- `.../quick/<Property>` returns a single property cheaply;
  `.../search/(<Property>==<Value>)` filters server-side.
- State changes are asynchronous **jobs**: POST a JobRequest to
  `/rest/api/uom/<Type>/<uuid>/do/<Operation>`, then poll
  `/rest/api/uom/Job/<job-uuid>`.

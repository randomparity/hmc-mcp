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
  *.py              # package-level modules and composition roots
  api.py             # supported connection/configuration facade (ADR 0118)
  config.py          # TOML profiles, environment values, and CLI configuration
  errors.py          # shared HMC error hierarchy
  resource_identity.py # managed-system, LPAR, and VIOS selector resolution
  audit/             # audit records and non-blocking diagnostic transport
  authorization/     # access policy plus dispatch-time scope enforcement
  client/            # HMCClient transport, response parsing, and domain mixins
  documents/         # domain XML request builders and common envelopes
  jobs/*.py          # job outcome normalization, polling, and request builders
  operations/        # presentation-neutral workflows and authorization policy
    affinity/, inventory/, lpar/, metrics/, storage/
    systems/, templates/, updates/, users/, vios/, virtualization/
                    # domain workflows; see each package for its resource scope
  server_tools/      # MCP adapters: inventory, lpar, metrics, storage, systems,
                     # templates, users, vios, and virtualization
  cli_commands/      # Typer adapters: config, jobs, lpar, metrics, storage, systems,
                     # vios, and virtualization
  snapshots/         # portable LPAR snapshots and affinity assessment
  ssh/*.py           # asyncssh transport and resource-specific HMC CLI commands
  tool_registry.py   # local MCP tool collection and ToolSecurity metadata
  _app.py            # FastMCP factory and shared execution helpers
  server.py          # MCP composition, startup validation, and serving bootstrap
  cli.py             # CLI registration aggregator
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

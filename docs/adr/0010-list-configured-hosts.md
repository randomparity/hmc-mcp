# ADR 0010: Expose Configured Hosts as a Read-Only MCP Tool

## Status

Accepted

## Context

Issues #124–#127 built the platform-native TOML profile loader, CLI config
commands, and per-call profile routing. An agent using the MCP server has no
way to discover which HMC profiles are configured without parsing the config
file itself. Issue #128 adds a discovery tool so agents can enumerate profiles
before choosing a `profile=` argument.

The same issue requires removing the repo-root `.env` documentation pattern
(README and `.env.example`) which contradicts the TOML-first config contract
established by ADR 0006.

## Decision

Add `hmc_list_configured_hosts` as a read-only MCP tool with zero parameters.
It reads the platform-native TOML config file using `tomllib` and returns a
list of safe per-profile metadata dicts. The tool never makes network calls,
never resolves `password_env` environment variables, and never emits password
or key values — only key-presence booleans.

Remove `.env.example` from the repo and update the README to remove the
inline `.env` reference in the *Use with Hermes Agent* section and the stale
`env/.env/flags` comment in the layout table.

## Consequences

- Agents can discover configured profiles with a single MCP tool call before
  issuing any REST or SSH call.
- The tool is placed in `READ_ONLY_TOOLS` and tagged `_READ_ONLY`; the
  capability test already enforces that classification.
- The tool is implemented in `server_system.py` (alongside `hmc_console_info`,
  the closest analogue) — no new module or server.py import chain is needed.
- `.env.example` removal is a breaking change for any user who relied on
  the `.env` workflow; ADR 0006 superseded that workflow and the README
  already documents the TOML-first path.

## Considered & rejected

**Implement in a new `server_config.py` module.** Rejected because the tool is
a single lightweight function; a new file adds an import chain in `server.py`
with no architectural benefit.

**Include merged env-var-override values in the response.** Rejected because
it would require `load_profile` (which resolves `password_env`, demanding the
secret be present), and the tool's value is discovery, not runtime config
inspection. The raw TOML metadata is sufficient.

**Add a `profile=` parameter to select a single profile.** Rejected as YAGNI.
The tool's purpose is enumeration; callers can filter the returned list
themselves.

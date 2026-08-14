# ADR 0010: Expose Configured Hosts as a Read-Only MCP Tool

## Status

Accepted; module ownership superseded by ADR 0013

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
  existing capability test (`test_every_registered_tool_matches_its_category`)
  enforces that classification and catches divergence between the decorator
  tag and the `_app.py` set.
- The tool is implemented in `server_system.py` because a single lightweight
  function does not warrant a new module; avoiding a new `server.py` import
  chain is the governing reason, not a loose analogy to the other tools in
  that file.
- `.env.example` removal is a breaking change for any user who relied on the
  `.env` workflow. ADR 0006 superseded that workflow and the README already
  documents the TOML-first path. The change is accompanied by a PR description
  noting the removal and pointing to `hmc-mcp config init` as the replacement.
  Both changes ship in the same PR because they implement the same
  configuration contract: the discovery tool is only useful once the old
  credential path no longer misleads new users.

## Considered & rejected

**Do nothing / document the TOML file path instead of adding a tool.** An
agent could discover profiles by reading the config file directly via a
filesystem MCP tool, or the operator could supply `profile=` values
manually. Rejected because it requires the agent to know the platform-native
config path, parse TOML, and apply secret-redaction logic itself — every agent
would duplicate that logic. A first-class tool centralises the redaction
contract where it can be tested and enforced.

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

**Expose profiles as an MCP prompt resource instead of a tool.** Rejected
because the issue body specifies a tool, and a prompt resource is passive text
rather than a callable that returns structured data. Agents query tools for
structured results; the prompt surface is for instructions, not inventory.

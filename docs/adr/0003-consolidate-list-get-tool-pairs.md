# ADR 0003: Consolidate List/Get Read-Only Tool Pairs into Unified Tools

## Status

Accepted

## Context

The MCP server exposes 105 tools. Six domains each have a
`hmc_list_<resource>` / `hmc_get_<resource>` pair (plus two LPAR helpers)
where the only difference is whether the caller passes a UUID. An LLM agent
must decide between two tools with nearly identical descriptions for every
lookup. Issue #51 notes that the optional-id pattern already exists in this
codebase (`hmc_list_adapters(adapter_type=...)`, `hmc_list_lpars(system_uuid=None)`)
and asks to apply it to the six pairs, saving 8 tools.

## Decision

Replace each pair with a single tool whose identifier parameter defaults to `None`:

| Old tools | New tool |
|---|---|
| `hmc_list_systems` + `hmc_get_system` | `hmc_systems(system_uuid=None)` |
| `hmc_list_lpars` + `hmc_get_lpar` + `hmc_find_lpar` + `hmc_lpar_state` | `hmc_lpars(system_uuid=None, lpar_uuid=None, name=None, state_only=False)` |
| `hmc_list_vios` + `hmc_vios_mappings` | `hmc_vios(system_uuid=None, vios_uuid=None)` |
| `hmc_list_shared_storage_pools` + `hmc_get_shared_storage_pool` | `hmc_shared_storage_pools(ssp_uuid=None)` |
| `hmc_list_partition_templates` + `hmc_get_partition_template` | `hmc_partition_templates(template_uuid=None)` |
| `hmc_list_users` + `hmc_get_user` | `hmc_users(name=None, user_type="all")` |

All merged tools remain `_READ_ONLY` and in `READ_ONLY_TOOLS`. No client-layer
methods change. No CLI commands change.

## Consequences

- Tool count drops from 105 to 97.
- Each merged tool has a polymorphic return type (`list | dict | str | None`
  depending on arguments); the Python annotation uses `Any` which renders as a
  generic MCP schema.
- The `state_only` flag in `hmc_lpars` preserves access to the cheap
  quick-property endpoint; callers that need only the state pass
  `lpar_uuid=<uuid>, state_only=True`.
- Tests, `READ_ONLY_TOOLS`, `server.py` re-exports, and README all update.
- Downstream callers using the old tool names will need to migrate (breaking
  change to the MCP surface).

## Considered & Rejected

**Keep separate tools.** The issue explicitly requests the merge, and the
optional-id pattern is already established in the codebase. Keeping them
separate preserves backward compatibility but leaves the tool count high and
forces agents to choose between two near-identical tools on every read.

**Fold `hmc_find_lpar` and `hmc_lpar_state` into a separate tool** (not merging
into `hmc_lpars`). The issue body explicitly proposes merging all four LPAR reads
into one tool. Keeping the helpers separate preserves the cheap-endpoint path but
adds a tool the issue says to remove.

**Tier 3 get/set merges.** Excluded per issue body: merging a read with a write
drops `readOnlyHint` and loses auto-approval; the accidental-write footgun risk is
real when an LLM echoes a read value.

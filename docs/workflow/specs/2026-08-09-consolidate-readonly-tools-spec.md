# Spec: Consolidate Read-Only List/Get MCP Tools (Issue #51)

**Branch:** feat/consolidate-readonly-tools-51
**BASE_BRANCH:** main
**Guardrails:** `just verify` (ruff + ty + detect-secrets + zizmor + pytest + smoke_mcp.py + CLI groups)
**ADR:** [docs/adr/0003-consolidate-list-get-tool-pairs.md](../../adr/0003-consolidate-list-get-tool-pairs.md)

## Outcome

Merge 6 read-only list/get tool pairs into single tools with optional
identifiers, reducing the exposed tool count from 105 to 97 (saving 8 tools)
with no capability-annotation loss. All merged tools remain `_READ_ONLY` and
appear in `READ_ONLY_TOOLS`.

## Completion Criteria

| # | Criterion | Source |
|---|---|---|
| 1 | `hmc_list_systems` + `hmc_get_system` → `hmc_systems(system_uuid=None)` | issue #51 body |
| 2 | `hmc_list_lpars` + `hmc_get_lpar` + `hmc_find_lpar` + `hmc_lpar_state` → `hmc_lpars(system_uuid=None, lpar_uuid=None, name=None, state_only=False)` | issue #51 body |
| 3 | `hmc_list_vios` + `hmc_vios_mappings` → `hmc_vios(system_uuid=None, vios_uuid=None)` | issue #51 body |
| 4 | `hmc_list_shared_storage_pools` + `hmc_get_shared_storage_pool` → `hmc_shared_storage_pools(ssp_uuid=None)` | issue #51 body |
| 5 | `hmc_list_partition_templates` + `hmc_get_partition_template` → `hmc_partition_templates(template_uuid=None)` | issue #51 body |
| 6 | `hmc_list_users` + `hmc_get_user` → `hmc_users(name=None, user_type="all")` | issue #51 body |
| 7 | All 6 merged tools remain in `READ_ONLY_TOOLS` frozenset; `test_capabilities.py` passes | issue #51 body |
| 8 | `_app.py` `READ_ONLY_TOOLS` updated: 8 old names removed, 6 new names added | issue #51 body |
| 9 | `server.py` re-export list updated | project convention |
| 10 | Domain tests updated to import/call new function names | issue #51 body |
| 11 | README tool tables updated to 97 tools with new names | issue #51 body |
| 12 | `just verify` passes (all guardrails green) | project convention |

## Exclusions

- Tier 3 get/set merges (`hmc_get/set_lpar_description`, `hmc_get/set_lpar_msp`, etc.) — explicitly out of scope in issue body; merging a read with a write drops `readOnlyHint`
- Power on/off pairs — state-changing, not read-only
- Backup/restore pairs — `_DESTRUCTIVE`

## Unified Tool Signatures

### `hmc_systems(system_uuid=None)` → list | dict | None

- `system_uuid=None`: returns `list[dict]` — all managed systems (current `hmc_list_systems`)
- `system_uuid=<uuid>`: returns `dict | None` — one system (current `hmc_get_system`)

### `hmc_lpars(system_uuid=None, lpar_uuid=None, name=None, state_only=False)` → list | dict | str | None

Priority resolution (only one "get" mode can be active at a time):
1. `lpar_uuid` provided + `state_only=True` → `str | None` — quick property (current `hmc_lpar_state`)
2. `lpar_uuid` provided → `dict | None` — one LPAR (current `hmc_get_lpar`)
3. `name` provided → `dict | None` — find by name (current `hmc_find_lpar`)
4. `system_uuid` provided → `list[dict]` — system-scoped list (current `hmc_list_lpars(system_uuid)`)
5. No UUID or name → `list[dict]` — all LPARs (current `hmc_list_lpars()`)

### `hmc_vios(system_uuid=None, vios_uuid=None)` → list | dict | None

- `vios_uuid` provided → `dict | None` — VIOS storage-detail mappings (current `hmc_vios_mappings`)
- `vios_uuid=None` → `list[dict]` — list all VIOS, optionally scoped by `system_uuid` (current `hmc_list_vios`)

### `hmc_shared_storage_pools(ssp_uuid=None)` → list | dict | None

- `ssp_uuid` provided → `dict | None` — one SSP (current `hmc_get_shared_storage_pool`)
- `ssp_uuid=None` → `list[dict]` — all SSPs (current `hmc_list_shared_storage_pools`)

### `hmc_partition_templates(template_uuid=None)` → list | dict | None

- `template_uuid` provided → `dict | None` — one template (current `hmc_get_partition_template`)
- `template_uuid=None` → `list[dict]` — all templates (current `hmc_list_partition_templates`)

### `hmc_users(name=None, user_type="all")` → list | dict | None

- `name` provided → `dict | None` — one user by username (current `hmc_get_user`); `user_type` ignored when `name` is set
- `name=None` → `list[dict]` — all users filtered by `user_type` (current `hmc_list_users`)

## Return-Type Polymorphism

Each merged tool returns different types depending on its dispatch path. This
is intentional and matches how the existing optional-id pattern works in this
codebase (e.g. `hmc_list_adapters(lpar_uuid, adapter_type=...)` already shows
the pattern — and `hmc_list_lpars(system_uuid=None)` already has an optional
arg). The MCP tool schema must describe this polymorphism via an `anyOf` or
union type in its return annotation. Using `Any` in the Python annotation
avoids runtime type errors and lets FastMCP render a generic schema.

## Files Changed

| File | Change |
|---|---|
| `src/hmc_mcp/server_tools/system.py` | Replace 7 tools with 2: `hmc_systems`, `hmc_lpars` (absorbing `hmc_list_systems`, `hmc_get_system`, `hmc_list_lpars`, `hmc_get_lpar`, `hmc_find_lpar`, `hmc_lpar_state`, and moving `hmc_vios_mappings` away; `hmc_list_vios` + `hmc_vios_mappings` → `hmc_vios` in this file) |
| `src/hmc_mcp/server_tools/storage.py` | Replace 2 tools with 1: `hmc_shared_storage_pools` (absorbs `hmc_list_shared_storage_pools` + `hmc_get_shared_storage_pool`) |
| `src/hmc_mcp/server_tools/templates.py` | Replace 2 tools with 1: `hmc_partition_templates` (absorbs list + get) |
| `src/hmc_mcp/server_tools/users.py` | Replace 2 tools with 1: `hmc_users` (absorbs `hmc_list_users` + `hmc_get_user`) |
| `src/hmc_mcp/_app.py` | Update `READ_ONLY_TOOLS` frozenset |
| `src/hmc_mcp/server.py` | Update re-exports |
| `tests/system/test_system_tools.py` | Update imports and test function names |
| `tests/storage/test_storage_tools.py` | Update SSP test functions |
| `tests/app/test_template_tools.py` | Update template test functions |
| `tests/security/test_users.py` | Update user test functions |
| `tests/app/test_capabilities.py` | Verify frozenset still contains new names |
| `tests/app/test_server_tools.py` | Update import of `hmc_find_lpar` → remove; add `hmc_lpars` |
| `README.md` | Update tool tables (97 tools, new names) |

## Edge Cases

- `hmc_lpars` with both `lpar_uuid` and `name` provided: `lpar_uuid` takes priority
- `hmc_lpars` with `state_only=True` but no `lpar_uuid`: raise `ValueError`
- `hmc_vios` with `vios_uuid` and `system_uuid` both provided: `vios_uuid` takes priority (returns storage-detail for the specified VIOS, `system_uuid` is ignored)
- `hmc_users` with `name` provided: `user_type` is silently ignored (consistent with other priority patterns)

## Non-Goals

- No new client-layer methods are added; all client methods remain unchanged
- No change to CLI commands (`cli_commands/systems.py`, etc.)
- No change to `_DESTRUCTIVE` tools
- No behavioral change in any currently working path

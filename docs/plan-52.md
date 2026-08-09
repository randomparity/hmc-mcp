# Implementation Plan: Issue #52 — Consolidate tool pairs (update/upgrade + metrics links/fetch)

## Context

- **Branch:** `feat/consolidate-tool-pairs-52`
- **BASE_BRANCH:** `main`
- **ADR:** `docs/adr/0004-consolidate-update-upgrade-and-metrics-tool-pairs.md`
- **Guardrails:** `just verify` (runs `just static test smoke` + CLI help checks)
- **Interaction:** interactive

## Merges

| Old pair | New tool | Annotation |
|---|---|---|
| `hmc_update_hmc` + `hmc_upgrade_hmc` | `hmc_hmc_update(system_uuid, repository, kind="update"\|"upgrade")` | untagged |
| `hmc_update_vios` + `hmc_upgrade_vios` | `hmc_vios_update(vios_uuid, repository, kind="update"\|"upgrade")` | untagged |
| `hmc_get_processed_metric_links` + `hmc_get_processed_metrics` | `hmc_processed_metrics(..., mode="links"\|"fetch")` | `_READ_ONLY` |
| `hmc_get_aggregated_metric_links` + `hmc_get_aggregated_metrics` | `hmc_aggregated_metrics(..., mode="links"\|"fetch")` | `_READ_ONLY` |

## Tasks

### Task 1: Write failing tests (TDD red phase)

**File:** `tests/app/test_server_tools.py`
- Replace imports of `hmc_update_hmc`, `hmc_upgrade_hmc`, `hmc_update_vios`, `hmc_upgrade_vios` with `hmc_hmc_update`, `hmc_vios_update`
- Rewrite `test_update_hmc_submits_job` → `test_hmc_update_kind_update`
- Rewrite `test_upgrade_hmc_submits_job` → `test_hmc_update_kind_upgrade`
- Rewrite `test_update_vios_submits_job` → `test_vios_update_kind_update`
- Rewrite `test_upgrade_vios_submits_job` → `test_vios_update_kind_upgrade`
- Add `test_hmc_update_default_kind_is_update` and `test_vios_update_default_kind_is_update`

**File:** `tests/unit/test_pcm.py`
- Replace imports of four old metric tool names with `hmc_processed_metrics`, `hmc_aggregated_metrics`
- Rewrite all metrics tests to use new names and `mode=` parameter
- Preserve the same behavioural assertions

### Task 2: Implement merged tools in `server_updates.py`

- Remove `hmc_update_hmc` and `hmc_upgrade_hmc`
- Add `hmc_hmc_update(system_uuid, repository, kind: Literal["update", "upgrade"] = "update")`
  - Dispatches to `update_hmc_job` or `upgrade_hmc_job` based on `kind`
  - Submits to `ManagementConsole/{system_uuid}/do/Update` or `.../do/Upgrade`
  - Raises `ValueError` for unknown `kind` (defensive guard)
- Remove `hmc_update_vios` and `hmc_upgrade_vios`
- Add `hmc_vios_update(vios_uuid, repository, kind: Literal["update", "upgrade"] = "update")`
  - Same pattern for `VirtualIOServer`
- Retain `hmc_update_firmware` and `hmc_get_available_hmc_ptfs` unchanged

### Task 3: Implement merged tools in `server_metrics.py`

- Remove `hmc_get_processed_metric_links` and `hmc_get_processed_metrics`
- Add `hmc_processed_metrics(category, resource_uuid, start_ts, end_ts=None, no_of_samples=None, mode: Literal["links", "fetch"] = "fetch")`
  - `mode="links"` → returns `_metrics_links(...)` result
  - `mode="fetch"` → returns `_metrics_fetch(...)` result
  - Decorated with `@mcp.tool(annotations=_READ_ONLY)`
- Remove `hmc_get_aggregated_metric_links` and `hmc_get_aggregated_metrics`
- Add `hmc_aggregated_metrics(category, resource_uuid, start_ts, end_ts=None, no_of_samples=None, mode: Literal["links", "fetch"] = "fetch")`
  - Same pattern for aggregated
  - Decorated with `@mcp.tool(annotations=_READ_ONLY)`
- Keep private helpers `_fetch_metric_links`, `_metrics_links`, `_metrics_fetch` unchanged

### Task 4: Update `_app.py` frozensets

- In `READ_ONLY_TOOLS`, replace the four old metric tool names with:
  - `"hmc_processed_metrics"`
  - `"hmc_aggregated_metrics"`

### Task 5: Update `server.py` re-exports

- In the `from .server_updates import` block: remove old four update tool names, add `hmc_hmc_update`, `hmc_vios_update`
- In the `from .server_metrics import` block: remove old four metric tool names, add `hmc_processed_metrics`, `hmc_aggregated_metrics`

### Task 6: Update README tool tables

- Replace the four old update tool rows with two merged rows
- Replace the four old metric tool rows with two merged rows

### Task 7: Commit ADR, verify guardrails green

```sh
git add docs/adr/0004-consolidate-update-upgrade-and-metrics-tool-pairs.md
git commit -m "docs: add ADR 0004 for update/upgrade and metrics tool pair consolidation"
```

Then implement tasks 1-6 and commit implementation.
Run `just verify` — must pass before pushing.

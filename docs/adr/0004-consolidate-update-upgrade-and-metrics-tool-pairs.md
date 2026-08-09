# ADR 0004: Consolidate Update/Upgrade and Metrics Links/Fetch Tool Pairs

## Status

Accepted

## Context

The MCP server exposes 101 tools after the ADR 0003 (issue #51) list/get
consolidation. Four same-category pairs remain that can be merged with no
capability-annotation loss:

- `hmc_update_hmc` + `hmc_upgrade_hmc` — both untagged (state-changing), both
  accept the same `(system_uuid, repository)` arguments, differ only in whether
  the HMC operation is `Update` (PTF install) or `Upgrade` (full version).
- `hmc_update_vios` + `hmc_upgrade_vios` — same pattern for VIOS targets.
- `hmc_get_processed_metric_links` + `hmc_get_processed_metrics` — both
  `_READ_ONLY`, both accept the same `(category, resource_uuid, start_ts,
  end_ts, no_of_samples)` arguments, differ only in whether the result is the
  raw link list or the fetched JSON document.
- `hmc_get_aggregated_metric_links` + `hmc_get_aggregated_metrics` — same
  pattern for the aggregated metrics endpoint.

Issue #52 requests this second tranche of consolidation, saving 4 tools
(101 → 97).

## Decision

Replace each pair with a single tool carrying a discriminating `kind` or `mode`
parameter:

| Old tools | New tool |
|---|---|
| `hmc_update_hmc` + `hmc_upgrade_hmc` | `hmc_hmc_update(system_uuid, repository, kind="update"\|"upgrade")` |
| `hmc_update_vios` + `hmc_upgrade_vios` | `hmc_vios_update(vios_uuid, repository, kind="update"\|"upgrade")` |
| `hmc_get_processed_metric_links` + `hmc_get_processed_metrics` | `hmc_processed_metrics(category, resource_uuid, start_ts, end_ts=None, no_of_samples=None, mode="links"\|"fetch")` |
| `hmc_get_aggregated_metric_links` + `hmc_get_aggregated_metrics` | `hmc_aggregated_metrics(category, resource_uuid, start_ts, end_ts=None, no_of_samples=None, mode="links"\|"fetch")` |

Annotation rules:
- `hmc_hmc_update` and `hmc_vios_update` are untagged (state-changing) — no
  annotation loss.
- `hmc_processed_metrics` and `hmc_aggregated_metrics` carry `_READ_ONLY` — no
  annotation loss; both source tools were already `_READ_ONLY`.

The `kind` default is `"update"` (PTF install is the more common path); the
`mode` default is `"fetch"` (callers that want the data, not the link list, are
the common case).

## Consequences

- Tool count drops from 101 to 97.
- `hmc_processed_metrics` and `hmc_aggregated_metrics` have a polymorphic
  return type (`list[dict]` for `mode="links"`, `dict` for `mode="fetch"`); the
  Python annotation uses `list[dict[str, str]] | dict[str, Any]`.
- `_app.py` `READ_ONLY_TOOLS` frozenset replaces the four old metric tool names
  with the two new ones.
- `server.py` re-exports updated to expose the four new names and stop
  exporting the eight old names.
- Tests updated: `tests/app/test_server_tools.py` (update/upgrade) and
  `tests/unit/test_pcm.py` (metrics) use the new tool names and `kind`/`mode`
  parameters.
- README tool tables updated.
- Downstream callers using the old tool names will need to migrate (breaking
  change to the MCP surface).

## Considered & Rejected

**Keep separate tools.** Preserves backward compatibility but leaves four
near-duplicate pairs that force agents to choose between two tools with nearly
identical descriptions. The `kind` / `mode` parameter makes the distinction
explicit at call time.

**Use `operation` as the discriminating parameter name for update/upgrade.**
`kind` matches the ADR 0003 precedent and is shorter. The issue body explicitly
uses `kind` for the update pair and `mode` for the metrics pair; this ADR
follows those names.

**Merge metrics links/fetch with PCM preferences.** `hmc_get_pcm_preferences`
and `hmc_set_pcm_preferences` are a get/set pair — merging a read with a write
drops `readOnlyHint` as established in ADR 0003's Tier 3 exclusion.

**Tier 3 merges (get/set, power on/off, backup/restore).** Excluded per issue
body referencing #51: merging a read with a write drops `readOnlyHint` and
creates an accidental-write footgun.

# 0012 — Stable public tool contracts

## Status

Accepted (2026-08-14)

## Context

Earlier consolidation reduced the MCP tool count by combining collection,
single-resource lookup, metric discovery, and metric fetching behind optional
selectors and mode flags. Those tools returned incompatible shapes depending
on an argument: a list for one call and an object, string, or `None` for
another. Agents and generated MCP schemas could not determine a call's result
shape from its tool name and output schema alone.

The runtime now also validates decoded PCM documents as JSON objects, so the
declared metric-fetch result can be enforced rather than merely asserted.

## Decision

Each public MCP tool represents one operation with one stable result shape.
Selector parameters may narrow a collection but must not switch a collection
tool into a single-resource lookup or change the output kind.

- Collection operations use the `hmc_list_<resource>` grammar and always
  return collections. System, state, or other collection filters retain the
  list shape. This includes `hmc_list_systems`, `hmc_list_lpars`,
  `hmc_list_vios`, `hmc_list_shared_storage_pools`,
  `hmc_list_partition_templates`, `hmc_list_users`, and
  `hmc_list_recent_jobs`.
- Single-resource operations have explicit names:
  `hmc_get_system`, `hmc_get_lpar`, `hmc_get_vios`, and
  `hmc_get_partition_template`.
- `hmc_processed_metric_links` and `hmc_aggregated_metric_links` return metric
  link collections. `hmc_processed_metrics` and `hmc_aggregated_metrics`
  fetch and return one JSON object, or an empty object when no retained
  document is available.
- A discriminator remains acceptable when every variant has the same result
  shape and capability annotation, as with update-versus-upgrade operations.
- New tools must encode materially different operations or result shapes in
  their names instead of adding modes that produce incompatible schemas. New
  collection and lookup tools use `hmc_list_<resource>` and
  `hmc_get_<resource>` respectively.

This decision supersedes ADR 0003's optional-identifier list/get merges and
ADR 0004's metric `mode` merges. It retains the useful constraint that reads
and writes with different capability annotations are separate tools.

## Consequences

MCP clients can select a tool and validate its output without interpreting an
input-dependent union. Adding explicit lookup and metric-discovery tools grows
the tool list, but each entry has a narrower contract and a useful generated
schema. Callers of the former optional-selector and metric-mode APIs must use
the explicit replacement tool; no compatibility shims are retained.

Contract tests must pin collection shapes, single-resource shapes, metric
schemas, and decoded PCM object validation. Documentation and live-runner
calls must use the same explicit operation names.

## Considered & rejected

**Keep polymorphic tools and document each mode.** Documentation cannot make a
single generated output schema precise when the result kind depends on an
input value.

**Return an envelope from every operation.** A universal envelope would add
noise to simple reads and require a broad public migration. Explicit operation
names solve the ambiguity without imposing a new wrapper on unrelated tools.

**Retain the former names as compatibility aliases.** Two public mechanisms
for the same operation would preserve the ambiguity and expand the defect
surface. This repository replaces obsolete contracts rather than deprecating
them in parallel.

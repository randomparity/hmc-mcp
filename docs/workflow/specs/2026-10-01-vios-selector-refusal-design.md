# Refuse a VIOS name in the install-by-LPAR selector

**Issue:** #1247 (part of #1243) · **Branch:** `feat/vios-selector-refusal-1247` ·
**Governing record:** ADR 0092 §3.4a (amended in prose; no new ADR)

## Problem

`install_vios_by_lpar_selector` resolves its target through the `LogicalPartition` feed
(`resolve_lpar_uuid`). A VIOS-type partition is listed only in the `VirtualIOServer` feed
(#1202), so naming one fails with "No LPAR named …", which points the caller at
`hmc_list_lpars` instead of `hmc_install_vios`, the tool that can install it.

The operator chose on 2026-10-01 to **refuse with a pointer, not widen** the selector.

## Design

The refusal lives in the operation layer, so the MCP tool, the facade, and any other
caller share it. A private resolver `_resolve_lpar_selector_target(hmc, value, *,
system_name_or_uuid)` in `operations/vios/install.py` replaces `resolve_lpar_uuid` as the
selector's resolver argument to `_submit_install`; it satisfies `_TargetResolver`:

1. Return `resolve_lpar_uuid(...)` unchanged on a hit, and for any UUID selector (which
   `resolve_lpar_uuid` passes through without a lookup).
2. On `ResourceNotFoundError`, call `hmc.find_vios_by_name(value, system_uuid=...)` with the
   already-resolved managed-system UUID `_submit_install` passes.
3. A VIOS entry → raise `ResourceNotFoundError("LPAR", value, message)` chained from the
   miss. The message names the value, says it is a Virtual I/O Server the selector cannot
   target because it resolves only `LogicalPartition`-feed partitions, and names
   `hmc_install_vios`.
4. No VIOS entry → re-raise the original miss, so its "No LPAR named …" text is unchanged.

The refusal is raised before `_validate_install_target`'s read, the CLI-name lookup, the
audit record, and SSH submission. It is a `ValueError` subclass, which the operation's
documented `Raises` already covers. Both docstrings (operation and MCP tool) state that the
selector targets only `LogicalPartition`-feed partitions and that a VIOS name is refused
with that pointer; `just tool-docs` regenerates `docs/tools/`. ADR 0092 §3.4a's selector row
gains one sentence recording the refusal; the adjacent `install_vios` row's stale
"checked through the resolved `LogicalPartition` resource" becomes `VirtualIOServer`, the
read 79e24009 made it use (same #1202 feed split).

## Failure model

1. **Actors and deployments** — an MCP client or facade caller with install authority, against
   an HMC whose `LogicalPartition` feed omits VIOS partitions (V10R3 per #1202).
2. **Invariants and assets at stake** — no `installios` submission or audit record on a refused
   name; the public tool contract (error text and description) for both outcomes.
3. **Accepted failure classes**
   - A VIOS **UUID** passed to the selector is not probed: `resolve_lpar_uuid` passes it
     through and the `LogicalPartition/{uuid}` read fails as it does today. The operator
     decision covers the name miss; the live 404 shape is #1202's.
   - A failed or ambiguous VIOS-feed read during the probe propagates its own error in place
     of the "No LPAR named" miss. Truthful, and nothing is submitted.
4. **Covered elsewhere** — widening the selector, merging `hmc_install_vios`, and the VIOS
   ownership model (unowned exclusions); SSH name resolution (#1203).

## Success

- A VIOS name on the selector's system raises the pointer error and submits nothing.
- A name in neither feed raises the unchanged "No LPAR named" error.
- An LPAR-feed hit is unaffected (existing tests stay green).

## Validation

- **Refusal (operation)** — focused-test: `tests/unit/test_install_operations.py`, a VIOS
  name with `find_partition_by_name → None` raises `ValueError` matching `hmc_install_vios`,
  with no SSH command and no target read. Red before the resolver exists.
- **Double miss** — focused-test: the existing `No LPAR named` case gains
  `find_vios_by_name → None` for the selector row.
- **Tool path** — focused-test: `tests/lpar/test_lpar_install.py`, respx serves an empty
  `LogicalPartition` feed and a `VirtualIOServer` feed listing the name; the tool raises the
  pointer and `run_installios` is never called. The existing unknown-name tool test gains an
  empty `VirtualIOServer` route, since the probe is a new read respx must answer; it then
  proves the double miss on the tool path.
- **Docs** — `just tool-docs-check` diffs regenerated `docs/tools/`;
  `tests/unit/test_adr_0092_citations.py` checks the §3.4a rows' `path.py::symbol`
  citations. CHANGELOG is task-test-not-applicable (no executable consumer of its wording).

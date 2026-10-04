# 0200 — LPAR inspection reads one partition and reports each section on its own

## Status

Accepted (2026-10-03), issue #1224. Partially supersedes ADR 0189 Decision 2 for
`hmc_inspect_lpar` only, as ADR 0196 does for `hmc_inventory`, and replaces the H1 spec's
inspect row. ADR 0189 otherwise stands, including Decision 5: inspection never captures a
console.

## Context

The H1 spec gives `hmc_inspect_lpar` the inputs `lpar` and `include` ⊆ {`resources`, `rmc`,
`profile_drift`, `refcodes`}, and delegates it to `hmc_get_lpar`, `hmc_get_lpar_state` and
`hmc_read_lpar_refcodes`, plus `hmc_get_vios_storage_detail` for each serving VIOS and #637's
profile read. Four facts shape the tool:

- One partition document carries the state, the RMC state, the partition ID and the memory and
  processor figures (`operations/inventory/composite.py` `_lpar_summary`). `hmc_get_lpar_state`
  reads one of those fields again.
- A client vSCSI adapter names its VIOS only by partition ID, and `hmc_get_vios_storage_detail`
  takes a VIOS name or UUID, so any route to the storage needs the system's VIOS list.
  Decommission reads every VIOS's mappings and keeps the ones that name the partition
  (`operations/lpar/decommission.py` `_inventory_storage_mappings`).
- No read-only partition-profile tool exists. The one profile read in the code runs inside
  `hmc_power_on_lpar` (`operations/lpar/core.py`), a `mutate` tool. #637 is open.
- The issue asks that busy, unsupported, unknown and failed be told apart from healthy.

## Decision

1. **The base read is required.** `hmc_inspect_lpar` reads the partition from its system's
   partition list, authorized as `hmc_get_lpar` for the caller's `lpar_name_or_uuid` and
   `system_name_or_uuid`. If the policy withholds `hmc_get_lpar` or denies that target, the
   call is refused: there is nothing to inspect. `system_name_or_uuid` is required, as in
   ADR 0199, and the partition must be in that system's list.
2. **Each source is authorized and reported on its own** (as in ADR 0196). Each included
   section carries a source status, `ok`, `unavailable` or `denied`, with the tool and bounded
   detail. A denied or failed source returns none of its data, and the other sections still
   answer. State, `rmc` and the `resources` figures come from the base read and need no other
   tool. `refcodes` is authorized as `hmc_read_lpar_refcodes`. The `resources` storage is
   authorized as `hmc_list_vios` for the system and then as `hmc_get_vios_storage_detail` for
   each VIOS by UUID; like decommission it reads every VIOS, so it also finds a mapping whose
   client adapter is gone. `include` defaults to `rmc` and `refcodes`, the two that answer a
   boot question; `resources` costs up to 18 reads and audit records, so it is asked for.
3. **`hmc_get_lpar_state` is not delegated.** Its one field comes from the base read.
4. **`profile_drift` is always `unavailable`** until #637 ships a read-only profile read.
   Nothing is read, and no mutating or destructive tool is used for a read.
5. **`next_actions` names tools only, and only tools the policy's capability ceiling
   admits;** a target grant may still deny one for this partition. It is chosen from the state
   and RMC alone. `hmc_capture_lpar_console` is named, never called.
6. **Registration:** `effect="read"`, `operation="lpar.inspect"`, `target_kind="lpar"`, with
   both selectors required and not exhaustive. The VIOS reads sit below the signature, so only
   an `all-targets` grant admits the tool itself and the delegated tools carry the target
   bound, as for `hmc_plan_lpar`.

## Consequences

- A policy can admit a partition and withhold some VIOSes. Those VIOSes report `denied`, the
  storage source is `denied`, and the mappings cover only the VIOSes that were read. That holds
  even for a VIOS that serves another partition only: inspection cannot tell without reading it.
- A policy that bounds `hmc_get_vios_storage_detail` must list VIOSes by UUID, because each VIOS
  is admitted by its UUID and target scope matches spellings literally (as in ADR 0198).
- Each delegated decision writes its own ADR 0040 record. A call with `resources` writes one
  for `hmc_inspect_lpar` itself, one for the base read, one for the VIOS list, one per VIOS (up
  to 16) and one for refcodes.
- `refcodes` needs the SSH transport. Without SSH it is `unavailable`, and the rest answers.
- When #637 lands, `profile_drift` needs its read added to Decision 2 and a test.

## Considered & rejected

- **All-or-nothing over every delegated tool (ADR 0189 Decision 2).** judgment: fit. It turns a
  withheld VIOS read into a refused inspection and makes `denied` unreachable. ADR 0196
  rejected it for inventory on the same ground.
- **Find the serving VIOSes from the client adapters.** judgment: fit. It reads fewer VIOSes
  and lets a policy scoped to the serving VIOSes answer `ok`, but it adds `hmc_list_adapters`
  and misses a server-side mapping whose client adapter is gone, which is the kind of
  misconfiguration inspection exists to show.
- **Read the profile through `hmc_power_on_lpar`'s contained-profile check.** judgment: fit.
  That would authorize a read as a `mutate` tool, which a read-only policy cannot grant.
- **Split `profile_drift` into a later PR.** judgment: cost. Reporting it as `unavailable`
  keeps the contract stable now and needs one change when #637 lands.

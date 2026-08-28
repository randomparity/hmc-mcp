# ADR 0105: Explicit VIOS FC label administration

## Status

Accepted (2026-08-28)

## Context

IBM POWER10 and POWER11 expose VIOS label administration through `lslabelvios` and
`labelvios`. FC-port labels identify equivalent physical FC paths across systems, while vFC
group labels constrain VIOS selection for virtual Fibre Channel storage during partition
migration and remote restart. The existing adapter tools instead create and delete an LPAR's
virtual adapter by VIOS partition ID, slot, and adapter UUID. Treating a label as an adapter
selector would combine two HMC contracts with different identities and lifecycles.

The HMC commands also cover MSP, vNIC, vSCSI, override defaults, and resource-wide deletion.
Issue #556 authorizes only individual FC-port and vFC-group label administration.

## Decision

Add a dedicated SSH-backed VIOS label module and explicit MCP and CLI operations for:

- listing, setting, and removing individual FC-port labels; and
- listing, creating, changing membership, renaming, and removing individual vFC group labels.

FC-port set and removal name exactly one port and one VIOS identity. Group creation and membership
changes accept exactly one non-empty selector family, VIOS names or VIOS IDs, containing one or
more identities; rename and individual group removal name only the group label. Update actions are
explicit rather than encoded in caller-provided `labelvios` attributes. The implementation
constructs only the documented `resource=fcport` and `resource=vfc` records through the existing
validating attribute-record builder, validates every standalone command argument at its trust
boundary, then shell-quotes the completed arguments. It returns structured list rows or a
structured mutation receipt.

Existing vFC adapter operations remain unchanged. The new functions are internal implementation
interfaces, not additions to the reusable `hmc_mcp.api` facade.

## Consequences

Operators can administer the labels that HMC migration and remote-restart placement consumes
without using the arbitrary-command escape hatch. Access policies can grant reads, ordinary
mutations, and removals independently through the existing effect classes. Generated MCP schemas
remain explicit about each operation and do not expose the wider `labelvios` grammar.

The feature depends on SSH access to the HMC; the REST references document consumption of label
overrides by migration jobs but provide no general label-management resource. Label management
does not make adapter add/delete label-aware. A separately authorized HMC operator may race a
request, and this change does not add transactions or rollback around HMC state.

## Considered & rejected

- **Use labels as selectors for existing adapter add/delete operations.** verified: the POWER10
  and POWER11 `labelvios` manuals state that FC-port and VIOS group labels govern partition
  migration and remote restart, while this repository's adapter operations address VIOS IDs,
  slots, and adapter UUIDs (`95bf414f3d11135cb6f50770ff3291fe0fece3b1`,
  `src/hmc_mcp/server_tools/adapters.py`). Combining them would invent HMC behavior.
- **Expose one generic `labelvios` tool.** judgment: it would publish unrelated MSP, vNIC,
  vSCSI, default-override, and bulk-removal grammar and make authorization coarser than the
  approved task.
- **Use REST for label administration.** verified: the captured POWER10 and POWER11 REST job
  references describe label override parameters for migration and remote restart but no general
  label-management endpoint; both command references document `labelvios` and `lslabelvios`.
- **Do nothing and retain the arbitrary-command escape hatch.** judgment: it provides no typed,
  bounded, independently authorizable surface for the daily operation requested by issue #556.

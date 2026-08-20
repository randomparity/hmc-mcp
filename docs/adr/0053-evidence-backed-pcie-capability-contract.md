# ADR 0053: Evidence-backed PCIe capability contract

## Status

Accepted

## Context

The repository exposes raw physical-slot rows and a profile-only slot append, while its SR-IOV
surface does not enumerate adapters, physical ports, or logical ports. Downstream inventory and
mutation work needs one falsifiable interpretation of HMC CLI identities, capacity units,
partition state, and unsupported capability behavior. IBM's Power8 through Power11 command
references agree on the resource families but add fields over time, so treating an unlabelled
sample or absent field as a stable contract would make version drift indistinguishable from an
empty resource.

## Decision

Repository fixtures are version-labelled evidence records: each names the IBM documentation
family, resource command, selected fields, and sanitized output. Parsers consume only explicit
`-F` column order and retain empty columns. Stable identities are:

- dedicated slot: managed-system identity plus `drc_index`;
- SR-IOV adapter: managed-system identity plus integer `adapter_id`;
- physical port: adapter identity plus integer `phys_port_id`;
- logical port: adapter identity plus `logical_port_id`, the DRC index documented by IBM.

Names, location codes, MAC addresses, and partition names are attributes, not identities.
SR-IOV `capacity`, `max_capacity`, and physical-port minimum granularities are percentages with
up to two decimal places. They are never bytes, bandwidth, or whole-number weights.

Read commands may report capability unavailable distinctly from an empty result. Unsupported
resource/subtype or selected-field failures are capability-unavailable; successful zero-row
output is an available empty collection; malformed successful rows are contract errors. The
contract records but does not implement mutation. Dynamic logical-port operations use
`chhwres -r sriov --rsubtype logport`; profile/create-time logical ports use the documented
`sriov_eth_logical_ports` / `sriov_roce_logical_ports` profile attributes; dedicated slots use
dynamic `chhwres -r io --rsubtype slot` only where the HMC permits DLPAR and otherwise change
`io_slots` in the profile. A profile change never claims to alter effective running state.

## Consequences

Downstream code can normalize inventories without guessing identifiers or units, and it can
fail closed when a required capability or selected field is unavailable. Version additions are
additive fixture cases rather than changes to identity. Mutation code must select dynamic versus
profile paths from the documented state matrix and verify the same identity fields after each
operation. This issue provides no live-mutation proof and no public mutation API.

## Considered & rejected

- **Normalize the current unqualified raw rows.** verified: `lshwres` reference pages for IBM
  Power8, Power9, Power10, and Power11 document different optional SR-IOV fields while retaining
  the resource families; absent fields therefore cannot safely mean absent resources.
- **Use location codes or partition names as identifiers.** verified: IBM's `chhwres` and
  `chsyscfg` command references select slots by DRC index and SR-IOV resources by adapter,
  physical-port, and logical-port IDs; names and locations are descriptive fields.
- **Represent SR-IOV capacity as an integer weight.** verified: the IBM Power10 and Power11
  `chhwres` references define capacity and maximum capacity as percentages with up to two decimal
  places and bind minimum values to physical-port granularity fields.
- **Treat unsupported commands as empty inventory.** judgment: capability absence and a real
  zero-resource result lead callers to different safe actions, so collapsing them prevents a
  falsifiable mutation precondition.
- **Implement mutation while establishing the contract.** judgment: issues #213–#216 own the
  safety policy and public behavior; performing mutation here would combine evidence collection
  with the operations it is meant to constrain.

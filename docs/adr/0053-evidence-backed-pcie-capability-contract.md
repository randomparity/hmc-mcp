# ADR 0053: Evidence-backed PCIe capability contract

## Status

Accepted on 2026-08-20 after the documentation-backed evidence records, strict parser/error tests,
state-matrix characterization, structural no-mutation check, and full `just verify` passed.

> **Partially superseded by [0056](0056-evidence-bounded-sriov-logical-port-assignment.md)**
> (2026-08-20) for captured POWER9/HMC V10R3 M1060 SR-IOV projections and mutation cells.

## Context

The repository exposes raw physical-slot rows and a profile-only slot append, while its SR-IOV
surface does not enumerate adapters, physical ports, or logical ports. Downstream inventory and
mutation work needs one falsifiable interpretation of HMC CLI identities, capacity units,
partition state, and unsupported capability behavior. IBM's Power8 through Power11 command
references expose related resource families through different commands and version-specific
fields, so treating an unlabelled sample or absent field as a stable contract would make version
drift indistinguishable from an empty resource. The documentation evidence compared on
2026-08-20 is the IBM
[Power8 `lssyscfg`](https://www.ibm.com/docs/en/power8/8284-22A?topic=commands-lssyscfg),
[Power8 `chsyscfg`](https://www.ibm.com/docs/en/power8/8284-22A?topic=commands-chsyscfg),
[Power9 `lshwres`](https://www.ibm.com/docs/en/power9/0000-REF?topic=POWER9_REF%2Fp9edm%2Flshwres.htm),
[Power10 `chhwres`](https://www.ibm.com/docs/en/power10/7063-CR1?topic=commands-chhwres),
and [Power11 `chhwres`](https://www.ibm.com/docs/en/power11/9824-42A?topic=commands-chhwres)
references. The evidence is documentation-backed; it is not a live-HMC capture. The admitted
claims are deliberately narrower than the union of those pages:

| Family | Reference section | Admitted evidence |
|---|---|---|
| Power8 docs | `lssyscfg` / `chsyscfg` partition/profile properties | Ethernet SR-IOV logical-port profile-property readback; `io_slots` and Ethernet SR-IOV profile mutation grammar; RoCE and live inventory unknown |
| Power9 docs | `lshwres` synopsis and filters | `adapter`, `physport`, and `logport`; adapter, physical-port, and logical-port ID selectors |
| Power10 docs | `chhwres` SR-IOV attributes | slot/adapter mode operations; logical-port IDs; `capacity`, `max_capacity`, and minimum granularity |
| Power11 docs | `chhwres` SR-IOV attributes | the Power10 contract plus current documented operation and unit confirmation |

The version-labelled repository evidence added by this change will retain the exact URL, command,
fields, a machine-checkable source locator made from command/option/attribute labels, and a
separately labelled editorial claim summary. Synthetic parser examples are labelled separately and
never presented as command output. No field is admitted merely because
another family documents it. The status becomes Accepted only after those artifacts and their
tests exist and pass.

## Decision

Repository fixtures are version-labelled evidence records: each names its record kind, IBM
documentation family, source URL, exact source locator, and editorial claim summary. Only a
`read-fixture` names a read command and selected fields and carries an explicitly synthetic parser
example. A `contract-evidence` record carries admitted claims and no parser data. Parsers consume only explicit
`-F` column order and retain empty columns. Stable identities are:

- dedicated slot: managed-system identity plus `drc_index`;
- SR-IOV adapter: managed-system identity plus the lossless CLI `adapter_id` value;
- physical port: adapter identity plus the lossless CLI `phys_port_id` value;
- logical port: adapter identity plus `logical_port_id`, the DRC index documented by IBM.

Names, location codes, MAC addresses, and partition names are attributes, not identities.
SR-IOV `capacity`, `max_capacity`, and physical-port minimum granularities are percentages with
up to two decimal places. They are never bytes, bandwidth, or whole-number weights.

Read commands may report capability unavailable distinctly from an empty result, but this change
admits no HMC error signature as proof of that outcome because the reviewed IBM references do not
document one. Successful zero-row output is an available empty collection. Every non-success —
including unknown or misspelled fields, malformed selections, and apparently unsupported
resources — remains an error unless a later version-labelled evidence fixture admits an exact
command, exit status, diagnostic, and classifier test. A downstream operation that requires an
unavailable capability must fail closed and must not mutate. Malformed successful rows are
contract errors. The contract records but does not implement mutation. Dynamic logical-port
operations use
`chhwres -r sriov --rsubtype logport`; profile/create-time logical ports use the documented
`sriov_eth_logical_ports` profile attribute; Power8 RoCE profile grammar remains unknown.
Dedicated-slot dynamic grammar uses `chhwres -r io` with `-l <slot-DRC-index>`, but mutation remains
capability-unavailable until one documentation family admits both that grammar and exact readback.
Dedicated-slot profile grammar is recorded, but profile mutation likewise remains
capability-unavailable until exact `io_slots` readback is admitted. A profile change never claims
to alter effective running state.

## Consequences

Downstream code can normalize inventories without guessing identifiers or units, and it can
fail closed when a required capability or selected field is unavailable. Version additions are
additive fixture cases rather than changes to identity. Mutation code must select only an
available path from the documented state matrix and verify the same identity fields after each
operation; it must not select a profile path whose readback remains unknown. This issue provides
no live-mutation proof and no public mutation API.

Every newly supported HMC family or newly admitted field adds a labelled evidence fixture and
contract-test maintenance obligation. That cost is deliberate: it makes compatibility changes
reviewable instead of allowing a downstream parser to widen the contract implicitly.

## Considered & rejected

- **Normalize the current unqualified raw rows.** verified: the versioned IBM references and
  sections in Context were compared on 2026-08-20. They place dedicated-slot profile evidence,
  inventory selectors, and SR-IOV mutation/unit evidence in different versioned command
  contracts; the repository evidence fixtures preserve each family-specific excerpt. A field
  admitted for one family therefore cannot safely be assumed present in another.
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
- **Do nothing and let each downstream issue infer its own contract.** judgment: #212–#216 would
  independently choose identities, capacity units, state behavior, and unsupported-capability
  semantics, making their public inventory and mutation behavior incompatible and unreviewable as
  one HMC contract.

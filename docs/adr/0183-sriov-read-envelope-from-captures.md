# ADR 0183: SR-IOV inventory reads admit each captured HMC level and machine type

## Status

Accepted on 2026-09-30. Supersedes [ADR 0056](0056-evidence-bounded-sriov-logical-port-assignment.md)
and [ADR 0113](0113-sriov-physical-port-level-selection.md) for two things only. The first is
the environment admitted for SR-IOV inventory reads. The second is physical-port level
selection. Their mutation cells, the mutation envelope, and every other decision stand.

## Context

ADR 0056 admitted one environment, an HMC at V10R3 M1060 managing a POWER9 8375-42A, for every
SR-IOV read and mutation. ADR 0113 kept that pair and read physical ports at `roce` and `ethc`,
accepting exactly one non-empty level.

The 2026-09-30 read-only sweep for #1202 adds three V11R2 SP1120 HMC captures (build
2607082225). Each records `lshmc -V`, `lssyscfg -r sys -F type_model`, and the default `lshwres
-r sriov` listings for adapters, physical ports at `eth`, `ethc` and `roce`, and logical ports:

| HMC level | Model | SR-IOV-mode adapters | Physical ports | Fixtures |
| --- | --- | --- | --- | --- |
| V10R3 M1060 | POWER9 8375-42A | adapter 1 | 2 at `roce` | `cli-lshmc-version`, `cli-sriov-adapters`, `tests/fixtures/sriov/*` |
| V11R2 SP1120 | POWER9 9009-42A | adapter 2 | 0-1 at `ethc`, 2-3 at `eth` | `cli-*-v11r2-p9*` |
| V11R2 SP1120 | POWER11 9824-42A | none (two dedicated, `adapter_id=null`) | none at any level | `cli-*-v11r2-p11-9824-42a` |
| V11R2 SP1120 | POWER11 9242-21B | none (one dedicated, `adapter_id=null`) | none at any level | `cli-*-v11r2-p11-9242-21b` |

Two findings follow. First, ADR 0113's "exactly one non-empty level" does not hold: the
9009-42A adapter lists ports at two levels, with disjoint port IDs. ADR 0113 never read `eth`,
so its survey could not see it. Second, no V11R2 capture holds any SR-IOV mutation, and neither
POWER11 system holds an SR-IOV-mode adapter. A physical- or logical-port read there has nothing
captured behind it.

The V11R2 captures are default-format listings. Each attribute the `-F` projections select
appears in them, except `min_eth_capacity_granularity`. V10R3's default listing omits that
attribute too, yet `-F` returns it. An `-F` projection naming an attribute the HMC does not have
exits 1 with "An invalid attribute was entered"
(`tests/fixtures/sriov/sriov-physport-granularity-v10r3.json`, `attribute_validation`). So an
unsupported projection fails closed; it is not read as empty.

## Decision

1. **Reads are admitted per (HMC level, model) pair and per read**, matched on `lshmc -V`'s
   own Version, Release and Service Pack fields exactly, as before:

   | Version.Release.Service Pack | Model | Admitted reads |
   | --- | --- | --- |
   | 10.3.1060 | 8375-42A | adapter, physical port, logical port |
   | 11.2.1120 | 9009-42A | adapter, physical port, logical port |
   | 11.2.1120 | 9824-42A | adapter |
   | 11.2.1120 | 9242-21B | adapter |

   Any other pair, and any read a pair does not list, reports `capability-unavailable` with a
   reason that names the admitted pairs for that read. `set-sriov-mode` only reads, so it uses
   the adapter read gate.
2. **Mutations keep the ADR 0056 envelope**, V10R3 M1060 on 8375-42A: logical-port assign and
   unassign, vNIC operations, and the ADR 0165 dedicated-slot gate. Declarative LPAR creation
   checks that envelope before it creates anything when SR-IOV or vNIC items are requested.
   The widened reads would otherwise let prevalidation pass and the assignment fail after the
   partition exists.
3. **Physical ports are read at `roce`, `ethc` and `eth`, and the rows are merged**, in that
   level order. Each row must name the requested adapter. A port ID listed at more than one
   level is refused as ambiguous. Every level empty remains an empty result, which physical-port
   inventory reports as unavailable, as ADR 0113 decided.

## Consequences

SR-IOV inventory answers on the three V11R2 systems the sweep captured, on the attribute names
those captures show. Physical-port reads cost three read-only commands instead of two. An
SR-IOV-mode adapter on a POWER11 system, a V11R2 8375-42A, or another service pack still reports
unavailable until a capture admits it. Mutations on V11R2 refuse before any command. The
`-F` projections on V11R2 rest on attribute names, not a byte capture; a missing attribute
fails the read with the HMC's invalid-attribute error.

## Considered & rejected

- **Admit all three reads on the POWER11 pairs.** verified: both captures hold only
  dedicated-mode adapters and list no physical or logical port at any level, so nothing shows
  those projections.
- **Widen mutations with the reads.** verified: no V11R2 capture holds an assignment,
  unassignment, vNIC change or mode transition, and ADR 0056's mutation cells are each tied to
  a captured V10R3 command.
- **Keep "exactly one non-empty level" and add `eth` as a third candidate.** verified: the
  9009-42A adapter returns rows at both `ethc` and `eth`, so that rule refuses a healthy adapter.
- **Read the default format instead of `-F` projections.** judgment: it would rewrite the
  admitted V10R3 path and lose `min_eth_capacity_granularity`, which assignment prevalidation
  uses and which no default listing prints.

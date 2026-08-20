# ADR 0054: Normalized PCIe inventory reports capability explicitly

## Status

Proposed on 2026-08-20. Acceptance requires focused contract tests and `just verify` to pass.

## Context

Issue #212 needs stable system-scoped dedicated PCIe and SR-IOV inventory schemas. ADR 0053 admits
an exact dedicated-slot read projection, but only selectors—not read projections—for SR-IOV
adapters, physical ports, and logical ports. An empty collection means a successful supported read;
it cannot also mean that no evidence-backed read exists.

## Decision

Expose four distinct presentation-neutral collection operations. Every operation returns a stable
result containing its resource kind, `available` or `capability-unavailable`, an item list, and an
optional unavailable reason. Only a successful exact evidence-admitted projection can return
`available`; command failures and malformed rows remain errors.

Dedicated-slot records use managed-system identity plus `drc_index`; optional description and owner
are explicit values or `None`, and availability remains explicit unknown rather than being inferred
from an empty owner. SR-IOV record schemas preserve the ADR 0053 identity hierarchy and
type all currently unadmitted attributes as unknown. Until a version-labelled fixture admits an
exact read projection, SR-IOV operations return capability unavailable without issuing a command.
Capacity values are decimal percentages or unknown, never bytes, bandwidth, or weights.

Issue #212 requires the stable schema to name mode, availability, ownership/use, location,
capacity, compatibility, and unknown categories. Their nullable schema slots are requirement-backed
categories, not read-field claims: every one remains unknown until same-family evidence admits its
projection. No closed mode or compatibility classifier is defined by this decision.

The normative schema table, closed literals, selectors, and invariants are in the linked design's
`Schema` section. They are part of this decision: adapters may not invent a second shape or widen
those vocabularies independently.

The legacy raw-slot API remains separate. New MCP, CLI, and Python entry points serialize the same
presentation-neutral records rather than redefining schemas at each adapter.

## Consequences

Agents can distinguish an available empty inventory from an unavailable capability and can persist
stable selector shapes without guessing HMC fields. Dedicated inventory becomes usable now; SR-IOV
inventory is honest but not populated until new evidence lands. Four operations add public surface,
but each has one resource kind and one independently testable schema.

## Considered & rejected

- **Return empty SR-IOV lists.** verified: ADR 0053 states successful zero-row output is available
  and every non-success remains an error; it admits no SR-IOV read projection. Empty would therefore
  claim evidence the accepted record does not contain.
- **Infer common SR-IOV fields from mutation documentation.** verified: ADR 0053 separates selector
  and mutation evidence from admitted read fields and forbids composing fields across families.
- **One heterogeneous PCIe tree.** judgment: callers need independent capability states and stable
  list schemas; a union tree makes one unavailable child family contaminate usable dedicated slots.
- **Replace `hmc_list_io_slots`.** judgment: issue #212 requests normalized entry points but does not
  authorize breaking the existing raw contract; a separate name makes the schema change explicit.
- **Do nothing until every family is readable.** judgment: it withholds the admitted dedicated-slot
  contract and gives downstream mutation work no stable fail-closed SR-IOV selector schema.

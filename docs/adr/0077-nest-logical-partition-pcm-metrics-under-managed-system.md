# ADR 0077: Nest logical-partition PCM metrics under managed systems

## Status

Accepted

## Context

The HMC documents processed and aggregated metrics for a logical partition below
its owning managed system. The current PCM client accepts one category and one
resource UUID, which can only express a flat path. Preferences and raw Long Term
Monitor feeds have no documented logical-partition endpoint.

## Decision

Represent a resolved PCM target as the resource UUID plus an optional owning
managed-system UUID. Processed and aggregated client methods use the owner to
build the nested logical-partition path. PCM tools accept an optional
`system_name_or_uuid` selector for logical-partition resolution. Preferences
and Long Term Monitor reject `LogicalPartition` before any HMC request.

## Consequences

Logical-partition names can be disambiguated and their metric paths match the
documented hierarchy. Callers supplying a logical-partition UUID must also
supply its owning system because ownership cannot be derived from that UUID.
Managed-system calls retain their existing paths and arguments.

## Considered & rejected

- **Discover ownership by scanning every managed system.** judgment: this adds
  fleet-wide I/O and ambiguity to a focused metrics request.
- **Keep the flat client signature and splice the owner into `resource_uuid`.**
  judgment: encoding two identifiers into one parameter obscures the contract
  and makes validation brittle.
- **Continue issuing flat logical-partition paths.** verified: issue #400 cites
  the Power10 and Power11 REST references captured 2026-08-22, which document
  only the nested processed and aggregated paths.

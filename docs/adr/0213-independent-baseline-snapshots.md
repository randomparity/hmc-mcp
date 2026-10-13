# ADR 0213: Independent baseline snapshots

## Status

Accepted (2026-10-13); campaign-approved read-only baseline scope for #1479.

## Context

Issue #1479 and epic #1506 require independent before/after evidence around a
live window. Per-arm checks witness only their own mutations. The operator
approved a minimum separate read set and conditional explicit VIOS access.

## Decision

Provide a standalone capture/compare script with a versioned private JSON
snapshot. Capture slot owners, partitions/states, profile io_slots and SR-IOV;
explicitly selected VIOS partitions add mappings and volume-group volumes.
Reuse existing read transports and parsers. Comparison validates completeness
before testing equality and ignores only table order and profile slot order.
The command creates no snapshot on a failed read and never overwrites one.

## Consequences

The window command can invoke capture twice and compare offline. Snapshot files
contain sensitive identifiers and remain local, mode 0600. Reads are sequential,
not an atomic remote transaction: the operator must quiesce unrelated changes.
No mutation or recovery operation belongs to this script.

## Considered & rejected

- **Document the read set only.** verified: #1506 requirement 8 explicitly requires
  a runnable script and comparison.
- **Reuse arm results as the baseline.** judgment: that would retain the same
  witness whose cleanup the independent baseline is meant to check.
- **Compare raw command text.** judgment: structural row comparison avoids ordering
  noise while keeping identity/state changes visible and malformed data unreadable.

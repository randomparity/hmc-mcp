# ADR 0078: Explicit RemoteRestart contract

## Status

Accepted

## Context

RemoteRestart currently borrows the Migrate parameter builder, omits the mandatory operation,
and cannot distinguish source and target managed-system identifiers. The HMC operation has five
materially different modes and conditional parameters.

## Decision

Expose the operation as a required literal through Python, MCP, and CLI. Build RemoteRestart XML
with its own parameter vocabulary. Require a source system selector, require a target selector
except for cleanup, preserve target UUIDs as `targetManagedSystemUUID`, resolve target names to
`targetManagedSystem`, and validate `usecurrdata` and `retaindev` by operation before submission.
Keep the existing `LpmResult` and `JobOutcome` result shapes, while including `detailedStatus` as
an error-detail fallback.

## Consequences

Callers must make the destructive operation and source system explicit. Cleanup can omit a target.
Invalid combinations fail locally without submitting a job. Existing normalized results remain
compatible.

## Considered & rejected

- **Default to restart.** judgment: a destructive default hides the mandatory five-way choice.
- **Continue using the Migrate builder.** verified: issue #401 documents that RemoteRestart uses
  lower-camel parameters absent from the Migrate vocabulary.
- **Always resolve targets to names.** verified: issue #401 documents a distinct
  `targetManagedSystemUUID` parameter, so UUID identity would otherwise be discarded.

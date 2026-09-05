# ADR 0106: Capture served Python warnings

## Status

Accepted

## Context

ADR 0043 bounds log writes made by served deployments, and its #534 amendment binds the
`hmc_mcp` namespace. Python's default `warnings.showwarning` still writes directly to stderr,
bypassing the queue, producer prefix, control escaping, and drop accounting. Warning capture is
process-global, so installing it during import or application composition would change library
consumers.

## Decision

The serve bootstrap enables the standard library logging bridge with
`logging.captureWarnings(True)` and binds `py.warnings` to ADR 0043's sink with its own producer
prefix. Installation is idempotent. Imports and `create_mcp` do not enable capture. A served
process owns capture for its process lifetime. Tests disable capture through
`logging.captureWarnings(False)` before restoring the suite's original callback, clearing
logging's internal capture sentinel as well.

## Consequences

Every Python warning emitted after serve bootstrap, including dependency warnings, enters the
bounded queue. Its text is escaped and producer-prefixed. Operator-installed handlers remain the
operator's responsibility. In-process callers of the private serve composer observe a global
warning-capture mutation, as they already observe global logger installation; test isolation must
restore it.

## Considered & rejected

- **Replace `warnings.showwarning` directly.** judgment: this duplicates a standard-library
  bridge and creates a package-owned global callback.
- **Convert existing package call sites to logger calls.** verified: issue #550 identifies both
  package and future dependency warnings as part of the served-process exposure; call-site edits
  cannot close that set.
- **Enable capture during import or `create_mcp`.** verified: ADR 0040 requires application
  composition not to mutate global logging state; those paths also serve library consumers.
- **Do nothing.** verified: Python's default `showwarning` writes to stderr and bypasses the sink,
  which is the live route demonstrated in issue #550.

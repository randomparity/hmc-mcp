# Served warning capture design

## Goal

In a served process, route Python warnings through ADR 0043's bounded stderr sink while
leaving imports, application composition, and other library use on Python's default warning
path. This implements issue #550.

## Decision

The serve bootstrap calls `logging.captureWarnings(True)` after installing the package and
third-party sinks. The `py.warnings` logger is bound to the shared sink with the producer prefix
`py.warnings: `. Repeated installs replace its handler rather than accumulating handlers.

`captureWarnings` is process-global. The server owns that global for the lifetime of the served
process; no import or `create_mcp` path enables it. Tests call `logging.captureWarnings(False)`
to clear logging's saved-callback sentinel, restore the suite's original
`warnings.showwarning`, and restore the `py.warnings` logger around every case. A sequential
regression proves a second served install captures again after that reset.

The warning message is formatted by `StreamSafeFormatter`, so embedded controls are escaped and
multi-line content retains a producer prefix. Submission uses the existing non-blocking queue and
drop accounting.

See [ADR 0106](../../adr/0106-capture-served-python-warnings.md).

## Alternatives

- Override `warnings.showwarning` with package code. This duplicates the standard library's
  logging bridge and creates a new global callback contract.
- Convert each package warning to a logger call. This misses dependency warnings and preserves a
  direct fd-2 route for future `warnings.warn` call sites.
- Capture at import. This would change embedding applications that never serve MCP.

## Verification

- A served warning reaches the shared sink with a `py.warnings:` prefix and escaped controls.
- A patched default `showwarning` is not called once serving installs capture.
- Two installs leave one handler on `py.warnings`.
- A library warning before serving still uses the ordinary `showwarning` path.
- The autouse logging isolation fixture restores warning and logger state after every test.

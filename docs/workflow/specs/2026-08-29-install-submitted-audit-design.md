# Detached install PID audit design

Issue #544 requires the served operator channel to receive the remote PID returned after
a detached `installios` submission. ADR 0109 selects a second structured audit event.

The operation emits `install-attempted` immediately before the submit and
`install-submitted` immediately after `run_installios` returns. The success event repeats
the stable target, host, path, and attribution fields and adds `pid`; a raised submit emits
no success event. Both use the reserved warning-level audit path, preserving delivery with
only `install_audit_sink` configured and at `--audit-level WARNING`.

Tests must prove the two-event success sequence, PID delivery with no root logging
configuration, attempted-only failure behavior, literal/emitter drift coverage, and public
documentation sample/field coverage. No install preflight, ownership, generic logging
routing, facade export, dependency, schema migration, or target-specific behavior changes.

Global constraints: Python 3.11–3.14; amd64 and arm64 CI targets; no new dependency; use
`just test` for the focused/full test gate and `just verify` before delivery. Base branch:
`main`; implementation branch: `feat/install-submitted-audit-544`.

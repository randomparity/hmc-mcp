# Live-test plan and state design

## Scope

Issue #700 separates operator-supplied live-test settings from discoveries and
cleanup state made by one invocation. The user approved a pre-release breaking
change: no `context` compatibility property or legacy result-file reader is
retained.

`LiveTestConfig` is a frozen dataclass containing every value parsed from
`LIVE_TEST_*`; `LiveTestArtifacts` is a mutable dataclass containing UUIDs,
baselines, created-resource flags, and saved boot order. `RunState` owns both
as `config` and `artifacts`. Scenario code reads configuration through
`state.config` and run discoveries through `state.artifacts`.

## Persisted contract

The runner writes exactly `{"config": ..., "artifacts": ..., "results": ...}`.
`_restore_artifacts_from_results()` accepts only objects whose saved `config`
equals `asdict(state.config)`, whose saved `connection` equals the current
non-secret `{"host", "port", "user", "verify_ssl"}` identity derived from
`HMCConfig`, and whose `artifacts` object decodes completely into declared
artifact fields. It constructs a temporary artifact value and replaces
`state.artifacts` only after that validation succeeds. Missing, legacy,
malformed, configuration-mismatched, or connection-mismatched documents abort a
selected run before scenario dispatch. A selected-subtask invocation still
restores from its prior results file when both identities match. Old
`{"context": ...}` documents
are rejected as unsupported pre-release output rather than silently mixing
configuration into mutable state.

## Failure handling and verification

Configuration parsing remains before MCP creation. A frozen config rejects
assignment, and restoration never writes into it. Tests prove configuration
immutability, artifact-only atomic restoration, rejection of legacy,
malformed, configuration-mismatched, and connection-mismatched files before
dispatch, and the emitted envelope. Existing scenario tests are updated to
exercise the new explicit ownership paths.

## Non-goals

This does not alter live-test inputs, connection configuration, result-row
format, or create a migration utility. It adds no dependency and does not run
the hardware suite locally.

## Decision record

ADR [0124](../../adr/0124-live-test-plan-state-ownership.md) records the
ownership boundary and rejected compatibility alternative.

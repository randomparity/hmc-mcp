# Live-test state ownership design

## Scope and authority

Issue #700 requires the live-test harness to separate validated operator configuration
from invocation-owned mutable discoveries. ADR 0124 is the accepted decision governing
that boundary. This change preserves live-test scenario behavior while replacing the
pre-release `LiveTestContext` surface.

## Architecture

`LiveTestConfig` is a frozen dataclass containing every value parsed from the
`LIVE_TEST_*` environment file. Its existing parsing, validation, and derived ISO
properties remain unchanged. It never contains discovered identifiers or mutable
collections.

`LiveTestArtifacts` is a mutable dataclass containing identifiers, snapshots, virtual
media state, and other values produced during one invocation. Each `RunState` owns one
config and one artifacts instance through explicit `config` and `artifacts` members.
There is no `context` compatibility alias.

Scenario modules read operator choices through `state.config` and read or mutate run
discoveries through `state.artifacts`. Local variable names follow that ownership rather
than retaining the ambiguous name `context`.

## Persistence and restoration

The result document stores `config` and `artifacts` as separate top-level members.
Restoration accepts only the new shape. Before changing live state, it validates that:

- `config` and `artifacts` are JSON objects;
- saved config exactly equals `dataclasses.asdict(state.config)`;
- saved non-secret HMC identity (`host`, `port`, `user`, and `verify_ssl`) equals the
  current connection identity; and
- every artifacts key is a declared `LiveTestArtifacts` field and its value can be
  assigned without partial restoration.

A malformed, stale, or mismatched document reports a restoration failure before scenario
dispatch and leaves `state.artifacts` unchanged. Existing mixed `context` result files are
unsupported, as ADR 0124 specifies.

## Error handling

Configuration parsing retains its existing actionable `ValueError` messages. Expected
result-file problems remain reported as restore failures; unexpected programming defects
continue to propagate. Validation is performed into a fresh `LiveTestArtifacts` instance,
which is installed only after the full document passes.

## Compatibility

This is an internal, pre-release live-test harness surface. Callers and tests migrate in
one change. Production MCP APIs, HMC operations, environment-file keys, and scenario order
do not change.

## Verification

- A focused test proves assigning to a `LiveTestConfig` field raises
  `dataclasses.FrozenInstanceError`.
- Existing environment-file tests prove parsing, validation, and derived ISO values.
- Focused restoration tests prove the separated document restores artifacts, rejects
  config or HMC identity mismatches, rejects legacy/malformed documents, and applies no
  partial mutation on failure.
- The live-runner suite proves all scenario call arguments and cleanup behavior remain
  intact after explicit member migration.
- `just verify` and `uv run --no-sync prek run --all-files` prove repository guardrails.

## Global constraints

- Python versions: 3.11, 3.12, 3.13, and 3.14.
- Architectures: amd64 and arm64; the current x86_64 host covers amd64 locally.
- Add no dependency and change no production package contract.
- Preserve every `LIVE_TEST_*` key and validation rule.
- Preserve scenario ordering, cleanup guarantees, redaction, and live operation behavior.
- Follow [ADR 0124](../../adr/0124-live-test-plan-state-ownership.md).


# ADR 0124: Live-test plan and state ownership

## Status

Accepted

## Context

`LiveTestContext` currently holds both `LIVE_TEST_*` operator input and values
discovered or mutated while a live test executes. It is also persisted as one
JSON object, so restoration must infer which fields are safe to overwrite.

## Decision

Use a frozen `LiveTestConfig` for parsed operator input and a mutable
`LiveTestArtifacts` for invocation-owned discoveries and recovery data.
`RunState` exposes them explicitly as `config` and `artifacts`, and persists
them in separate JSON members. This pre-release surface replaces `context`
without an alias or legacy-file compatibility reader.

## Consequences

Scenario code states whether each value is configured or discovered. Result
restoration has a bounded mutable target and cannot alter configuration. Prior
mixed result files are intentionally unsupported and must be regenerated. A
selected invocation restores artifacts only from a result document whose saved
config exactly equals its current config; malformed or mismatched documents
fail before scenario dispatch and cannot partially alter artifacts.

## Considered & rejected

- **Keep one mutable context and document field ownership.** verified:
  `scripts/live_test_runner.py` currently needs field-specific restoration for
  `lp3_baseline` and `vmedia_orig_boot_order`; prose cannot prevent a mutation.
- **Keep a deprecated `context` alias and read legacy files.** judgment:
  pre-release callers should move directly to the explicit boundary instead of
  carrying two ownership models and ambiguous restoration behavior.

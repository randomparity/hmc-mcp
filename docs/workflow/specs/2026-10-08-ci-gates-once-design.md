# Execute each CI gate once per native leg

## Problem and authority

Issue #1431, within epic #1429, owns duplicate execution inside a CI leg.
The approved scope is recorded in issue #1431's `WORK:SCOPE` q1431-728aa497.
ADR [0207](../../adr/0207-ci-gates-once.md) records the changed orchestration contract.

## Design

Keep focused recipes and the static-to-hook correspondence. Extract the existing
non-static tail into `verify-runtime`: test, smoke, build, artifact validation,
installed CLI help and CLI-group smoke. Compose `verify: static verify-runtime`.
CI runs the pinned real `prek run --all-files` first, then `just verify-runtime`,
then uploads the validated wheel. Remove the two separate documentation invocations
and the final redundant hook pass. Each documentation hook retains its name, failure
status and diagnostic output. Local contributors still run full verify and hooks.
No skip flags, changed scan scope, cached verdicts, new tools or concurrent execution.

## Global Constraints

Retain amd64 and arm64 native Ubuntu runners and Python 3.11, 3.12, 3.13, 3.14.
Retain the exact 90.5% branch-coverage gate, full tests, timeouts, interrupt handling,
security scans, build/artifact validation, CLI/MCP smoke and installed-wheel jobs.
Retain action/tool pins, read-only permissions, weekly schedule and artifact provenance.
No product API, live hardware, weaker coverage/security, target/version removal,
new hardware floor, baseline work (#1430), nested-test narrowing (#1432), fingerprint
optimization (#1433), parallelism (#1434) or cross-leg relocation (#1435).

## Execution map

| Gate set per native CI leg | Before | After |
| --- | --- | --- |
| lint, format-check, typecheck, secrets, workflow-security, env-vars, nicknames, test-layout, capability-inventory, adr-numbering, live-vocabulary, scenario-gap | verify/static + hooks: 2 | hooks: 1 |
| tool-docs-check, doc-freshness | named step + verify/static + hooks: 3 | individually identified hooks: 1 |
| full tests and exact coverage, MCP smoke, build, artifact validation, root/group CLI smoke | verify: 1 | verify-runtime: 1 |
| library-wheel-smoke, library-range-floors, wheel-smoke native matrix | existing jobs | unchanged jobs |

Counts cover orchestration invocations, not nested gate regression tests within pytest.
Standalone verify reaches both halves once. A failing half blocks its CI job and
wheel upload; downstream jobs retain `needs: ci`.

## Failure model

- Actors and deployments: local contributors and the eight declared native CI legs.
- Invariants and assets: gate completeness, meaningful failures, hook wiring,
  identifiable documentation diagnostics, validated artifacts and comparable timings.
- Accepted failure classes: hosted queue/cache/resource variance prevents causal
  claims from one elapsed sample; report it separately rather than promising a multiplier.
- Covered elsewhere: each leaf gate owns its validation semantics; #1432 owns nested
  regression scope; #1433 fingerprints; #1434 concurrency; #1435 cross-leg placement.

## Validation and measurement

Use real just and pinned prek in temporary Git fixtures, replacing costly leaf
commands with recording/failing boundary commands. For standalone verify and CI's
actual ordered gate commands, assert each intended leaf occurs once, and inject
nonzero exits for each static gate and runtime gate including CLI to prove refusal.
Retain structural checks for static/hook correspondence, full native matrices,
permissions, pins, schedule and artifact dependencies. Mutate missing hook/recipe
wiring to prove those checks bite. Real generated-doc stale-content failures through
focused recipes and real hooks must identify both guards and return nonzero; restore
all mutations. These controls do not substitute for final real verify/hooks and CI.
Use #1430's established baseline and the eventual green #1433 CI head for hosted
comparison. Report before/after actual workflow elapsed, summed job duration and queue
context separately, with exact commits, runner/version/cache and confounds. No repeated
full-suite benchmarking; focused repeated route samples only after the campaign slot.

# Cross-leg CI gate-placement evaluation

## Problem and scope

Resolve [#1435](https://github.com/randomparity/hmc-mcp/issues/1435) under
[#1429](https://github.com/randomparity/hmc-mcp/issues/1429) with the approved
NO-GO route in [ADR 0209](../../adr/0209-cross-leg-ci-gate-placement.md).
The deliverable is a contract review and bounded evidence report, not a trial workflow.
Only this specification, ADR 0209 and the measurement report change.
Sibling optimizations, administrative protection settings, historical ADR edits,
workflow/tests/code, generated docs, builds, wheel relocation, product APIs and live
HMC operations remain outside this change. No hardware or supported-target floor changes.

## Current gate and target map

At `d520a1ff2acae51e94493bda6be6b8dea8ddb6de`, `justfile`,
`.pre-commit-config.yaml` and `.github/workflows/ci.yml` define this map.

| Gate group | Current execution | Sensitivity / evaluation |
| --- | --- | --- |
| `secrets`, `format-check`, `workflow-security` | Three real hooks in each of eight producers | Tracked-text/workflow candidates for relocation; cross-platform equivalence has not been demonstrated by this evaluation |
| `lint`, `typecheck`, `env-vars`, `nicknames`, `test-layout`, `capability-inventory`, `tool-docs-check`, `adr-numbering`, `doc-freshness`, `live-vocabulary`, `scenario-gap` | Eleven real hooks in each producer | Retain native/interpreter context and generated-document guarantees; no independence claim |
| Tests, MCP smoke, build, artifact validation, root/group CLI load | `just verify-runtime` in each producer after hooks | Native amd64/arm64, Python 3.11–3.14; exact coverage and failure behavior retained |
| Installed-wheel smoke | Eight matching consumers, `needs: ci` | Consume each producer's retained validated wheel; no rebuild |
| Library wheel smoke and range floors | Separate jobs, `needs: ci` | Existing installation/dependency contracts retained |
| Python-support drift | Schedule-only job | Existing lifecycle/network contract retained |
| Verification report | Separate job; report on PR, enforce stale policy on schedule | Existing live-evidence reporting contract retained |

All eight native producers keep their displayed names and runner/version tuples.
The inactive ppc64le template remains inactive, as ADR 0021 requires.
ADR 0207 supersedes only the older hosted entry-point/repeated wiring requirements
in ADR 0002 and ADRs 0097/0098. It preserves the full per-leg gate set established by
ADR 0020, architecture policy and closed #163's adopted
[design](2026-08-15-python-architecture-verification-design.md).
No ownership transition, caller migration or obsolete path removal is adopted.

## Candidate and decision boundary

A bounded alternative would select the three candidate hook IDs in one job using
pinned prek's positive selectors, and select the remaining eleven in every producer.
The partition would need exact union/disjointness and real-hook negative controls;
no `SKIP` environment convention is necessary. Tests, generated docs, native builds
and installed-wheel consumers would remain across all eight arms.

A dependency guard could reject each non-success prerequisite result, including
failure, skip and cancellation, while retaining producer names and `needs: ci` wheel
lineage. That is a candidate design, not executed proof. GitHub documents that skipped
checks can satisfy required checks and recommends `always()` with `needs` for
[dependent required checks](https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/troubleshooting-required-status-checks).
A guard must actually evaluate the dependency result rather than merely run.

The observed repository has no active main protection or repository rulesets.
A YAML failure and a required GitHub merge check are separate responsibilities.
Consequently the literal dedicated-job merge-blocking criterion cannot be proven
within the approved administration exclusion. Retention is the issue's explicit
alternative; it neither establishes merge enforcement for today's workflow nor
claims the technical candidate cannot work. Reconsideration conditions are in ADR 0209.

## Failure model

- **Actors and deployments:** maintainers reading these three records; existing
  local verification and GitHub-hosted native PR/push/schedule workflows.
- **Invariants and assets at stake:** truthful contract and timing attribution;
  unchanged fourteen-hook coverage, runtime checks, native matrix and artifact lineage.
- **Accepted failure classes:** unavailable candidate latency/savings, because no
  relocation is adopted; historical timing variance is disclosed without extrapolation.
- **Covered elsewhere:** security/runtime execution remains under existing hooks,
  tests and ADR 0207; administrative merge enforcement remains maintainer-owned.

## Success and validation

C1: the map accounts for fourteen hooks, eight runtime producers and downstream jobs.
C2: the decision distinguishes workflow propagation from GitHub merge enforcement and
records actual API observations; failed reads must not become assertions of absence.
C3: ADR 0209 records retention, credible alternatives and an explicit reconsideration
condition without altering accepted placement or administrative state.
C4: the report identifies historical commits/environments and separates elapsed time,
runner time and queue delay; it states that candidate and post-#1434 timings were not measured.

For these human-readable conclusions, task-test-not-applicable: no executable consumer
parses the prose or decision, so prose snapshots would test wording rather than truth.
Check sources and the exact three-path diff; run ADR numbering, document freshness,
mandatory real hooks and normal hosted CI. The hosted run validates the retained workflow,
not the rejected candidate. No new tests or full local runtime suite for this prose change.

# ADR 0209: Retain cross-leg CI gate placement

## Status

Accepted — NO-GO for relocation evaluated in #1435 (2026-10-08).

## Context

Issue #1435 permits retaining the existing contract after review. ADR 0207
already removes within-leg repetition: each native producer executes the fourteen
real static hooks once, followed by `verify-runtime`. Its explicit guarantee that
no gate leaves the matrix is the operative placement contract. ADR 0002's recipe
ownership, ADR 0020's supported Python coverage and ADR 0021's native architecture
boundary remain applicable; closed #163 established complete native producers and
matching fresh-wheel consumers.

The issue requires proof that a dedicated job blocks merge, including skipped or
cancelled dependencies. Read-only GitHub observations on 2026-10-09 UTC reported
`protected: false`, no repository rulesets, and `Branch not protected` from the
main protection endpoint. [The evidence report](../workflow/cross-leg-ci-gate-placement-measurements.md)
binds these observations and historical timings to their actual sources.

## Decision

Retain the current placement. No supersession of ADR 0207 or the remaining
0002/0020/0021/0097/0098 guarantees is adopted. This record resolves the relocation
proposal through the issue's permitted NO-GO outcome, not a new full-verify entry point.

A dedicated hook job with explicit hook selection and an unconditional dependency
result guard is a credible technical candidate. It can make workflow failure
propagate; it does not configure GitHub-enforced required checks. Operator or
assistant merge discipline is distinct from that enforcement. With administration
maintainer-owned and outside this change, the literal merge-blocking proof is
unavailable. This is not a claim that cross-leg consolidation is impossible.

Reconsider after a maintainer chooses and enables an enforceable required-check
contract for the proposed job/dependency graph, or explicitly revises the acceptance
criterion. A subsequent relocation needs reviewed supersession, failed/skipped/
cancelled negative controls, real-hook partition proof and comparable hosted timings.

## Consequences

The fourteen hooks, runtime checks and wheel lineage stay where they are. Local
`just verify` stays complete; native amd64/arm64 × Python 3.11–3.14 coverage,
90.5% combined coverage floor with branch measurement, security scan scope, pins,
permissions and schedule stay intact.
Repeated work remains across legs. No speedup or protection improvement is claimed,
and historical measurements are not a relocation benchmark. No administrative change
or speculative follow-up issue is part of this decision.

## Considered & rejected

- **Move three hooks and rely on workflow failure alone.** verified: the protection,
  branch and ruleset API observations above provide no required-check configuration;
  workflow dependencies alone do not establish the issue's merge-blocking proof.
- **Enable branch protection in this PR.** judgment: administration belongs to the
  maintainer and exceeds the approved scope; no setting is inferred from this decision.
- **Treat multi-hook selection as unavailable.** verified: pinned prek 0.5.0
  `uv run --no-sync prek run --help` accepts `[HOOK|PROJECT]...`; lack of a selector
  is not a reason to reject relocation.

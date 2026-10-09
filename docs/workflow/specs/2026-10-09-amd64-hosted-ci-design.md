# amd64 hosted CI verification

## Problem and authority

[#1452](https://github.com/randomparity/hmc-mcp/issues/1452) explicitly revises
native coverage in epic #1429. The human approved its displayed scope and then
requested the quest workflow. Frozen scope: q1452-07c7469d.
[ADR0210](../../adr/0210-amd64-hosted-ci.md) supersedes the architecture portions
of ADRs0021/0207/0209 while retaining their other controls.

## Design and ownership

Delete the four ARM tuples from each existing `ci` and `wheel-smoke` include list.
Keep the explicit AMD tuples and their names; no owner/caller migration or new job.
The producer remains hooks -> verify-runtime -> exact wheel upload. Matching
consumers retain `needs: ci` and their exact artifact names. AMD/Python3.13 library
smokes/range floors and the independent verification report retain existing behavior.
The inactive ppc64le template stays byte-identical. Current guidance and independent
expected tuples follow the new matrix; historical decisions retain their bodies.
The earlier architecture design receives only a successor pointer.

## Global Constraints

Hosted native CI: amd64/ubuntu-24.04 with Python 3.11, 3.12, 3.13, 3.14.
Preserve canonical local verify, fourteen static hooks, exact 90.5% combined coverage
floor with branch measurement, runtime/build/installed-wheel checks and failure behavior.
Preserve action/tool/dependency pins, contents: read, disabled persisted checkout
credentials, schedules, timeouts, fail-fast: false and artifact provenance.

## Success

C1: exactly four producer and four corresponding consumer tuples; no active ARM job.
C2: existing per-leg gates, two library consumers and report behavior remain intact.
C3: guidance, accepted decisions and epic requirements disclose lost ARM evidence.
C4: read protection before deleting names; validate the real remaining matrix and
report workflow latency, summed jobs and queue context separately, without a causal
speedup claim from one uncontrolled comparison. Scope criteria source each promise.

## Failure model

- Actors/deployments: contributors and GitHub-hosted PR/push/schedule workflows.
- Invariants/assets: remaining Python coverage, gated wheels, truthful target/timing claims.
- Accepted classes: future ARM-only defects can escape hosted CI under the human-approved
  reduction; runner/cache/queue variance limits timing attribution, disclosed per sample.
- Covered elsewhere: branch-protection/required-check administration is maintainer-owned;
  existing leaves own their validation semantics; standalone quest owns this child.

## Threat model

- Boundary inventory: existing PR source -> ephemeral hosted runner -> GitHub artifact
  service -> exact matching fresh-wheel consumer; no new or widened boundary.
- Actors: PR authors control source/wheel; GitHub controls runners/artifacts; locked tools
  and pinned actions are trusted as before.
- Controls: existing read-only permissions, no persisted checkout credentials, timeouts,
  exact artifact selection, validated upload and installed-package checks stay intact.
- Out of scope: upstream service/supply-chain compromise is held by existing controls,
  not changed here; protection administration remains maintainer-owned.

## Validation

The single coupled implementation unit uses existing exact matrix tests red then green,
then controlled ARM reinsertion and missing-AMD controls. Existing pipeline tests cover
hooks/recipes, report and artifact callers together. Run final local verify and pinned
hooks and exact-head real CI; retain immutable sample identities and measurement limits.
Prose and epic policy have no executable consumer: inspect provenance and resulting text,
not wording snapshots. Rollback restores both matrices/expectations through git revert.

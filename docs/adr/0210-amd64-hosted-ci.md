# 0210: Restrict hosted native CI to amd64

## Status

Accepted (2026-10-09)

## Context

The maintainer approved #1452 after research found no ARM-caused project code/test
failure in 1,688 available CI runs from 2026-08-14 through 2026-10-09 14:59:50 UTC.
The review read 361 relevant failed-job logs, including earlier attempts/cancelled runs.
Four ARM-only service/network failures and one unexplained pre-test uv sync exit134
remain disclosed on the issue. This finite history does not predict future ARM safety.

## Decision

Use four explicit amd64/ubuntu-24.04 producers and matching installed-wheel consumers
for Python3.11–3.14. Remove the four ARM entries from each matrix. Hosted CI no longer
provides native arm64 evidence; this does not assert the package cannot run on ARM.

Supersede ADR0021's active-native-architecture boundary, ADR0207's two-architecture
placement promise and ADR0209's retained two-architecture coverage. Their other
contracts remain operative: the inactive, delimited ppc64le template and its pins,
VM isolation, privilege/credential restrictions and timeout; complete local verify;
fourteen real static hooks once per producer followed by verify-runtime; distinct
named documentation diagnostics; exact combined90.5% coverage with branch measurement;
installed-wheel/library checks; validated artifact lineage; read-only permissions,
pins and schedules. ADR0020's rolling Python policy remains accepted unchanged.
No cross-leg gate relocation is adopted. Producer failure still blocks dependent wheels.

Align epic#1429's requirements with this explicitly authorized coverage reduction.
Required-check administration stays maintainer-owned. Read-only observations on
2026-10-09 found main protected:false, protection404 Branch not protected and rulesets[].
No protection change or stronger merge-enforcement claim follows.

## Consequences

Eight hosted matrix jobs are removed; four Python versions retain AMD verification.
Future architecture-specific bugs can pass AMD-only CI. A real remaining matrix run
proves that matrix, not equivalence to ARM coverage. Compare elapsed workflow time,
summed job duration and queue context separately with exact commits/cache confounds;
no numerical gain is promised and historical measurements remain historical.

## Considered & rejected

- **Keep ARM legs.** judgment: contrary to the explicit approved reduction after the
  bounded history review; maintaining broader coverage remains the safer tradeoff.
- **Keep one ARM sentinel.** judgment: retains some ARM evidence but does not implement
  the approved removal of both four-entry ARM sets.
- **Replace ARM with emulation.** judgment: adds infrastructure and work absent from the
  requested deletion; retained ppc64le remains inactive.

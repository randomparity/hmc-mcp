# 0014 — Conservative template-deployment ownership inference

## Status

Accepted (2026-08-14)

## Context

ADR 0011 requires all three LPAR creation paths to stamp an ownership token, but
template-deployment jobs do not reliably return the created LPAR identity across HMC
firmware versions. Issue #135 calls for identifying it by comparing the target system's
LPAR list before and after a waited deployment. Another actor can create an LPAR during
the same interval, so a list difference can contain more than the deployed partition.

## Decision

When `hmc_deploy_partition_template` runs with `wait=True`, capture the target system's
LPAR UUIDs before submitting the deployment. After the job reports `COMPLETED`, list the
LPARs again and select entries whose UUID was absent from the baseline.

Attempt the ADR 0011 ownership stamp only when exactly one new UUID identifies an entry.
Zero or multiple candidates, missing identity data, and either list request failing leave
the successful deployment intact and return `ownership_stamped=None` with an actionable
warning. A failed stamp returns `False` with the existing best-effort warning. Non-waited
and non-completed jobs do not perform the post-deployment inference or stamp.

## Consequences

- Concurrent creation cannot cause this workflow to stamp an arbitrarily selected LPAR.
- A waited deployment adds two system-scoped LPAR list requests and one SSH call when the
  diff is unambiguous.
- REST observation and SSH stamp failures remain warnings rather than retroactively
  failing a deployment job.
- A concurrent create can make ownership stamping inconclusive even when the deployed
  LPAR is present; the caller must resolve that ambiguity manually.

## Considered & rejected

**Choose the first or newest new LPAR.** Feed order and timestamps are not a reliable
causal link to the deployment, so this can stamp another actor's partition.

**Match only by partition name.** The deploy response does not reliably supply the name,
and names can be changed. UUIDs are the stable identity already exposed by parsed feeds.

**Fail the deployment result when inference fails.** The remote create has already
succeeded, and ADR 0011 explicitly makes ownership stamping best-effort.

**Keep manual stamping for every template deployment.** This leaves the third supported
create path outside ADR 0011's machine-readable ownership guarantee when the identity is
unambiguous.

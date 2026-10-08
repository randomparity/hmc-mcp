# Bounded detach/RMC investigation (#1391)

## Authority and goal

Frozen scope q1391-63ce397b, issue #1391 annotation 6065626819; campaign
`d0a19b80-5de52adb-d394-4a54-80f9-4c635acf7b40`, same attempt and author.
One PR, investigation first; optional `--detach-probe` already approved.
Operator approved the remaining cycle (2026-10-08, call33c item1): TWO fresh
attach attempts already consumed; at most ONE remaining fresh cycle, cumulative
ceiling THREE. Revised sequencing needs complete design review/scope audit,
implementation checks and a NEW exact pushed-head root grant before hardware.
ADR 0136/product success policy stays unchanged; a concrete supported proposal
returns to the operator after capture before any product implementation.

## Problem and verified limits

Historical V10R3 HTTP500/REST0126/HSCL2957 detach responses do not establish
which endpoint lacked RMC or their exact before/after mapping identities.
At the first granted diagnostic head, cycle1 NA attach/detach passed; cycle2
OF ATTACH failed HTTP500/REST0126/HSCL7006 requiring Running, leaving a server-only
residue. No OF detach or HSCL2957 reproduction occurred. These FAILED observations
remain immutable. Identity-checked cleanup restored the actual original baseline;
that does not change the failed run's status. The remaining experiment attaches
while NA, then activates only its new owned client OF before one detach.
One observation cannot guarantee reproduction or distinguish intermittent causes.

## Global Constraints

Python >=3.11. No new dependency, setting, arm or subtask.
Use `just setup`; every `uv run` includes `--no-sync`.
No force push, pushed-history rewrite, default-branch edit or worker merge.
One live run at a time, granted by the orchestrator at the exact pushed head.
Preflight, named arm, recovery, commit-stamped filtered evidence are required.
Raw test-results JSON stays private; public text is fully redacted.
Maximum three fresh attach/detach cycles total; one scratch partition and
one run-owned 1 GiB logical volume; existing test partition unchanged.

## Components and data flow

- Replace the old three-cycle loop with ONE fixed remaining cycle3. Existing
  selector/runner dispatch/provenance and ST41 fixture prefixes remain installable;
  no budget setting, new CLI flag, arm, subtask or product interface is introduced.
  The orchestrator's durable two-attempt history and exact-head grant bound execution;
  the helper performs one attach and one detach maximum per admitted invocation.
- Existing ST41 admission and strict mapping/adapter readers keep their owner;
  shared ST41/product helpers, recovery machinery and client_storage.py are unchanged.
  Existing default arms, ownership authorization and If-Match remain protected.
- Capture the system baseline and prove scratch/LV names absent before creation.
  Create one ownership-stamped scratch and one 1024MiB LV. Before attach require
  current UUID/token/NA state, active VIOS RMC and exact original mapping/adapters.
- Attach ONCE while NA. Require accepted response, exactly one mapping naming the
  owned client UUID and LV, unchanged protected mappings, and exactly one reciprocal
  owned client/selected-VIOS adapter pair. Malformed/duplicate raw identities fail
  before set conversion; only the established empty sentinel means no adapters.
- Capture context after attach and before activation; require NA/active VIOS and
  current owner. Activate only this mapped owned scratch OF ONCE. Bounded READ polling
  may establish OF; failed activation remains failed and causes retention, even if
  later state converges. No repeated activation or new Running workload is allowed.
- Re-read the full exact mapped snapshot after activation; it must equal the accepted
  post-attach snapshot. Recheck owned UUID/token/OF, selected VIOS active RMC and
  reciprocal pair BEFORE detach. Identity/adapter/backing/context drift stops with
  zero detach and no cleanup writes; original test partition is never activated.
- Detach the SAME exact mapping ID ONCE. Capture pre-attach, pre-activation,
  post-activation/pre-detach and post-detach contexts, mapping/adapter snapshots,
  original response, available actual HTTP status and REST/HSCL codes. FAIL stays
  FAIL; no maturity observation or diagnostic readback promotes product success.
  Safe cleanup after a failed detach is allowed only if exact absence and preserved
  original mapping/adapter inventory are independently established; no retry occurs.
- Probe-local cleanup requires settled mapping/adapter baseline and current owner.
  Delete only the run LV ONCE; require accepted response and exact volume baseline.
  Power off only the owned OF client ONCE; require accepted response, NA readback and
  current ownership. Recheck settled mappings before deleting the owned partition
  ONCE; require accepted response and confirmed disappearance. Shared permissive
  teardown helpers are not called. Cleanup failure/unknown effect stops, preserving
  its failed row and whatever assets remain, without subsequent cleanup writes.
- Uncertainty/interruption records retained identities and uses read-only recovery;
  no generic teardown retries, automatic compensation or broad residue cleanup.
  Final system baseline must equal its original snapshot; intermediate pool resource
  reservations are not normalized into restoration. Recovery runs after the attempt.

## Success and validation

Focused production-RunState/fake-boundary tests prove exactly NA attach→OF
activation→OF detach, one attach/power-on/detach, protected baseline guards before
both activation and detach, exact mapping identity across activation, and retained
assets/zero detach on rejected activation or changed identity/context/inventory.
Existing parser/mode/default/provenance/recovery tests remain; failed-response
HTTP/code/scalar capture is unchanged. Probe-local cleanup tests prove each write
once and no subsequent destructive dispatch after refusal/unknown/interruption,
including effect-taking failures. Controlled guard removal must fail the matching
zero-detach/retention case. No prose snapshot tests are introduced.
Full `just verify` and pinned all-files prek are required before push, followed by
root-allocated branch/security review and a new exact-head live grant. Native proof
reports the one remaining observation, original failures, cleanup/recovery limits
and supported/non-supported hypotheses; never asserts a cause from HSCL7006 alone.

## Failure model

- Actors/deployments: trusted operator/orchestrator; serial admitted lab runner;
  offline CI replaces external tool boundaries.
- Assets/invariants: original partitions/storage/mappings/adapters preserved;
  one new owned fixture/LV; one remaining attempt under three cumulative total;
  immutable failed responses; no ambiguous-write retry or failure promotion.
- Accepted: intermittent cause may remain unresolved; concurrent lab drift stops
  rather than repairs; interrupted/uncertain fixtures may need manual recovery.
  Process persistence outside the grant protocol is not promised by this helper.
- Covered elsewhere: orphan authorization #1387; backing deletion #1229;
  ownership/power ADR0092; generic reconciliation ADR0136; provision/PCIe #1390.

## Threat model

- Boundaries: existing local selector, HMC/CLI inventories, scoped power/storage
  dispatch and private evidence; no added network service or widened auth scope.
- Actors: trusted operator; malformed/changing HMC responses and other lab writers.
- Controls: existing early selection rejection/UUID-token authorization/If-Match/
  quoting/power admission; strict identities, exact baselines and one-shot sequencing;
  failure retention; private raw JSON and filtered/redacted public evidence.
- Out of scope: compromised HMC or malicious local operator; credential/TLS policy
  unchanged. Concurrent changes stop without automatic repair or isolation guarantees.

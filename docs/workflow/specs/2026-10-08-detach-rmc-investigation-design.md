# Bounded detach/RMC investigation (#1391)

## Authority and goal

Frozen scope q1391-63ce397b, issue #1391 annotation 6065626819; campaign
`d0a19b80-5de52adb-d394-4a54-80f9-4c635acf7b40`.
Operator 2026-10-08 chose one PR, investigation first, and approved the bounded
`--detach-probe` interface. This phase prepares diagnostic capture. A concrete
ADR 0136/product policy proposal returns to the operator after live capture,
before a separately reviewed design/build phase changes that policy.

## Problem and hypotheses

Historical V10R3 HTTP500/REST0126/HSCL2957 responses and subsequent active VIOS
RMC reads do not establish the cause or exact mapping snapshots. HSCL2957's
“the partition” wording may describe the scratch client rather than VIOS.
Compare Not Activated/Open Firmware on one scratch client: client DLPAR/RMC
capability predicts context-dependent failure with absent mapping; VIOS RMC
transition predicts endpoint state change; a grouped-write platform issue may
be context-independent. Three cycles cannot guarantee reproduction or causality.

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

- Optional flag on existing lpar-power wrapper/runner/preflight; reject mode/group
  mismatch before access. Default arms remain unchanged. ST41 probe selection
  records `detach_probe: true` provenance and plain diagnostic rows, without
  maturity observations promoting a failed product response.
- Preflight names only one ST41-prefix scratch fixture and one 1 GiB volume,
  three-cycle cap and inactive/OF comparison. No broad power/provision/PCIe path.
- Focused helper reuses ST41 admission, scratch UUID/token, create/read/power/
  delete and system baseline helpers. No ownership transition or facade needed.
- Strict full mapping/adapter snapshots precede writes: reject malformed or
  duplicate identities, decode typed rows, preserve exact ID/client/kind/backing
  and protected other rows. Confirm scratch/LV names absent before creation.
- Create owned fixture/LV; cycle1 in Not Activated, activate only this fixture
  to OF, then cycles2–3 in OF. Before fresh attachment verify prior absence,
  mapping/adapter baseline, current UUID/token, client state and active VIOS RMC.
- After attach and before detach require preserved VIOS/client adapter baselines
  plus exactly one reciprocal scratch/selected-VIOS slot pair; any drift retains
  fixtures without detach. Validate bounded raw three-field adapter rows and unique
  local slots before sets; only the established no-results sentinel denotes empty.
- Around detach record exact mappings/adapters and both endpoint RMC/state,
  original response, actual HTTP status and REST/HSCL message. FAIL remains FAIL.
- Next cycle requires exact absence and preserved protected inventory. Failed
  attach, surviving/unknown mapping, identity drift or unreadable diagnosis stops;
  no ambiguous write retry, including from existing generic teardown helpers.
- Normal completion rechecks safe inventory, deletes only run LV and owned
  fixture, verifies disappearance and compares system baseline. Uncertainty or
  interruption retains assets for manual recovery using existing ST41 prefixes.

## Success and validation

Focused tests prove mode forwarding/rejection/dispatch/provenance and matching
preflight scope; strict typed snapshots; three-cycle cap/context order; actual
HTTP/code capture without promotion; ownership/other-inventory guards; stop,
no retry and retention on uncertain outcomes; safe cleanup and prefix recovery.
A controlled guard removal must fail the destructive-dispatch regression.
Run full `just verify` and pinned prek before pushing. Exact-head live proof
reports reproductions, non-reproductions, before/after comparisons and cleanup/
recovery limits; no cause or policy is preselected. Product contract remains
ADR 0136 diagnostic-only until a later explicit operator decision.

## Failure model

- Actors/deployments: trusted operator/orchestrator, serial admitted lab runner;
  offline CI replaces external tool boundaries.
- Assets/invariants: original storage/mappings/adapters/partitions preserved;
  one owned fixture/LV and three fresh cycles; original failures retained;
  no ambiguous-write retry.
- Accepted: intermittent cause may remain unresolved; concurrent lab drift stops
  rather than repairs; interrupted/uncertain fixtures may need manual recovery.
- Covered elsewhere: orphan authorization #1387; backing deletion #1229;
  ownership/power ADR0092; generic reconciliation ADR0136; provision/PCIe #1390.

## Threat model

- Boundaries: optional local selector, HMC/CLI inventories, scoped power/storage
  calls and private evidence; no new network service.
- Actors: trusted operator, malformed/changing HMC responses and other lab writers.
- Controls: early parser rejection; existing UUID/token authorization, If-Match,
  power admission and quoting; strict identities/baselines, cycle cap, fail-closed
  retention; private JSON and filtered/redacted public evidence.
- Out of scope: compromised HMC/malicious local operator; credential/TLS policy
  unchanged. Concurrent changes stop without automatic repair.
